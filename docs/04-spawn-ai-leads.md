# 04 - Spawn and AI state

This file is the current architecture note for bot spawning. Older clone,
local-participant, and monster-AI experiments are summarized only where they
still affect decisions.

## Current spawn path: Phase B

The working spawn path is not a hand-built participant and not a clone. It
drives the engine's own DirectPlay join handler with a synthetic queue entry.

High-level flow in `zaxbot/hook/spawn.py`:

1. Re-check the MP gate.
2. Require captured `cap_dpmgr` from `sub_480BD0` and captured `cap_a2` from
   `sub_59DF90`.
3. Read the hosted session's advertised `MaxPlayers` from
   `[[dpmgr + 0x08] + 0x0C]`; clamp to the 16 synthetic bot slots.
4. Find a free bot scratch slot.
5. Enter `0x6BDBF0` DirectPlay critical section.
6. Publish a synthetic "player added" queue entry at `dpmgr + 0x44D`.
7. Set `dpmgr + 0x38`, `+0x39`, and `+0x8FC` so `sub_480800` consumes the entry.
8. Call `sub_480800` synchronously with `ecx = dpmgr` and `edi = host_char`.
9. Read the new participant pointer from `[queue_slot + 7]`, store bot
   participant/index scratch entries, and clear the queue entry.
10. Leave the critical section.
11. Write team id through the stats object returned by `sub_5BA820`.
12. Pre-grow `mgr + 0x290` to 16 if capacity is lower.
13. Set `botmode = 1`, call `sub_59DF90(mgr, a2, botidx, 0, 0)`, clear
   `botmode`.
14. Bump `mgr + 0x294` if needed.
15. Assign a random ASCII bot name with `CString::operator=` (`sub_4E1930`) on
   `*(participant + 0x1C)`.
16. Cache the new character pointer in `bot_chars[slot]`.

The synthetic DirectPlay id range is `0xBADC0DE0..0xBADC0DEF`.

## Why `edi = host_char` matters

`sub_480800` uses its incoming `edi` as part of a stack context for name-block
initialization. Calling it with `edi = 0` crashed. The working value is the host
character pointer: `*(*(worldmgr + 0x290) + 0)`.

`detour_name_block_skip` skips the unsafe DirectPlay name block for synthetic
ids at `0x480889`. Real player joins still run the original code.

## `sub_59DF90`

`sub_59DF90(this=mgr, a2, index, name, a5)` creates and places the player's
character:

- creates the `CEntityCharacter`;
- stores it into `mgr->charArray[index]`;
- attaches player control components;
- names/places it through `sub_4F4950` and spawn-point logic.

`detour_df90` captures the per-match `a2`. If incoming `a2` changes, it treats
that as a new match and clears the 64 dwords covering:

```text
bot_participants, bot_indices, bot_chars, bot_controllers
```

This prevents stale controller pointers from matching host objects in later
matches.

## Multi-bot and capacity fixes

Multiple bots are supported up to the advertised map `MaxPlayers`, bounded by
the 16 synthetic ids.

Important fixes currently in the patch:
- Re-check participant count inside the DP critical section before publishing.
- Clear the synthetic queue entry after synchronous `sub_480800`.
- Pre-grow `mgr + 0x290` to 16 entries before character creation.
- Keep `mgr + 0x294` at least `botidx + 1`.
- Null-skip the character iterator at `0x4F5204`.

These fixes avoid the old duplicate-participant, OOB char-array, and garbage
slot crashes.

## Controller strategy

The first stable passive bot skipped the human walking controller, which stopped
input mirroring and camera steal but removed idle animation. The current patch
keeps the controller and captures it:

- `detour_5AA4E0` skips the camera tracker while `botmode == 1`.
- `detour_542550` captures the bot's `CPlayerWalkingControlAI` by active slot
  during spawn and by player index after natural respawn.
- `detour_542360` synthesizes a movement vector for captured bot controllers
  (wander + hazard repulse + item attractor — see "Bot movement" below).
  Falls back to a zero vector when `MOVEMENT_ENABLED == 0`.
