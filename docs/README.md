# Zax bot-support notes

Goal: let the **host** of a multiplayer match add bot participants to
*Zax: The Alien Hunter* by runtime-patching the original `Zax.exe`.

Current control path:
- **B** opens the bot menu in a hosted MP match.
- A digit selects the spawn/team option for the current mode.
- **R** appends a diagnostic runtime snapshot to `zax_dump.bin`; it does not spawn.
- **O** toggles the visual waypoint graph in a live MP match.

## Source of truth

- `Zax.exe.bak` is the pristine original. Never modify it.
- `Zax.exe` is rebuilt from `Zax.exe.bak` every time `python3 zax_patch.py` runs.
- `zax_patch.py` is now a thin entrypoint. The actual patch lives in `zaxbot/`:
  - `zaxbot/config.py` - section size, scratch layout policy, bot ids/names.
  - `zaxbot/addresses.py` - original-image VAs and verified prologues.
  - `zaxbot/patch_manifest.py` - enabled hook/detour sites.
  - `zaxbot/hook/` - dispatcher, mode detection, spawn, snapshot.
  - `zaxbot/detours/` - prologue detours for capture, safety, control, fire/aim.
- `Zax.exe.i64` is an IDA database for the original image only. It does not contain
  the appended `.zaxbot` section.

## Build and test

```bash
python3 zax_patch.py
```

Current testing is on Windows 11 in this local workspace. `zax_patch.py` rebuilds
the local `Zax.exe` from the local `Zax.exe.bak`; do not assume old Linux
`/run/media/...` paths. Then the user runs the game, hosts an MP match, and
exercises B/R. Do not launch the game from automation; runtime testing is
user-owned.

Historical Linux/Wine observations are still useful but can miss Windows-native
behavior. A severe low-FPS regression was seen in earlier diagnostic builds
that kept waypoint-overlay / pickup-registration hot-path detours active on
Windows 11. The visual waypoint overlay hook is installed for authoring but
starts hidden; press O in a live MP match to toggle drawing. Pickup
self-registration is installed for overlay item markers, but its scratch flag is
off while the overlay is hidden and the detour fast-skips the disabled path.

## Current result

The working path is **Phase B: synthetic DirectPlay queue injection**. On spawn,
the hook injects a synthetic "player added" event, calls the engine's own
`sub_480800` join handler, then calls `sub_59DF90` to create and place the
character. The bot is a real participant: it appears on the scoreboard, has a
visible character, takes damage, can be killed, registers kills, and is broadcast
to a second PC.

Current limitations:
- `detect_mode` calls the engine's `sub_59FF90` getter and reads `[result+0]`
  to resolve DM, CTF, and Salvage King. CTF (the only team mode) supports
  picking the bot's team via digit `'1'`/`'2'`; DM and SK both spawn one
  free-for-all bot per `'1'` press. CTF bots now route to the enemy base,
  grab the flag, return to their own base, and capture even when the host is
  far away. SK objective behavior is still absent.
- Bots navigate with `detour_542360`: if a saved `waypoints/<map>.zwpt` graph
  is loaded they steer **straight at the current node**, advance along real
  edges to a **random connected neighbour** (so they roam the whole graph), and
  re-acquire the nearest node on respawn. With no graph they idle (the old
  random-wander/hazard-repulse/pickup-attractor potential field was removed —
  it kept pushing the heading into walls). Because the engine moves a bot only
  by the emitted **angle** and refuses to move it at all when that angle points
  into geometry (no auto-slide), the detour does **not** mirror the stock
  "freeze + face away" wall handler; instead, when a bot is physically wedged
  for a few frames it sweeps the emitted angle (`WP_SLIDE_TURN_STEP_DEG`) until
  a heading clears the wall and slides along it, decaying back to straight once
  moving. This replaced an architecture that froze bots against walls for a
  150-frame timeout.
