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
- `detour_542360` synthesizes a movement vector for captured bot controllers:
  pure node-to-node waypoint following plus a reactive wall-slide (see "Bot
  movement" below). Falls back to a zero vector (idle) when
  `MOVEMENT_ENABLED == 0`, `WP_FOLLOW_ENABLED == 0`, or no graph is loaded.
- `detour_5436F0` synthesizes aim/fire for captured bot controllers when the
  host is within `FIRE_RANGE_SQ` and `sub_491380` reports line of sight.

Net result: bots follow the saved waypoint graph node-to-node, roaming the
whole graph by picking random connected neighbours, and slide along walls
instead of freezing. With no graph they idle. CTF bots layer objective routing
on top of the same follower; SK bots still do not pursue collectors.

## Bot movement (waypoint follower + wall-slide)

### Why the angle is everything

Decompiling the caller `sub_543B60` (it calls `0x542360` at `0x543ced`) pins
down how the engine uses our two outputs:

- The character's movement DIRECTION is `cur_pos + 100*(cos(angle),
  sin(angle))` passed to `sub_4303F0`. **Only the emitted angle `[esp+8]`
  steers.**
- The velocity vector `[esp+4]` matters only through its MAGNITUDE, which the
  engine compares against the model's walk/run schema thresholds to pick the
  animation tier and step. Its direction is ignored.
- `sub_4303F0` is ALL-OR-NOTHING: if the angle points into geometry the
  collision sweep fails and the character does **not move at all**. There is no
  engine wall-slide.

The old detour synthesized the angle from a potential field (random wander +
hazard repulse + pickup attractor) and an edge look-ahead, then MIRRORED
`sub_542360`'s own wall-block post-process (zero the vector + face away when
`dot(block, out) < 0`). For a human that freeze is fine — the player steers
parallel by hand to slide. A bot has nobody to steer it, so it froze against
the wall until a 150-frame timeout. That, plus the field/look-ahead constantly
aiming the angle INTO walls, is why bots stuck on walls. The fix was a full
rewrite, not a tuning pass.

### The follower

The detour writes `velocity = normalize(node - bot) * cfg.BOT_MOVE_SPEED`
(magnitude only — keeps the engine out of Idle) and `angle = atan2(desired)`
via `sub_509100`. When `cfg.WP_FOLLOW_ENABLED` and `overlay_vertex_count > 0`:

- **Steer straight at the current node** (no look-ahead; the look-ahead was
  what cut corners into walls).
- **Arrival + edges.** When `dsq(bot, node) < cfg.WP_REACHED_RADIUS_SQ`
  (`(64px)²`) it advances via `wp_advance` to a RANDOM connected neighbour
  (gated by `cfg.WP_RANDOM_NEIGHBOR`; prefers `!= prev`, falls back to `prev`
  at a dead end), so bots roam the whole graph while respecting connections.
- **Respawn / death.** A (re)spawned bot drops its latch and cold-acquires the
  NEAREST node; edges constrain only after that first pick. Detected by the
  live char pointer changing.
- **Progress watchdog.** If the bot fails to strictly reduce `dsq` to the
  current node for `cfg.WP_PROGRESS_TIMEOUT_FRAMES`, it re-acquires the nearest
  node (a coarse safety net; the wall-slide normally keeps it progressing).

With no graph / follow disabled, the detour emits a zero vector (idle). The
random-wander/hazard-repulse/pickup-attractor pipeline was REMOVED.

### Wall-slide (replaces the freeze)

The detour does NOT re-emit the engine's freeze. The trigger is LACK OF
PROGRESS, not stillness: a bot grinding ALONG a wall keeps moving
(`bot_stuck_count == 0`) yet never approaches its node, so a position-delta
metric misses it. The progress watchdog's `bot_wp_try[slot]` climbs whenever the
bot fails to get strictly closer to its node. Once `wp_try >=
WP_SLIDE_TRIGGER_FRAMES` (≈8) the follower cycles a per-bot deflection
(`bot_flee_ticks[slot]`, repurposed as `slide_turn`, 0..11 wrapping) one step
every few frames and adds `slide_turn * cfg.WP_SLIDE_TURN_STEP_DEG` to the
emitted angle, so the bot tries every heading around a full circle until one
escapes the wall/pocket and makes progress (which resets `wp_try` → straight at
the node again). The velocity magnitude stays `BOT_MOVE_SPEED` throughout, so
the engine keeps walking the bot. No angle wrap is needed: the movement angle is
range-agnostic (the engine uses cos/sin) and is overwritten by the fire/aim path
before any facing use.

IMPORTANT — this reactive sweep only escapes wall *grazes* between nodes; it
cannot reliably climb out of a deep spawn pocket whose nearest node is hundreds
of px away across a wall. Reliable following REQUIRES a graph dense enough that
consecutive connected nodes have a walkable straight segment between them
(≈60–80px spacing through corridors and around corners), AND a node at/near each
spawn point. On Molten Ice the bottom-left spawn pocket (~290,1110) currently has
no node within 195px — bots spawned there have nothing to follow. Author a node
where bots spawn plus a chain tracing the walkable route out.

DIAGNOSTIC: the controller block vector `+0x14/+0x18` is mirrored into the
dormant `bot_wander_x/y[slot]` so an `ai_move` R-dump shows whether the engine
populates it near walls — the input needed to later add a smoother geometric
slide (project the heading onto the wall tangent) on top of the angle sweep.

A `MOVEMENT_ENABLED = False` panic switch reverts to the zero-vector behavior.

## CTF flag routing and far-base capture

CTF bots now use the waypoint graph for objective routing:

- `flag_data.py` parses static Red/Blue flag-base anchors from `Data.dat`.
  Do not assume CTF maps live only under `/CTF/`: live testing used CTF mode on
  `Levels/Multiplayer/DeathMatch/Hydroplant Bouncefest.zax`, and its Red/Blue
  flag anchors are valid.
- `world_scan.py:load_flags` copies the active map's anchors plus explicit
  team tags (`0` Blue, `1` Red) into `flag_table` / `flag_team` on match change.
- `build_flag_routes` runs once per CTF match and fills `flag_dist[base][node]`
  by BFS over the authored waypoint graph. At each node arrival,
  `ctf_next_hop` picks a connected neighbour with a strictly smaller distance
  to the current goal base.
- `ctf_pick_goal` chooses enemy base while not carrying and own base while
  carrying. Carry detection is the live-verified inventory-group test:
  `sub_4267E0(char)` then `sub_425290(inv, [0x714454]) != -1`.
- `flag_present[]` is EVENT-DRIVEN. Vanilla CTF expresses "own flag is home"
  as the base checker trigger's activation (`Red Checker` / `Blue Checker`,
  authored exactly on the flag spawn anchor on every CTF-capable map): the
  shared canned scripts in Data.dat run `CDeactivateAction` on the checker
  when that flag is stolen and `CActivateAction` when it is returned or reset
  after a capture. `detours/flag_events.py` detours the two actions'
  per-entity applies (`sub_4C29F0` / `sub_4C2D60`) and, when the resolved
  target entity sits on a `flag_table` anchor, writes `flag_present[i] = 1/0`
  — the exact vanilla transition set with no scan staleness. Flags start home
  (`load_flags` seeds 1); the engine has no auto-return (no exe reference to
  the spawn-point names), so nothing else moves a flag. If an attacker sees
  the enemy flag absent,
  `ctf_pick_goal` rolls a stable temporary policy for that missing-flag episode:
  either set `route_goal_flag = -1` so node arrivals fall back to random
  waypoint roaming/search, or keep the goal base so the bot waits/patrols near
  the missing flag's home anchor. If a carrier's own home flag is absent, it
  always uses search mode; do not route or final-approach a carrier into an
  empty home base, because CTF capture is illegal until the home flag returns.
  The policy is cleared when that flag is present again or the bot changes goal.
- When the current waypoint is already the goal base's nearest node, the
  movement detour steers directly at `flag_table[goal]`. This final approach is
  required to physically touch the flag or home capture object; without it the
  bot bounces between graph nodes around the base. The final approach carries
  its own progress watchdog (target = the flag position, reusing
  `wp_best_dsq`/`wp_try`): it used to jump straight to the emit with no
  arrival/progress machinery at all, so a blocked line to the base (a door)
  pinned the carrier forever.
- Routed wedges suspend routing per bot (`bot_route_suspend[slot]`,
  `WP_ROUTE_SUSPEND_FRAMES`): after any routed progress-timeout the bot roams
  the graph randomly for a few seconds instead of letting deterministic BFS
  funnel it back into the same blocked segment, then routing resumes.
  `ctf_pick_goal` reports "no goal" while suspended, which also parks the
  final approach and the far-base force-tick for that bot.
- The failed-edge marker is retried, not permanent (`route_block_hits[slot]`,
  `WP_ROUTE_BLOCK_RETRY_HITS`). Live CE caught the residual stuck loop: with
  the door edge marked and on the only shortest path, every arrival re-chose
  it, the forced fallback bounced the bot between the two nodes flanking the
  door (both inside the arrival radius, so `wp_try` stayed 0 — no timeout, no
  suspension), and the marker never expired even after the door became
  passable. After N forced fallbacks the marker is cleared so the edge is
  retried; a still-blocked edge re-marks itself through the wedge timeout.
  The marker also clears when a roam suspension expires.

Far from the host camera, two things must be forced awake. The page-flip detour
already force-ticks skipped bot characters through the same three entity stages
as the engine's active-entity driver (`vtbl +0x7C`, `+0x80`, `+0x8C` with
`EBP=0x10000`). That keeps the carrier moving, but capture still did not fire
until the host approached the carrier's home base. The missing piece was the
base checker trigger itself: it is camera-gated too. The periodic
`scan_portal_active` grid walk caches the distinct entities sitting exactly on
each `flag_table` anchor in `flag_entity[]` (three slots per anchor — checker,
spawn marker, recreated flag — so grid iteration order cannot evict the
checker), and the page-flip detour force-ticks the cached home entries only
while a carrying bot is within `CTF_FLAG_HOME_FORCE_TICK_RADIUS_SQ` of its home
base AND `flag_present[home] != 0`. That final gate is load-bearing AND now
exact: the checker deactivation event flips `flag_present[home]` in the same
action chain that steals the flag, so this path can never re-arm a
script-deactivated checker. (Waking a deactivated checker sets a sticky Active
bit, which re-enables captures for EVERYONE until the next script transition —
that was the mechanism behind bots scoring while the enemy team's own flag lay
dropped on the ground, something vanilla players can never do.)

The flag-entity cache must exclude player characters. Live CE caught the red
carrier itself in `flag_entity[red][0]` while it stood on the red base with the
blue flag; the far-base helper then ticked the bot as if it were a base entity,
visibly increasing its fire/update rate. The cache carries no presence meaning
anymore — earlier designs derived `flag_present[]` from anchor entity
pairs/counts plus carried-inventory and dropped-item subtractions, and every
variant had a false-home hole (most fatally: a DROPPED flag is a plain
script-created `CEntityAnimated` with no inventory identity, invisible to a
`CInventoryItem + 8` definition-id match, so `flag_present` stayed 1 after a
carrier died).

The score action keeps a last-resort guard: `detour_5A9960` wraps
`CGiveTeamAPointAction::execute` and suppresses the gametype score callback
while the scoring team's own flag is away per `flag_present[]` or any live
character inventory. It should never fire with the event-driven gate in place.
The old flag-use guard at `sub_5B3100` (`CUseInventoryItemAction::execute`) was
REMOVED and must not be reinstated there: the drop-on-death canned script
("Does player have a flag") consumes the dying carrier's flag through the very
same action, so a home-flag guard at that site wrongly blocked flag drops
whenever both flags were out; and blocking the capture chain mid-way is not
clean regardless, because the canned object's enemy-flag re-create runs before
the use action.

Important CE finding: flag-base entity matching must use raw entity
`+0x4C/+0x50` coordinates, not `sub_4FB0A0`. On Hydroplant Bouncefest,
`sub_4FB0A0` aliased nearby visual/base pieces to the flag anchor and cached
the wrong pair, while the actual capture/touch entities sat exactly on the raw
anchor. Temporarily writing those exact-anchor entity pointers into
`flag_entity[home]` made `route_carry` clear within about a second, proving that
the capture path was correct and the cache selection was wrong.

The live flag state is now exact for all three states (home / carried /
dropped), but routing still does not chase the dropped position. If pursuit is
ever added: the dropped copy is a script-created entity named `Red Flag` /
`Blue Flag` with sequence `Not Home`, findable in the spatial grid. Do not add
BFS/pathfinding toward a dropped flag without a deliberate live position table
and role policy; random graph walking is the intended search behavior until
that is built.

### Movement calibration recipe

The engine multiplies the velocity output by ~100 inside `sub_543CED` before
`sub_4303F0`, so `cfg.BOT_MOVE_SPEED` is in small units; keep it above the
model walk threshold (default `1.0`).

1. Spawn one bot (B → 1). Watch its walk speed compared to a strafing host.
2. If the bot crawls, raise `BOT_MOVE_SPEED`; if it teleports / clips through
   walls, lower it.
3. Press R near a wall, decode the `ai_move` chunk via `tools/diffdump.py`:
   `bot_last_x/y` (and the live char position) should CHANGE between dumps — a
   sliding bot moves; a frozen one does not — and `bot_wp_try` should stay low
   rather than climbing to `WP_PROGRESS_TIMEOUT`. The first two `ai_move`
   fields (formerly `bot_wander_x/y`) now hold the mirrored block vector.
4. If wall handling feels jittery, lower `WP_SLIDE_TURN_STEP_DEG` for a finer
   sweep, or implement the geometric block-vector slide (see Open work).

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
- CTF static-base routing: not carrying -> enemy base, carrying -> own base.
- CTF missing-base behavior: attackers search by random waypoint roaming or
  wait near the missing enemy flag's base, chosen randomly per missing-flag
  episode; carriers whose own home flag is missing always search.
- Far-camera CTF capture support by force-ticking skipped bots and nearby
  cached home flag/base entities.

## Still open

- Engine `CWayPointMap` integration. The active navigation path uses the
  patch's saved `waypoints/<map>.zwpt` overlay graph, not the shipped engine
  `CWayPointMap` / `CWayPointPath` data.
- CTF dropped-flag pursuit: `flag_present[]` detects carried flags and exact
  dropped flag world items away from home, but bots still do not route to the
  dropped position. Future bot commands can choose roles on top of the current
  attacker search/wait split and carrier search fallback.
- SK objective AI: SK bots don't gather at their own collector yet.
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