- `detour_5436F0` synthesizes aim/fire for captured bot controllers when the
  host is within `FIRE_RANGE_SQ` and `sub_491380` reports line of sight.

Net result (DM): bots wander the map, drift toward visible pickups within
range, steer away from cached damage zones, and shoot at the host when in
sight. CTF/SK bots still wander but don't pursue objectives.

## Bot movement (DM)

The movement detour at `0x542360` synthesizes a 2D velocity by accumulating
three contributions and writing the normalized direction (scaled by
`cfg.BOT_MOVE_SPEED`) along with `atan2(dy, dx)` via `sub_509100` to the
engine's per-call output pointers.

1. **Random-target wander.** Each bot tracks a `(wander_x, wander_y)` and a
   tick counter (`bot_wander_ticks[slot]`). When the counter expires or
   stuck-detection trips, the detour rolls a new target via
   `sub_55C4E0(RNG, -R, +R)` on each axis (same RNG the lead coin-flip uses),
   added to the bot's current position. `cfg.WANDER_TARGET_RADIUS` sets R.
2. **Hazard repulse.** `scan_hazards` (in `zaxbot/detours/world_scan.py`)
   walks the world entity array at `mgr+0x2BC..0x2C0` once per match (called
   from `detour_df90` after the state wipe) and caches every entity of
   class `CDamageExpandingRadiusAI` (descriptor at `dword_6BD74C`, lazy-init
   via accessor `sub_4764A0`) into `hazard_table`. Per tick, each cached
   hazard within `cfg.HAZARD_REPULSION_RADIUS_SQ` contributes
   `weight / sqrt(d²) * (bot - hazard)` to the accumulator.
3. **Item attractor.** `pick_pickup` does the same walk but for class
   `CPickupAI` (descriptor `dword_6D0B9C`, accessor `sub_53D190`),
   tracking the closest entity within `cfg.ITEM_ATTRACTOR_RADIUS_SQ` and
   LOS-testing the winner via `sub_491380`. The result is cached per bot
   for `cfg.ITEM_SCAN_INTERVAL_FRAMES` frames; when valid, the cached
   target contributes `weight * (pickup - bot)` to the accumulator. Engine
   collision (`sub_4303F0`, downstream) triggers the pickup itself when
   the bot walks over it.

Stuck detection compares the bot's current position against
`bot_last_x/y[slot]` (refreshed every tick); if `d² < STUCK_DELTA_SQ` for
`STUCK_FRAMES_THRESHOLD` consecutive frames, the wander target is
re-rolled immediately. This catches bots wedged against walls or
unreachable random targets.

A `MOVEMENT_ENABLED = False` panic switch reverts to the original
zero-vector behavior with a one-byte scratch flip; no rebuild needed if
the scratch is patched at runtime, otherwise rebuild.

### Movement calibration recipe

The engine multiplies the velocity output by ~100 inside `sub_543CED`
before `sub_4303F0`, so `cfg.BOT_MOVE_SPEED` is in small units. Default
3.0 ≈ human walk pace empirically.

1. Spawn one bot (B → 1 in DM). Watch its walk speed compared to a
   strafing host.
2. If the bot crawls, raise `BOT_MOVE_SPEED` (try 5.0, 8.0). If it
   teleports / clips through walls, lower (try 1.5, 1.0).
3. Press R, decode the `ai_move` snapshot chunk via `tools/diffdump.py`
   — verify `bot_wander_x/y` is within `WANDER_TARGET_RADIUS` of the
   bot's current position and `stuck_count` stays near zero while the
   bot is moving. `tag_hazard` shows the cached `hazard_table` entries.
4. If hazard avoidance doesn't kick in for lava on a specific map, the
   shipped lava entity probably isn't `CDamageExpandingRadiusAI` — the
   `hazard_table` chunk will be empty. Add candidate descriptor VAs to
   `cfg.HAZARD_CLASSES` (currently a single-entry tuple).

## Debug weapon override

