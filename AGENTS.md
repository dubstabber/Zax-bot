# AGENTS.md - Zax bot-support binary patch

This project adds host-side bot support to *Zax: The Alien Hunter*
(Reflexive Entertainment, 2001) by runtime-patching `Zax.exe`. The game is a
fullscreen-DirectDraw, single-thread C++ program with working DirectPlay
multiplayer but no built-in bots.

## Read first

The detailed notes live in `docs/`:
- `docs/README.md` - current status and file map.
- `docs/01-binary-and-patching.md` - PE layout, `.zaxbot`, patch manifest.
- `docs/02-keyboard-and-message-pump.md` - WM_KEYDOWN hook and main-thread rules.
- `docs/03-multiplayer-and-display.md` - manager/session/participant anchors.
- `docs/04-spawn-ai-leads.md` - Phase B spawn flow and remaining AI work.

## Files

- `Zax.exe` - patch target; rebuilt from `Zax.exe.bak` on every patch run.
- `Zax.exe.bak` - pristine original; never modify it.
- `Zax.exe.i64` - IDA database for the original image only; `.zaxbot` is not in it.
- `zax_patch.py` - thin entrypoint that rebuilds and writes the patched image.
- `zaxbot/` - actual patch implementation:
  - `addresses.py` - verified original-image VAs and prologue bytes.
  - `config.py` - section size, scratch policy, synthetic ids, bot names.
  - `patch_manifest.py` - enabled redirects into `.zaxbot`.
  - `hook/` - dispatcher, mode detection, spawn, snapshot.
  - `detours/` - capture, safety, controller, fire/aim detours.
- `zax_dump.bin` - appendable tagged runtime snapshots written by R.
- `tools/diffdump.py` - parser/comparator for `zax_dump.bin`.
- `zax_step.log` - one-byte spawn progress markers (`A/M/2/B/C/D/S/T/P/E/V/N/F/W`).

## Build and test

```bash
python3 zax_patch.py
```

Then the user runs `Zax.exe` under Wine and reports runtime behavior. Do not
launch the game from automation.

## Current state

Working path: **Phase B - synthetic DirectPlay queue injection**.

- WM_KEYDOWN hook at `0x599A1A` redirects `call sub_599580` to
  `.zaxbot:hook_entry`, then tail-jumps back to `sub_599580`.
- `.zaxbot`: VA `0x71A000`, raw `0x231000`, size `0x4000`, RWX.
- Scratch starts at `0x71C000` (`SCRATCH_OFF = 0x2000`).
- B opens the bot menu via `sub_59B260`; R writes a runtime snapshot.
- Digit selection calls `do_spawn_with_team`.
- Spawn injects a synthetic DirectPlay "player added" queue entry at
  `dpmgr + 0x44D`, calls `sub_480800(ecx=dpmgr, edi=host_char)`, reads the
  participant from `[queue_slot + 7]`, clears the queue entry, then calls
  `sub_59DF90(mgr, a2, botidx, 0, 0)` to create and place the character.
- Bots are real remote-classified participants: visible character, scoreboard
  entry, damage/death, kill registration, and PC2 visibility work.
- Bot display names are set on host through `sub_4E1930(*(part+0x1C), name)`.
  PC2 does not reliably see the chosen name because the synthetic DirectPlay
  player-data store is not populated.
- Each bot name owns a deterministic `(color1, color2)` pair from
  `BOT_COLORS` in `zaxbot/config.py`. Coloring is split across two phases:
  - **Pre-spawn** (before `sub_59DF90`): the patch writes the chosen
    `(color1, color2)` into the bot's pcfg at `*(stats+0x1C)+4/+8`. SK
    paints each player's collector during character creation by reading
    `pcfg.color1` off the bound participant; the colors must be in place
    before `sub_59DF90` or the collector stays the default yellow until
    the bot dies and respawns (the respawn path re-reads pcfg).
  - **Post-spawn** (after `sub_59DF90`): the patch mirrors the engine's
    own `sub_5ABE80` (server-side handler for "client options changed").
    Walk the bot char's first child via `sub_4FC7C0`/`sub_4FC7D0`, look up
    the appearance via `sub_418790(dword_6C0520, child)`, and write the
    chosen colors as floats at `+0x0C` and `+0x18`. Then invoke the active
    gametype's vtable[+0x9C] (`sub_4698B0` in CTF, `nullsub_3` in DM/SK)
    to let CTF replace `color1` with the team hue.

  Both writes share the same `picked_name_idx`; the post-spawn name write
  reuses it instead of re-rolling RNG so the collector hue (from pcfg) and
  the character sprite hue (from appearance) always match.
