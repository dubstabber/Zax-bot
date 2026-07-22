# Zax bot-support notes

Goal: let the **host** of a multiplayer match add bot participants to
*Zax: The Alien Hunter* by runtime-patching the original `Zax.exe`.

Current control path:
- **B** opens the graphical bot menu in a hosted MP match (engine-native
  widget dialog): DM/SK show **Add Bot**, CTF shows **Add Blue Bot** /
  **Add Red Bot**, plus **Close** (Esc / the X box also close). The menu
  stays open so several bots can be added.
- **R** appends a diagnostic runtime snapshot to `zax_dump.bin`; it does not spawn.
- **O** toggles the visual waypoint graph in a live MP match.

## Source of truth

- `Zax.exe.bak` is the pristine original. Never modify it.
- `Zax.exe` is rebuilt from `Zax.exe.bak` every time `python3 zax_patch.py` runs.
- `zax_patch.py` is now a thin entrypoint. The actual patch lives in `zaxbot/`:
  - `zaxbot/config/` - per-feature knob modules behind a re-exporting facade
    (`cfg.X` works for every knob; put a new knob in its feature's module).
  - `zaxbot/addresses.py` - original-image VAs and verified prologues.
  - `zaxbot/layout/` - scratch-field layout (field classes in `model.py`,
    per-feature blocks, `builder.py` orchestrator, `from_config.py`).
  - `zaxbot/static_data/` - build-time scratch packing (per-feature tables +
    `write_static_scratch_data` + the cfg mapping in `from_config.py`).
  - `zaxbot/patch_manifest.py` - enabled hook/detour sites.
  - `zaxbot/hook/` - dispatcher, mode detection, spawn, snapshot, `bot_menu.py`
    (B-key graphical bot menu); `entry.py` orchestrates the build (emit order is
    load-bearing).
  - `zaxbot/detours/` - one module per detour; `world_scan/`, `flag_route/`
    and `bot_movement/` are packages split per stage/domain, emitted in a
    fixed order (see `AGENTS.md` for the full map).

The 2026-07 refactor split the former `config.py` / `layout.py` /
`static_data.py` / `world_scan.py` / `flag_route.py` / `bot_movement.py`
monoliths into those packages **byte-identically**: the rebuilt `Zax.exe`
hash was pinned before/after every step. When restructuring emitters, keep
emit order and re-verify byte identity (build twice, compare hashes).
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

Behavior summary (AGENTS.md "Current state" is the authoritative,
detailed map — this is the short version):
- `detect_mode` calls the engine's `sub_59FF90` getter and reads `[result+0]`
  to resolve DM, CTF, and Salvage King. CTF teams are picked with the menu's
  Add Blue/Red buttons; DM and SK spawn free-for-all bots. CTF bots route to
  the enemy base, grab the flag, return, and capture even when the host is
  far away — with alternating attacker/defender roles, per-team route lanes,
  an enemy-carrier chase, a carrier escape priority (no weave/diverts while
  carrying) and a near-base standoff when their own flag is away. SK bots
  execute the mode goal (collect minerals, deposit at their own bin, steal
  death piles). All modes share the dodge weave in fights and need-gated
  health/energy/shield pickup diverts.
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
  home capture object.
  `flag_present[]` ("is that team's flag at its base?") is EVENT-DRIVEN: the
  vanilla map scripts express the state as the base "checker" trigger's
  activation (deactivated by the shared canned scripts when the flag is stolen,
  reactivated on return/capture), and `detours/flag_events.py` detours the
  `CActivateAction`/`CDeactivateAction` per-entity applies (`sub_4C29F0` /
  `sub_4C2D60`) to mirror exactly those transitions whenever the resolved
  target entity sits on a flag anchor. Flags start home; there is no engine
  auto-return, so the two events are the complete transition set. The old
  scan-derived heuristics (anchor-pair counting plus carried/dropped
  subtraction) were removed: a dropped flag is a plain renamed CEntityAnimated
  with no inventory identity, so they never saw the dropped state and left
  `flag_present[]` stuck at 1.
  If an attacker sees the enemy flag absent, it randomly chooses a stable
  temporary policy for that missing-flag episode: random waypoint roaming to
  search, or continuing to route toward the missing flag's base to wait nearby.
  A carrier whose own home flag is absent standoff-tethers near its base
  (route home beyond the map-scaled defend radius, roam inside it) instead of
  routing into the empty home base. The policy clears once that flag is
  present again or the bot switches goals. DROPPED flags are a real pursuit
  target: the periodic scan name-matches the dropped world copy, binds it to
  a graph node, and latched bots graph-route to it (straight steer only
  close-in) — see the dropped-flag bullet in AGENTS.md.
- The page-flip hook keeps far bots simulated and also force-ticks the cached
  exact-anchor base entities while a carrier is at home and `flag_present[home]`
  is true; this was required because the engine camera-gates the base
  interaction too. The event-driven gate flips the same frame a steal
  deactivates the checker, so the tick can never re-arm a script-deactivated
  checker — which is the vanilla "your flag must be home to score" enforcement
  and was the root cause of bots capturing while the enemy's own flag lay
  dropped on the ground. Live CE verification showed flag-base entity caching
  must match raw entity `+0x4C/+0x50` coordinates, not `sub_4FB0A0`, because
  the getter can alias nearby visual pieces to the same anchor while the actual
  capture objects sit on the raw anchor. The cache holds up to three entities
  per anchor (checker, spawn marker, recreated flag) so grid order cannot evict
  the checker, and still excludes live player characters (a carrier standing on
  its base was once cached and double-ticked).
- The CTF score action is guarded at `sub_5A9960`
  (`CGiveTeamAPointAction::execute`) as a last-resort backstop, using
  `flag_present[]` plus an exact live inventory scan for the scoring team's own
  `Red Flag` / `Blue Flag` item. The old companion guard at `sub_5B3100`
  (`CUseInventoryItemAction::execute`) was REMOVED: the drop-on-death canned
  script consumes the dying carrier's flag through the same action, so that
  guard wrongly blocked flag drops whenever both flags were out.
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
  `CTeleportAction` warp the moment it fires. On top of detection, pads whose
  warp DESTINATION resolves at build time (`New Location` → a positioned Level
  Part, e.g. Hydro Vengence's four warm/cold pads) are DIRECTED ROUTING EDGES:
  `bind_portal_nodes` binds pad + exit to their nearest graph nodes per match,
  `bfs_run` relaxes entry→exit in every CTF distance field (Hydro's two arenas
  connect only through the pads), `ctf_next_hop` latches a pad final-approach
  when the pad is the shortest hop, a >192px per-think position jump
  cold-reacquires the nearest node at the exit, and roaming/DM bots
  occasionally wander into an adjacent active pad
  (`cfg.PORTAL_WANDER_CHANCE`). See the portal-routing bullet in AGENTS.md.
- Doors are detected the same static way (`door_data.py` extracts every MP
  map's `Activity=CDoorAI` Level Parts — 10 maps / 333 doors — and
  `load_doors` copies the active map's centers in per match). Live open/closed
  state is PER-FRAME fresh: the periodic grid scan only caches the entities
  sitting on each door anchor, and the page-flip hook re-reads their SOLID bit
  (`entity+0x1C & 0x40000`) every frame — the original scan-derived state was
  live-tested and rejected because the frame-counted scan interval stretched
  to many seconds whenever the visible overlay lowered FPS, leaving rings
  stale until the overlay was toggled. The overlay draws every door as a small
  oval and rings closed ones at double radius. Two routing consumers ship on
  top: (1) a failed-edge marker set while wedged against a blocked door is
  cleared the moment that door reads passable again; (2) door-aware CTF
  rerouting with DIRECTIONAL, PER-TEAM passability — per-team BFS fields
  treat a closed-door edge as traversable only from a side that team's bot
  could open the door from (parsed opener topology: walk-in triggers,
  self-trigger walk-up doors incl. same-team-conditional ones, Hydroplant-
  style arming volumes; open+toggle actions with '#a-b#' template-name
  expansion; doors with no authored opener are engine bump-open and pass
  both ways; switch/spawn/timer-only doors — e.g. the Torture Chamber pillar
  walls — are impassable while closed). The fields rebuild (debounced)
  whenever any door flips state, so a bot pinned at one closed path actively
  reroutes the moment an alternative opens, and falls back to the full field
  (walk at the door) when the goal is unreachable otherwise. Switch-seeking
  (CollideTriggerAI wall switches) ships too: sealed or shortcut-blocked
  routed bots seek the best reachable door-opening switch and bump it, and
  goal-less roamers occasionally press adjacent blocked switches.
- Bots can fire/aim at targets within range and line of sight via
  `detour_5436F0`, with per-weapon projectile-speed lead prediction.
  `zaxbot/config/spawn.py` can force newly spawned bots to equip a selected
  debug inventory item name so lead tuning can be tested in isolation.
- Host-side bot names are set after spawn; PC2 still does not reliably see the
  chosen name because the synthetic DirectPlay player-data store is not populated.

## Doc index

- `01-binary-and-patching.md` - PE layout, `.zaxbot`, patch manifest, build checks.
- `02-keyboard-and-message-pump.md` - WM_KEYDOWN hook and main-thread constraints.
- `03-multiplayer-and-display.md` - manager/session/participant anchors and messages.
- `04-spawn-ai-leads.md` - current Phase B spawn architecture and remaining AI work.

Addresses are absolute VAs for image base `0x400000`. Prefer runtime dumps over
static guesses when validating new multiplayer or bot behavior.