Newly spawned bots can be force-equipped for lead-shot testing without changing
runtime input handling. In `zaxbot/config.py`, set `FORCE_BOT_ITEM_NAME` to an
inventory item name such as `Missile Launcher`, or add names to
`DEBUG_BOT_WEAPON_NAMES` and select one with `DEBUG_BOT_WEAPON_INDEX`. The
direct override wins if both are set. `None` disables the override and leaves
the default loadout intact.

The spawn path applies the selected item after character creation by resolving
the engine inventory item definition by name, creating the same transient
pickup item the XmasShopping cheat creates, applying it only to the new bot,
then calling `sub_425590` on the bot inventory to auto-equip it into `Primary`.
Existing bots are not re-equipped; rebuild with `python3 zax_patch.py` and spawn
a new bot after changing the config.

Use R snapshots to confirm the active weapon: `weapon_info` records, in
order, the bot's last firing weapon object, the inventory item-definition
pointer, the resolved CModel pointer (the projectile prototype after
`sub_48D8F0` resolves `[def + 0x20]`; zero ⇒ hitscan weapon or NULL
resolution), the raw `Move/Max Velocity` float read from `[proto + 0x60]`
(pixels/sec, schema range ≈ 300..4000), and the build-time `speed_scale`.
`host_weapon`/`pc2_weapon` record comparable local item ids and item-
definition pointers for real players.

## Per-weapon aim leading

`compute_proj_speed` runs once per fire-tick per bot and drives `apply_lead`.
Three-tier dispatch:

1. **Manual override** — `cfg.WEAPON_SPEEDS` is a `(item_def_va, speed)`
   list scanned linearly. On a hit, the override wins; `speed = 0.0` forces
   hitscan even if the engine has a projectile prototype. Leave the list
   empty (the default) unless you need to pin a specific weapon's speed.
2. **Dynamic def read** — on no override, the hook reads `[def + 0x20]`
   (CInventoryItemDefinition "Projectiles/Projectile"). That field is a
   small-integer registry key, NOT a resolved pointer — the engine
   resolves it lazily via `sub_48D8F0(dword_6CFDD8, key)` → `CModel*`,
   the same resolver the force-equip path uses for item-defs. Key 0 or a
   resolved CModel with `Move/Max Velocity == 0` ⇒ hitscan weapon (Semi
   Auto Pistol, Alien Electrical Weapon, …); `is_hitscan` is set and
   `apply_lead` is skipped. Non-zero velocity ⇒ multiplied by
   `cfg.SPEED_SCALE` to land in the per-fire-tick units `apply_lead`
   expects.
3. **Static fallback** — `cfg.PROJECTILE_SPEED` is the last-resort speed
   used if both lookup paths bail (NULL weapon, NULL def, etc.).

### Intercept solver

`apply_lead` solves the exact quadratic intercept

```
|d + v*t|² = (muzzle + p*t)²
  ⇒  (|v|² - p²) * t² + 2*(d·v - muzzle*p) * t + (|d|² - muzzle²) = 0
```

and picks the positive root using the numerically-stable citardauq form
`t = 2c' / (-b' + sqrt(disc))`. This handles non-perpendicular motion
correctly (the first-order `t = dist/p` approximation was systematically
off by the `d·v` cross-term). Fall back to first-order
`t = (sqrt(c) - muzzle) / p` if `disc < 0`, `a >= 0`, or `c' < 0` (target
inside the muzzle).

### Unit conversion: why a single scale works

`vx` is the target's per-pick_target-call delta (pixels per call), and
`proj_speed` after the def read is `raw_pixels_per_sec * SPEED_SCALE`. The
frame-time `dt` term cancels algebraically — `lead = (real_vel*dt) *
(dist/(raw*dt))` — so one tuned `SPEED_SCALE` covers every weapon as long
as gameplay frame rate is roughly stable (confirmed: engine main loop at
`sub_40F5F0` calls the per-frame tick with `dt = 1/60` exactly).

### Muzzle compensation

`cfg.MUZZLE_OFFSET` (default 20 px) accounts for the gun-barrel spawn
position — bullets don't spawn at the bot's character center, they spawn
at the muzzle tip ~20 px out along the firing angle. Without this, the
bullet's actual flight distance is `dist - muzzle` instead of `dist`, so
the bot would consistently over-lead by `vel * muzzle / proj_speed`
pixels per shot.