- Up to map `MaxPlayers` bots are supported, bounded by 16 synthetic ids
  (`0xBADC0DE0..0xBADC0DEF`).
- `mgr + 0x290` is pre-grown to 16 entries before bot `sub_59DF90` calls.
- `detour_df90` clears bot scratch arrays on match change when captured `a2`
  changes.
- Mode detection calls the engine's `sub_59FF90(ecx=mgr)` getter to get the
  active `CMultiPlayerGameType` instance and compares `[result+0]` against
  the three known game-type vtables — 0 (DM), 1 (CTF), or 2 (SK). Bot team
  id (`stats + 0x14`) is mode-specific:
  - **CTF** (1): user-chosen team verbatim (`0`=Blue, `1`=Red).
  - **SK** (2): `botidx` — unique per bot AND inside `[0, MaxPlayers)`, the
    valid range for per-player collector ownership. `slot + 0x10` falls
    outside that range, which makes the engine fall back to a single shared
    collector for every bot (observed as "one bot has a collector, the
    rest are red" with 12 bots). Bots still all have different team ids
    so `sub_51D400` doesn't mis-label cross-bot kills as TEAMMATE.
  - **DM** (0): `slot + 0x10` (16..31) — unique per bot and above the real-
    player team range (host=0, PC2=1, …) so `sub_51D400` never mis-labels a
    bot kill as TEAMMATE. DM has no per-player collector, so the out-of-
    range id doesn't bite anything.

  Unknown vtables drop a one-shot 0x200-byte dump of the game-type object
  and fall back to DM. `zaxbot/config.py` exposes a `FORCE_MODE` knob for
  offline testing.
- DM bots wander via `detour_542360` synthesizing a movement vector from
  three contributions: a random wander target (re-rolled on timer or stuck
  detection) plus a hazard repulse and a pickup attractor. The hazard cache
  is rebuilt once per match by `scan_hazards` (called from `detour_df90`),
  walking the world entity array at `mgr+0x2BC..0x2C0` and filtering by
  `CDamageExpandingRadiusAI`. The attractor uses the same iteration to find
  the closest `CPickupAI` within range, then LOS-tests via `sub_491380`.
  Engine `sub_4303F0` handles wall collision and pickup-on-walkover for
  free. `cfg.MOVEMENT_ENABLED = False` reverts to the original zero-vector
  behavior. `detour_5436F0` still synthesizes aim/fire when range + LOS
  allow.
- Lava and other tile hazards are handled REACTIVELY: when a bot's
  `[char+0x7C]` (cur_damage) increases between frames, the wander target
  is biased to the opposite of the bot's recent motion direction and
  committed for `cfg.HAZARD_FLEE_FRAMES` (default 120). Tile-grid
  reverse-engineering attempts didn't yield a reliable signal — the
  `CPlasmaTileMap` plane-0 dwords aren't direct `CGroundTextureFrame*`
  pointers (they're packed values resolved via a separate texture list).
  Proper navigation around tile hazards is deferred to the planned
  waypoint system, which will route bots over the level designer's
  intended paths. The reactive flee remains the backstop for any hazard
  the waypoints don't cover.
- Shot prediction is fully wired. `compute_proj_speed` reads the active
  weapon's projectile speed from `[CModel + 0x60]` via
  `sub_48D8F0(dword_6CFDD8, [def + 0x20])`; NULL projectile key or zero
  velocity ⇒ `is_hitscan` (Semi Auto Pistol, Alien Electrical Weapon).
  `apply_lead` solves the exact intercept quadratic with muzzle-offset
  compensation (`cfg.MUZZLE_OFFSET = 20px`); `bot_fire_aim` rolls
  `cfg.LEAD_PROBABILITY` (default 0.5) per shot to mix prediction with
  straight-shooting for a less robotic feel.
- `zaxbot/config.py` can force newly spawned bots to equip an inventory item
  by name (`FORCE_BOT_ITEM_NAME`) for lead-shot testing. The force path
  resolves the engine item definition by name, creates a transient pickup
  item for the new bot, then switches the bot's Primary slot to the
  bot-local item index.

## Enabled detours

Source of truth: `zaxbot/patch_manifest.py`.

Current patched sites:
- `0x599A1A` - WM_KEYDOWN hook.
- `0x480BD0` - capture DirectPlay manager.
- `0x59DF90` - capture `a2`; clear bot state on match change.
- `0x5AA4E0` - skip bot camera tracker while spawning.
- `0x4FBC50` - NULL-tolerant component attach.
- `0x542360` - bot movement-vector override.
- `0x5436F0` - bot fire/aim override.
- `0x542550` - walking-controller capture/scrub.
- `0x480889` - synthetic-id name-block skip in `sub_480800`.
- `0x4F5204` - character iterator NULL-skip.

Older emitted labels or disabled detours are not active unless they appear in
`patch_manifest.py`.

## Anchor addresses

| symbol | meaning |
|---|---|
| `dword_713F14` | game/world manager pointer |
| `mgr->vtbl[0x184]` | active level getter |
| `level + 0x30` | live `CMultiPlayerGameData*`, NULL outside MP |
| `dword_6C2080` | world/entity manager |
| `dword_713F18` | session/participant container |
| `sub_59B260` | `__stdcall on_screen_msg(text, type)`, `type=-1` broadcast |
| `sub_480BD0` | DirectPlay poll; `ecx = dpmgr` |
| `sub_480800` | DirectPlay player add/remove handler |
| `sub_59DF90` | per-player character create/place |
| `sub_5BA790` | participant factory |
| `sub_5BA820` | stats helper used before writing `stats + 0x14` |
| `sub_59FF90` | `__usercall(this=ecx, hint=esi)` -> active game-type instance |
| `sub_4E1930` | `CString::operator=(this, char*)` |
| `sub_4F1050` | active char getter / `a2` fallback |
| `def + 0x20` | CInventoryItemDefinition "Projectiles/Projectile" - integer registry key (not a pointer); resolve with `sub_48D8F0(dword_6CFDD8, key)` → `CModel*` (key 0 ⇒ hitscan weapon) |
| `proto + 0x60` | CModel "Move/Max Velocity" - float pixels/sec (schema range ~300..4000); scaled by `cfg.SPEED_SCALE` for per-tick lead math |
| `dword_6CFDD8` | CModel registry, passed as `this` to `sub_48D8F0` to resolve "Projectiles/Projectile" and similar `sub_54E560` reference fields |
| `sub_48D8F0` | `__thiscall(registry, key) -> object*`; registry-key resolver used for both `dword_6C0C08` item-defs and `dword_6CFDD8` CModel lookups |
| `sub_55C4E0` | `__thiscall(rng, low, high) -> int`; engine PRNG, used by `bot_fire_aim` for the `LEAD_PROBABILITY` coin-flip and by `name_block` for bot-name picks |
| `dword_7124C0` | engine RNG instance (passed as `this` to `sub_55C4E0`) |
| `sub_40F5F0` | engine main loop; calls the per-frame tick with `dt = 1/60` exactly (confirms `cfg.SPEED_SCALE = 1.0/60.0`) |
| `sub_418790` | `__thiscall(class, char)` -> appearance component (color1@+0xC, color2@+0x18, floats); query the **child** entity, not the player char |
| `sub_4FC7C0` | `__thiscall(char)` -> child-list count |
| `sub_4FC7D0` | `__thiscall(char, idx)` -> child entity |
| `sub_5ABE80` | server handler for `CClientOptionsToServer` — canonical color-apply path |
| `dword_6C0520` | class descriptor for the "player look" appearance component |
| `0x5D034A` | `operator new` |
| `0x5D0330` | `operator delete` |
| `VT_DM_VA = 0x5F0D54` | Deathmatch game-type vtable |
| `VT_CTF_VA = 0x5EF544` | Capture-the-Flag game-type vtable |
| `VT_SK_VA = 0x5FED48` | Salvage King game-type vtable |
| `stats + 0x14` | team id |

## Constraints

1. Single main thread drives DirectDraw. Do not add blocking/noisy file I/O to
   key or frame hot paths. R snapshots and one-shot mode dumps are explicit
   diagnostics; normal feedback uses `sub_59B260`.
2. Hand-encoded ModR/M bytes are high risk. The old `FF 51 08` vs `FF 52 08`
   mistake jumped to EIP=1. Prefer emitter helpers and verify bytes.
3. Range checks do not make arbitrary dereferences safe. The `0x6E6F6E00`
   mode-scan crash is the canonical failure. Dereference only known-safe
   offsets or add SEH/`IsBadReadPtr`.
4. Never modify `Zax.exe.bak`.
5. The IDB does not contain `.zaxbot`; inspect built hook bytes from
   `Zax.exe` at raw offset `0x231000`.

## Open work

- Replace the random-target wander with engine-waypoint navigation.
  `CWayPointMap` / `CWayPointPath` are shipped engine constructs that
  monsters patrol; reading the live waypoint graph would let bots route
  around walls instead of bumping and re-rolling.
- CTF/SK objective behavior on top of the DM wander primitive: CTF bots
  need flag-aware target selection, SK bots need collector-aware return
  paths.
- Populate or hook DirectPlay player data so PC2 sees chosen bot names
  (and team colors in CTF/SK).