- CTF routing uses static Red/Blue flag-base anchors parsed from `Data.dat`,
  per-match BFS distance fields over the authored waypoint graph, and a final
  direct approach to the base anchor so the bot physically touches the flag or
  home capture object. The periodic grid scan also matches the expected
  exact-anchor flag/base entity pair at each base and writes `flag_present[]`;
  after that scan it clears a team's entry again if any live character carries
  that team's exact `Red Flag` / `Blue Flag` inventory item, or if that exact
  flag item exists as a dropped world entity away from its home anchor. The
  subtraction is required because base action entities can remain at the anchor
  while the flag item is away.
  If an attacker sees the enemy flag absent, it randomly chooses a stable
  temporary policy for that missing-flag episode: random waypoint roaming to
  search, or continuing to route toward the missing flag's base to wait nearby.
  If a carrier's own home flag is absent, it always searches instead of routing
  into the empty home base. The policy clears once that flag is present again or
  the bot switches goals. Routing to a live dropped flag position is still
  future work.
- The page-flip hook keeps far bots simulated and also force-ticks the cached
  home flag/base entities while a carrier is at home and `flag_present[home]`
  is true; this was required because the engine camera-gates the base
  interaction too, but must not bypass the normal "your flag must be home to
  score" rule. Live CE verification showed flag-base entity caching must match
  raw entity `+0x4C/+0x50` coordinates, not `sub_4FB0A0`, because the getter can
  alias nearby visual pieces to the same anchor while the actual capture objects
  sit on the raw anchor. The flag cache also excludes live player characters:
  a carrier standing on its home base can otherwise be cached as a base entity,
  making `flag_present[]` falsely true and causing the far-base force-tick path
  to tick the bot a second time. Live CE also showed that non-character base
  entities can still leave `flag_present[]` true while the real flag item is
  carried or dropped away from home, so the periodic scan now subtracts exact
  carried-flag inventory state and exact dropped flag item state before routing
  or far-base ticks consume `flag_present[]`.
- The CTF flag-use action is guarded at `sub_5B3100`
  (`CUseInventoryItemAction::execute`) so an illegal capture does not consume
  the carried enemy flag or fire the base's success feedback while the scoring
  team's own flag is away. The actual score action is also guarded at
  `sub_5A9960` (`CGiveTeamAPointAction::execute`) as a numeric-score fallback.
  Both guards use `flag_present[]` plus an exact live inventory scan for the
  scoring team's own `Red Flag` / `Blue Flag` item.
- The visual waypoint overlay is available through the `0x5693A0` page-flip
  detour but starts hidden for normal FPS. In a live MP match, `O` toggles
  drawing, `N` drops/snaps a node at the host, `J` selects the nearest node,
  `X` deletes the nearest node, and `,` saves the current graph to
  `waypoints/<map>.zwpt`; loading is automatic on match change. When visible,
  the overlay culls off-screen vertices/edges before calling the expensive
  engine draw helpers (`OVERLAY_CULL_MARGIN` controls the extra border), and
  turns on pickup self-registration so collectible item markers appear too.
  Teleport portals populate a live `portal_table` (drawn with their own overlay
  color) two complementary ways. The build-time `Data.dat` parse
  (`portal_data.py`) extracts every multiplayer map's warp-teleporter source
  centers — following nested action wrappers and no longer requiring the
  destination name to resolve, so it now catches the **conditional /
  script-driven** teleporters (e.g. Jungle Ruins' "Upper"/"Lower" pair) it used
  to miss — and `load_portals` copies the active map's points in on match
  change, marking portals PROACTIVELY at match start. As defence-in-depth, a
  runtime detour on the relocate/teleport executor (`detour_4C11A0`,
  `cfg.PORTAL_REGISTER_ENABLED`) also self-registers the source pad of any
  `CTeleportAction` warp the moment it fires. Both are detection only for now;
  routing bots into portals is future work.
- Bots can fire/aim at the host within range and line of sight via `detour_5436F0`.
  `zaxbot/config.py` can force newly spawned bots to equip a selected debug
  inventory item name so projectile lead tuning can be tested without bot movement.
- Host-side bot names are set after spawn; PC2 still does not reliably see the
  chosen name because the synthetic DirectPlay player-data store is not populated.

## Doc index

- `01-binary-and-patching.md` - PE layout, `.zaxbot`, patch manifest, build checks.
- `02-keyboard-and-message-pump.md` - WM_KEYDOWN hook and main-thread constraints.
- `03-multiplayer-and-display.md` - manager/session/participant anchors and messages.
- `04-spawn-ai-leads.md` - current Phase B spawn architecture and remaining AI work.

Addresses are absolute VAs for image base `0x400000`. Prefer runtime dumps over
static guesses when validating new multiplayer or bot behavior.