Calibrated empirically against Missile Launcher / Light Pistol / Modified
Laser Welder. Some other weapons (Twin Disruptor and others with
asymmetric bullet bounds polygons at `CModel +0x78..+0xA0`) have engine-
side rendering quirks that no muzzle constant can fix.

### Lead randomization

`cfg.LEAD_PROBABILITY` (default 0.5) controls a per-shot coin-flip
between "apply lead" and "shoot at current position":

```
roll = sub_55C4E0(RNG, 0, 99)            ; engine RNG, [0, 99]
if (roll < lead_threshold) apply_lead()  ; threshold = int(PROBABILITY*100)
```

0.0 = always shoot straight, 1.0 = always lead, 0.5 = coin-flip. The
mix makes bots feel more human — a strafing target can't just
counter-strategy against constant prediction; sometimes the bot fires
where you currently are, sometimes where you're going. Hitscan weapons
ignore this knob (they skip `apply_lead` unconditionally).

### Calibration recipe

1. Set `FORCE_BOT_ITEM_NAME = "Missile Launcher"` (slow projectile, easy
   to see lead) and rebuild with `python3 zax_patch.py`.
2. Set `cfg.LEAD_PROBABILITY = 1.0` temporarily so every shot uses
   prediction (eliminates the randomization variable while calibrating).
3. Spawn one bot, host strafes a circle at ~150 px from the bot.
4. If shots consistently miss in front of the target (over-lead), raise
   `cfg.MUZZLE_OFFSET` (e.g. 25, 30). If they miss behind (under-lead),
   lower it (e.g. 15, 10).
5. Switch `FORCE_BOT_ITEM_NAME` to `"Semi Auto Pistol"` and
   `"Alien Electrical Weapon"` and confirm shots land regardless of host
   strafing — these should resolve as hitscan, so the bot aims at the
   current position with no lead. The `ai_fire` snapshot chunk shows
   `is_hitscan == 1` for these.
6. Restore `LEAD_PROBABILITY` to your preferred mix (0.5 default).

## What is finished

- Host-side B/digit spawn path.
- Natural participant classification through `sub_480800`.
- Visible character creation through `sub_59DF90`.
- Scoreboard participation and kill registration.
- Damage and death behavior without the old host hurt-SFX leak.
- PC2 visibility of the bot participant/character.
- Multi-bot support up to map cap, bounded by 16 bot slots.
- Scratch-array cleanup on match change.
- Stationary controller handling with custom fire/aim.
- Per-weapon dynamic projectile speed via engine def lookup.
- Quadratic intercept solver with muzzle-offset compensation.
- Per-shot lead vs straight-shot randomization (`LEAD_PROBABILITY`).
- Hitscan detection (Semi Auto Pistol, Alien Electrical Weapon).

## Still open

- Engine-waypoint navigation. The current DM movement is a random-target
  wander with hazard repulse and item attractor; bots will bump walls and
  re-roll instead of routing around them. The shipped `CWayPointMap` /
  `CWayPointPath` infrastructure should give a proper navigation graph
  per level — bolts onto the existing `bot_wander_x/y` slot.
- CTF/SK objective AI. CTF bots now move but don't pursue flags; SK bots
  don't gather at their own collector. Both require team/objective
  awareness on top of the movement primitive.
- Remote display name. Host writes the stats CString after spawn, but the
  synthetic DirectPlay player-data store is not populated for PC2.

## Historical conclusions to keep

- Hand-encoded ModR/M bytes are dangerous. The `call [edx+8]` vs `call [ecx+8]`
  mistake once produced EIP=1. Prefer helpers and verify emitted bytes.
- Range checks do not make arbitrary dereferences safe. The `0x6E6F6E00`
  mode-scan crash is the canonical example.
- Creating a participant with `sub_5BA790` alone is insufficient; the natural
  join path does important registration/classification.
- Monster `CApproachTargetAI` can attach to a player body, but that path was
  unstable and is not the current approach.
- The old sound mute/filter detours are no longer needed because Phase B gives
  the bot natural remote-participant classification.
