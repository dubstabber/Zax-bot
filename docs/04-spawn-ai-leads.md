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
- `detour_542360` returns a zero movement vector for captured bot controllers,
  so the bot stands still without stealing host input.
- `detour_5436F0` synthesizes aim/fire for captured bot controllers when the
  host is within `FIRE_RANGE_SQ` and `sub_491380` reports line of sight.

Net result: bots are stationary, animate idly, and can shoot at the host, but
they do not navigate.

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

### Unit conversion: why a single scale works

`apply_lead` computes `lead = vx * t` with `t = sqrt(dist²) / proj_speed`.
`vx` is the target's per-pick_target-call delta (pixels per call), and
`proj_speed` after the def read is `raw_pixels_per_sec * SPEED_SCALE`. The
frame-time `dt` term cancels algebraically — `lead = (real_vel*dt) *
(dist/(raw*dt))` — so one tuned `SPEED_SCALE` covers every weapon as long
as gameplay frame rate is roughly stable (which it is in this single-
threaded engine). Default is `1/60` because the engine ticks at ~60Hz.

### Calibration recipe

1. Set `FORCE_BOT_ITEM_NAME = "Missile Launcher"` (slow projectile, easy to
   see lead) and rebuild with `python3 zax_patch.py`.
2. Spawn one bot, host strafes a circle at ~150 px from the bot.
3. If shots land in front, lower `SPEED_SCALE` (e.g. `1/120`). If behind,
   raise it (e.g. `1/30`). One factor-of-2 step usually brackets the right
   value. The same value then works for every other projectile weapon.
4. Switch `FORCE_BOT_ITEM_NAME` to `"Semi Auto Pistol"` and
   `"Alien Electrical Weapon"` and confirm shots land regardless of host
   strafing — these should resolve as hitscan, so the bot should aim
   directly at the current position with no lead. Press R and verify in
   the snapshot that either `current_proto_model_va == 0` (key 0 → no
   resolution) or `proto_speed_raw == 0` (resolved CModel has zero
   velocity), and `is_hitscan == 1` in the `ai_fire` chunk.

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

## Still open

- Movement/navigation. Movement is bundled with either the player's walking
  controller or monster move behavior. The current hook only zeroes movement;
  it does not feed a bot movement vector.
- Objective AI. Bots spawn correctly in all three modes and shoot at the
  host within range, but they do not navigate — so CTF bots can't chase
  flags and SK bots can't collect salvage at their own base. Same
  navigation/goal-seeking workstream as DM movement.
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
