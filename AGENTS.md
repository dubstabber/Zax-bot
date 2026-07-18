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

Current testing is on Windows 11 in this local workspace. `zax_patch.py` rebuilds
the `Zax.exe` next to it from the local `Zax.exe.bak`; do not assume old
`/run/media/...` Linux paths. Then the user runs `Zax.exe` and reports runtime
behavior. Do not launch the game from automation.

Historical Linux/Wine results are still useful but not definitive. A severe
low-FPS regression from earlier diagnostic builds with waypoint-overlay /
pickup-registration hot-path detours was observed on Windows 11 only on the
same machine; it did not reproduce on Linux via Wine. The visual waypoint
overlay hook is installed for authoring but starts hidden; press O in a live MP
match to toggle drawing. When visible, the overlay cheap-culls off-screen graph
segments before calling the engine draw helpers; tune `OVERLAY_CULL_MARGIN` if
near-edge visibility vs FPS needs adjustment. Pickup self-registration is
installed for overlay item markers, but its scratch flag stays off while the
overlay is hidden and the detour fast-skips the disabled path.

## Current state

Working path: **Phase B - synthetic DirectPlay queue injection**.

- WM_KEYDOWN hook at `0x599A1A` redirects `call sub_599580` to
  `.zaxbot:hook_entry`, then tail-jumps back to `sub_599580`.
- `.zaxbot`: VA `0x71A000`, raw `0x231000`, size `0x16000`, RWX (grown from
  `0xA000` for the CTF flag static tables, then to `0xC000` for the CTF routing
  BFS distance field, then to `0xD000` for far-base CTF flag entity force-ticks,
  then to `0xF000` for the door detection static tables, then to `0x11000` for
  the door-aware routing field + anchor-entity cache, then `0x12000` for its
  per-team split, then to `0x14000` for the switch detection tables, then to
  `0x16000` for the portal routing layer).
- Scratch starts at `0x722000` (`SCRATCH_OFF = 0x8000`; the code/scratch
  boundary moved from `0x5A00` at the door layer, from `0x6800` at the switch
  layer, then from `0x7000` when the portal routing layer landed with ~246
  code bytes left).
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
- Bots navigate the authored waypoint graph via `detour_542360`. The model is
  a **pure node-to-node follower with a reactive wall-slide**, grounded in how
  the engine consumes our two outputs (confirmed by decompiling the caller
  `sub_543B60`, call site `0x543ced`):
  - Movement DIRECTION is `cur_pos + 100*(cos(angle), sin(angle))` fed to
    `sub_4303F0` — **only the emitted angle `[esp+8]` steers**. The velocity
    vector `[esp+4]` matters only through its MAGNITUDE, which picks the
    idle/walk/run tier; its direction is ignored.
  - `sub_4303F0` is ALL-OR-NOTHING: if the angle points into geometry its
    collision sweep fails and the bot does **not move at all**. There is no
    engine wall-slide for bots.
  - So the detour steers straight at the current node (`desired = node -
    bot`; `angle = atan2(desired)`, `|velocity| = BOT_MOVE_SPEED`), advances
    on arrival (`WP_REACHED_RADIUS_SQ`) to a RANDOM connected neighbour via
    `wp_advance` (gated by `cfg.WP_RANDOM_NEIGHBOR`; prefers `!= prev`), and
    re-acquires the nearest node on respawn.
  - **Wall-slide, NOT freeze.** The detour deliberately does NOT mirror
    `sub_542360`'s own "wall block" post-process (which zeros the vector and
    faces away when `dot(block, out) < 0`). That freeze is correct for a human
    who then steers parallel by hand, but a bot has nobody to steer it, so the
    old mirrored version pinned bots to walls until a 150-frame timeout. WARNING
    to future agents: do **not** reinstate that freeze. Instead, when a bot
    makes NO PROGRESS toward its node for `WP_SLIDE_TRIGGER_FRAMES` (the `wp_try`
    counter — NOT `stuck_count`: a bot grinding ALONG a wall keeps moving, so a
    position-delta "stuck" metric misses it), the follower cycles the emitted
    angle through a full circle (`cfg.WP_SLIDE_TURN_STEP_DEG` per step, one step
    every few frames) until a heading escapes the wall/pocket and the bot makes
    progress again (which resets `wp_try` → straight at the node). Engine-
    internal-independent.
  - DIAGNOSTIC: the controller block vector at `+0x14/+0x18` is mirrored into
    the dormant `bot_wander_x/y[slot]` so an `ai_move` R-dump reveals whether
    the engine populates it near walls — the data needed to later add a smoother
    geometric slide (project the heading onto the wall tangent) on top of this
    angle sweep.
  - `cfg.MOVEMENT_ENABLED = False` reverts to zero-vector. `cfg.WP_FOLLOW_
    ENABLED = False` / no graph ⇒ bots idle (the random-wander/hazard-repulse/
    pickup-attractor potential field and the edge look-ahead were REMOVED —
    they constantly aimed the angle into walls). `detour_5436F0` still
    synthesizes aim/fire when range + LOS allow.
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
- Teleport portals populate `portal_table` (drawn by the overlay) two
  complementary ways — both now catch conditional/script-driven teleporters:
  - **Static, PROACTIVE** (`portal_data.py` → `world_scan.py:load_portals`,
    per match): the build-time `Data.dat` parse extracts every Level Part whose
    action tree contains a `CTeleportAction` (or a warp-carrying
    `CRelocateAction`) and records its source-trigger center; `load_portals`
    copies the active map's points in on match change, so portals are marked at
    match START without anyone using them. The parse follows nested wrappers
    (`Exit Action=CMultipleActionsAction` → `Action=Array` →
    `Action=CTeleportAction`) and does **not** require the action's
    `New Location` to resolve to a Level Part name — that over-strict gate used
    to drop the "Upper"/"Lower" script teleporters (e.g. Jungle Ruins DM). Scope
    is MULTIPLAYER maps only (the whole pipeline is MP-gated; SP maps would
    never load, and all 54 SP+MP portal maps overflow the scratch table — only
    2 MP maps have portals). Verified in CE: the parsed Jungle Ruins centers
    (1259.5, 2105.0)/(1610.25, 1445.0) match the live pads.
  - **Runtime, OBSERVATIONAL** (`portal_register.py`, `detour_4C11A0`, gated by
    `cfg.PORTAL_REGISTER_ENABLED`): a detour on the relocate/teleport executor
    self-registers the SOURCE pad of every `CTeleportAction` warp the instant it
    fires (filters `[action]` == `0x6033B0`, reads the teleported entity at
    `[esp+4]` via `sub_4FB0A0`, dedups within `PORTAL_DEDUP_RADIUS_SQ` = 128px,
    appends/accumulates per match). Complements the static table as defence-in-
    depth: catches any active teleporter the text parse missed and truly dynamic
    ones, the moment something teleports. Mirrors the pickup self-registration
    model; confirmed firing in-game (breakpoint at `0x4C11A0`, `ecx` = the two
    `CTeleportAction`s). Detection feeds the ROUTING layer below; runtime-
    registered pads have no build-time destination so they stay wander-only.
- Bots ROUTE THROUGH portals (`cfg.PORTAL_ROUTING_ENABLED`; the layer that
  makes Hydro Vengence CTF playable — its two arenas connect ONLY via pads):
  - **Destinations at build time**: `portal_data.resolve_portal_routes` also
    resolves each warp action's `New Location` to a positioned Level Part
    (Hydro's `warm 1/2`/`cold 1/2`; each pad's exit lands next to the paired
    return pad). Script targets that don't resolve (Jungle Ruins
    "Upper"/"Lower") keep dest=None → detect/wander-only. Packed as
    `portal_static_dests`/`portal_static_hasdest` parallel to the source
    points; `load_portals` copies them per match.
  - **Per-match node bindings** (`bind_portal_nodes`, from `detour_df90`
    after `wp_load`+`load_portals`, before `build_flag_routes`): nearest
    graph node to each pad (`portal_node`) and to each resolved exit
    (`portal_dest_node`); also clears the per-bot pad latches.
  - **Directed BFS edges**: `bfs_run`'s portal pass relaxes
    `dist[src_node] = dist[dest_node] + 1` for every dest-carrying pad in
    EVERY field it fills (full, per-team open, switch-seek) — the BFS runs
    from the goal outward, so a pad whose EXIT node is dequeued lowers its
    ENTRY node. Not gated on live pad state (fields are per-match; a stale
    route into a deactivated pad ends in the normal watchdog → suspension →
    roam machinery). Offline-verified + pinned in tests on the shipped Hydro
    graph: arenas disconnected without pads, enemy base 12 hops with them,
    both departure-arena pads strictly descending (bots pick whichever their
    path reaches).
  - **Pad next-hop** (`ctf_next_hop`): after the neighbour scan, a pad bound
    to the CURRENT node whose exit node carries a strictly smaller distance
    in the active field wins; the pad idx+1 goes to `route_portal_hop`, the
    call returns cur, and the follower latches `bot_portal_target[slot]`.
    THIS side is gated on live `portal_active[]` (live-verified 1 for all 4
    Hydro pads) so a deactivated teleporter is never entered.
  - **Pad final-approach** (`bot_movement`, top of `s542360_wp_have_cur`):
    a latched bot steers at the pad CENTER through the same watchdog as the
    CTF flag final approach (stall ramps `wp_try` → wall-slide sweep). The
    trigger is a THIN SLIVER on a collidable teleporter prop, so a watchdog
    timeout first grants PAD-PRESS PATIENCE (`cfg.PORTAL_PRESS_PATIENCE`
    fresh watchdog cycles of continued pressing/sweeping — mirror of the
    door patience; live snapshots caught a carrier suspending at the pad
    and only succeeding on its second visit) before the latch drops and
    routing suspends. Latch also drops on respawn, death, match change,
    stale idx, or the pad reading inactive.
  - **Teleport-jump re-acquire** (stuck-detection stage): a per-think move
    farther than `sqrt(cfg.PORTAL_JUMP_REACQUIRE_DIST_SQ)` (192 px; engine
    step is ~1.7 px/frame, Hydro pads jump ~1600 px) can only be a teleport
    — drop the whole nav latch (current/prev wp, markers, slide, pad latch;
    NOT route_suspend) and cold-acquire the NEAREST node at the exit this
    same think. Mode-independent, so bots knocked through script teleporters
    they never chose also recover.
  - **Post-teleport RETURN-PAD heading veto** (`s542360_portal_veto`, after
    the wall-slide; the anti-ping-pong wall): the teleport drops the bot at
    the exit marker inside a collision pocket around the teleporter prop,
    ~28 px from the RETURN pad's thin trigger sliver; the wall-slide sweep
    escapes the pocket but tries headings in fixed order, and any
    sliver-ward heading that moves fires an ENGINE re-teleport (live proute
    snapshots: carrier pinned at exact exit coords, then bounced
    arena-to-arena every ~1-2 s — no bot decision involved, so the wander
    gates couldn't stop it). While `bot_portal_cd` runs, any heading whose
    `LAVA_LOOKAHEAD_PX` lookahead lands within
    `sqrt(cfg.PORTAL_VETO_RADIUS_SQ)` (40 px) of a NON-LATCHED pad center is
    rotated onward (lava-veto style) — pads become virtual walls; the
    deliberately latched pad stays enterable (returning through a pad is
    often the correct route).
  - **Roam wander-entry** (`portal_wander_check`, from the `wp_advance`
    fallback path): in DM — and for CTF bots roaming on a missing-flag
    search — an arrival at a node hosting an active pad rolls
    `RNG(0..99) < cfg.PORTAL_WANDER_CHANCE` (default 25) and occasionally
    walks INTO the teleporter instead of picking a random neighbour. No
    destination knowledge needed (the jump re-acquire recovers the graph).
    Two gates, both live-diagnosed from R snapshots: NO roll while the
    bot's routing is SUSPENDED (suspension roam is a local unstick; a
    suspended CARRIER was caught bouncing arena-to-arena on this roll),
    and NO roll for `cfg.PORTAL_WANDER_COOLDOWN_FRAMES` after any teleport
    (each pad's exit node IS the return pad's node, so the very next
    arrival re-rolled the coin — the observed pad ping-pong).
  - R-snapshot chunk `proute` dumps dest tables + bindings + per-bot latches
    (approach latch, wander cooldown, pad patience) + the last jump d².
- CTF flags populate `flag_table` (drawn by the overlay in blue) via the SAME
  static Data.dat pipeline as portals (`flag_data.py` → `world_scan.py:
  load_flags`, per match). The build-time parse extracts each multiplayer
  `.zax` map's two flag-base anchors — the `"Red Flag Spawn"` / `"Blue Flag
  Spawn"` Level Parts (`Position X`/`Y`) — and `load_flags` copies the active
  map's points into `flag_table` on match change (matched by the runtime map
  name at `MAP_NAME_CSTRING_VA`, full-path form e.g.
  `Levels/Multiplayer/CTF/Torture Chamber.zax`). The flag spawn anchors are the
  HOME-base positions — the right foundation for CTF bot routing (carry the
  enemy flag to your home base). Current CTF routing uses these static base
  anchors successfully: bots go to the enemy base, grab the flag, return home,
  and capture. `flag_present[]` ("is that team's flag at its base?") is
  EVENT-DRIVEN — see the checker state machine below; it is NOT derived from
  the grid scan anymore, and it is still not a dropped-flag routing target.
  NOTE: in the
  8-bit palettized overlay the hue
  is driven by the BLUE byte alone, so flags
  (blue), portals (pink), pickups (cyan) and vertices (white) all render with
  the same palette index — distinguish them by position/count, not color.
- CTF bots ROUTE to flags through the waypoint graph (`detours/flag_route.py`,
  gated by `cfg.CTF_FLAG_ROUTING_ENABLED`). Pieces:
  - **Team tagging** (build time): `flag_data.py` tags each base by anchor name
    (Red=1/Blue=0); `static_data.py` packs a parallel `flag_static_team`;
    `load_flags` copies `flag_table` + `flag_team` per match (file order is NOT a
    reliable Red/Blue order, hence the explicit tag).
  - **Per-match BFS** (`build_flag_routes`, from `detour_df90`, only when
    `detect_mode()==CTF` and `flag_count>0`): nearest graph node to each base,
    then a BFS over the UNDIRECTED edge list fills `flag_dist[base][node]` (hop
    distance, `0xFFFFFFFF`=unreachable). Arms `flag_routing_active`.
  - **Goal-biased follow** (`ctf_pick_goal` + `ctf_next_hop`, injected at
    `s542360_wp_arrived`): goal = carrying ? OWN base : ENEMY base (team to
    `flag_team`). `ctf_next_hop` steps to the neighbour of the current node with
    strictly-smaller `flag_dist[goal]` (guaranteed progress on a real shortest
    path); -1 falls back to the random `wp_advance` whenever routing can't apply
    (non-CTF / no graph / no flags / unreachable). Carry detection is the
    live-verified inventory-group test (`sub_4267E0` then
    `sub_425290(inv,[0x714454])`), fully NULL-guarded; see
    [[ctf-flag-carry-detection]].
  - **Final approach** (`bot_movement.py` at `s542360_wp_have_cur`): once the
    bot's current node IS the goal base's nearest node the graph can't get
    closer, so steer straight at `flag_table[goal]` to physically TOUCH the flag
    (grab it / deliver to own base to capture). Without it the bot circles the
    last node (at the goal node `ctf_next_hop` finds no closer neighbour, the
    random `wp_advance` bounces it off, routing snaps it back, loop).
    `ctf_pick_goal` runs every frame, so the instant the bot grabs the flag the
    goal flips to home and it heads back. If the current goal flag is absent
    from its base (`flag_present[goal] == 0`), attacker bots roll one stable
    temporary policy for that missing-flag episode: search by random waypoint
    roaming (`route_goal_flag = -1`), or keep routing toward the missing flag's
    base to wait/patrol nearby. Carrier bots whose OWN home flag is missing
    always use search mode; do not route/final-approach a carrier into an empty
    home base. The policy clears when the flag becomes present again or the bot
    switches goals. Do NOT add BFS/pathfinding for an unknown flag location.
  - **Blocked-route suspension** (`bot_route_suspend[slot]`, flag-route block;
    `cfg.WP_ROUTE_SUSPEND_FRAMES`): BFS routing is deterministic, so a bot
    whose shortest path is physically blocked (classic case: a door the
    camera-gated engine never opens for far bots) used to be funnelled back
    into the same blocked segment from every direction — a carrier pinned at
    "certain waypoints" until its goal changed. Now any routed
    progress-timeout (the `s542360_wp_reacquire` watchdog while
    `route_goal_flag != -1`) suspends routing for that bot: `ctf_pick_goal`
    reports no goal while the per-bot countdown runs, so next-hops, the final
    approach AND the far-base force-tick all fall back to random graph roaming
    (exactly the behavior that empirically un-sticks such bots), then routing
    resumes. The follower decrements the counter once per think; respawn
    clears it. The CTF **final approach also has its own watchdog** now — it
    used to jump straight to the emit, bypassing arrival/progress machinery
    entirely, so a carrier with a blocked straight line from the goal node to
    the base steered into the obstacle forever. It now mirrors the node
    watchdog with the FLAG as the target: no strict `dsq` improvement ramps
    `wp_try` (which drives the wall-slide sweep), and a full progress-timeout
    triggers the same routing suspension.
  - **Failed-edge marker RETRY** (`route_block_hits[slot]`;
    `cfg.WP_ROUTE_BLOCK_RETRY_HITS`): the marker alone is NOT enough — live CE
    on Hydroplant Bouncefest caught the exact residual loop: marker held the
    door edge (17,15) on the only shortest path home; every arrival at 17
    routing demanded 15, the marker forced the random fallback, and 17's only
    other neighbour bounced the bot back to 18 — cur flipped 17↔18 with
    `wp_try` pinned at 0 (both nodes inside the 64px arrival radius), so no
    timeout ever fired and the marker never expired, even with the door long
    since passable. Manually clearing the marker made the bot walk through
    and capture within seconds. So: each routed arrival that is forced off
    the marked edge increments `route_block_hits`; after
    `WP_ROUTE_BLOCK_RETRY_HITS` the marker is cleared and the edge RETRIED
    (open → walks through; still blocked → the 30-frame wedge re-marks it,
    resets the budget, and the roam suspension engages). The marker is also
    cleared when a suspension expires. Hits reset on clean routed hops,
    marker re-set, reacquire, and respawn.
- Bots are kept SIMULATED when far from the host's camera
  (`cfg.BOT_FORCE_ACTIVE_ENABLED`). The engine deactivates entities far from the
  local camera, and the per-entity component advance `sub_4FADC0` gates ALL
  component updates (incl. the bot walking-controller think `sub_543B60`, which
  our `sub_542360` override rides inside) on `char->flags(+0x1C) &
  ENTITY_ACTIVE_BIT (0x800000)`. So a bot walking away from the host (carrying
  the flag home) froze mid-route until the host approached. The Active bit is
  sticky (live-verified: a cleared bit is NOT re-set per frame), so the page-flip
  detour re-sets each live bot char's Active bit every frame. BUT that is NOT
  enough on its own — breakpoint proof: the engine's update DRIVER skips far
  entities entirely. Calling only `sub_4FADC0` reaches the controller think, but
  bypasses the active-entity driver's later position sync, so the bot computes
  movement without changing `char+0x4C/+0x50`. So the page-flip ALSO
  **force-ticks** (`cfg.BOT_FORCE_TICK_ENABLED`): for each live bot the engine
  skipped this frame it mirrors `sub_57A030` for that one bot by running entity
  vtable stages `+0x7C`, `+0x80`, and `+0x8C` with `EBP=0x10000` (the `+0x8C`
  player path runs component advance, the controller think → our `sub_542360`
  → bot movement, then position sync). A per-bot 0/1/2 flag `bot_ticked` (dormant
  `bot_last_item_scan`), set by `detour_542360` when the engine ticks the bot
  and reset each page-flip, prevents double-ticking near bots; `bot_indices[slot]==0`
  (host/unused) is skipped. Both loops are cheap fixed 16-slot loops once per
  frame — do NOT hook `sub_4FADC0` itself (per entity per frame = the Windows FPS-regression hot
  path). See the `bot-far-from-camera-freeze` memory.
- **Bots are engine-native ACTIVATION SOURCES** (`cfg.BOT_PARTICIPANT_POS_
  ENABLED`, rides inside the force-active loop) — THE fundamental anti-culling
  fix, addressing the whole class of "world near a far bot is frozen" bugs
  (far team doors never opening for their own team's bot, far enemy-base flag
  never stealable, checkers asleep). Decompiled chain: the MP world update
  `sub_4F37E0` (virtual, vtable-referenced at `0x5F909C`/`0x602EA4`) walks ALL
  participants and, for each with a valid layer index at `part+0xDC`, appends
  the float pair `part+0xC0/+0xC4` as an activation POINT; `sub_4EA350` turns
  each point into a screen-sized rect (static array `dword_6C1BDC`, count
  `dword_6C1BE0`); `sub_4E74A0` collects every entity inside the union of the
  host viewport rect + all participant rects via the `sub_57A100` grid collect
  and runs `sub_57A030` on the collection. Real clients stream `+0xC0/+0xC4`
  over DirectPlay; the host's is engine-maintained; a bot's stayed at (0,0)
  forever (live-verified) so nothing near a far bot was ever simulated. The
  page-flip loop now mirrors each live bot char's `+0x4C/+0x50` into its
  participant's `+0xC0/+0xC4` each frame, and the ENGINE builds a live rect
  around every bot exactly as for a real connected player (CE-verified: the
  rect array tracked the bot mid-roam). Touch/proximity door triggers near
  bots now think and fire natively. SAFE against the checker re-arm hazard by
  construction: `sub_57A100` only collects entities whose Active bit is SET,
  so script-deactivated CTF checkers stay asleep — this path never writes any
  entity's Active bit. The force-active/force-tick loops above remain as
  belt-and-braces (they no-op when the engine ticks the bot natively).
- **Vanilla CTF rule = the checker state machine, and `flag_present[]` mirrors
  it exactly.** Every CTF-capable map (all 6 CTF maps + Hydroplant Bouncefest)
  authors a hidden `Red Checker` / `Blue Checker` `CTouchingOvalTriggerAI`
  exactly on the flag spawn anchor, whose Enter Action runs the shared canned
  object `Canned Objects/Returned a Flag` (Data.dat): same-team toucher
  carrying the enemy flag ⇒ recreate the enemy flag at ITS spawn, consume the
  carried item, `CGiveTeamAPointAction`, reactivate the ENEMY checker. The
  companion canned `Picked up a Flag` (on the flag entity's
  `CPassThroughTriggerAI`) DEACTIVATES a team's checker when its flag is
  stolen and REACTIVATES it when a same-team player touches the dropped copy
  (sequence `Not Home`) to return it; `Does player have a flag` is the
  drop-on-death script (consume carried item, spawn dropped copy at the death
  spot). A deactivated checker is never ticked, so captures while your own
  flag is away are impossible — that trigger activation IS the whole vanilla
  "own flag must be home" rule; the engine has no separate check and no
  auto-return (no exe reference to the spawn-point names). The patch therefore
  detours the two action PER-ENTITY APPLIES (`sub_4C29F0` =
  CActivateAction apply, `sub_4C2D60` = CDeactivateAction apply — vtable slot
  27, entity at `[esp+0x10]`, both funnel every execute through the by-name
  resolver `sub_41AED0`) in `detours/flag_events.py`: when the resolved target
  entity sits on a `flag_table` anchor (that entity is the checker), it writes
  `flag_present[i] = 1/0`. Zero staleness, no strings, no grid walk; flags
  start home (`load_flags` seeds 1). The OLD heuristics (2-entities-at-anchor
  presence, carried-inventory subtraction, dropped-item `+8` def-id grid
  match) were REMOVED — the world flag is a plain CEntityAnimated (unnamed in
  some maps' authored form, `New Name="Red Flag"` when script-recreated) with
  NO inventory identity, so a DROPPED flag was invisible to them and
  `flag_present` stuck at 1, which let the far-base force-tick re-arm a
  script-deactivated checker and hand out illegal captures (the "enemy scores
  while my flag lies on the ground" bug).
- CTF home base entities are also kept awake when needed. The periodic
  `scan_portal_active` grid walk caches the distinct live entities sitting
  exactly on each `flag_table` anchor in `flag_entity[]`
  (`FLAG_ENTITY_SLOTS_PER_FLAG = 3`: checker trigger, spawn marker, recreated
  flag — 2 slots could evict the checker depending on grid order), matched by
  raw entity `+0x4C/+0x50` coordinates rather than `sub_4FB0A0` because the
  getter can alias nearby visual/base pieces to the same anchor. The page-flip
  detour force-ticks (and Active-bit-arms) those cached entities only when a
  carrying bot is within `CTF_FLAG_HOME_FORCE_TICK_RADIUS_SQ` of its home base
  AND `flag_present[home]` is 1 — the event-driven gate flips the same frame a
  steal deactivates the checker, so this path can no longer re-arm a
  script-deactivated checker (the root cause of bot-only illegal captures).
  This covers the case where a far bot reaches its base carrying the enemy
  flag but capture does not fire until the host walks close enough to wake the
  base area. The scan still excludes player characters from `flag_entity[]`
  (live CE once caught a carrier standing on its base being cached and
  double-ticked). The cache carries NO presence meaning.
- CTF capture score is guarded at the score action only, as a last-resort
  backstop. `detour_5A9960` wraps `CGiveTeamAPointAction::execute` and
  suppresses the gametype score callback while the scoring team's own flag is
  away per `flag_present[]` (event-accurate) or found in any live character
  inventory (fallback for a missed event). It should never fire now that the
  checker wake-ups are gated correctly. The old `detour_5B3100` flag-USE guard
  was REMOVED and must NOT come back at that site: the drop-on-death canned
  script consumes the dying carrier's flag through the very same
  `CUseInventoryItemAction`, so a home-flag guard there cannot tell a capture
  consume from a drop consume and wrongly blocked flag drops whenever both
  flags were out (a common CTF state) — and a blocked capture chain is not
  clean anyway (the canned object's enemy-flag re-create runs BEFORE the use
  action, so blocking mid-chain duplicates flags).
- Doors are DETECTED and their open/closed state tracked live
  (`cfg.DOOR_DETECT_ENABLED`; static pipeline mirror of portals/flags):
  - **Static positions** (`door_data.py` → `world_scan.py:load_doors`, per
    match): the build-time Data.dat parse extracts every MP map's Level Parts
    carrying `Activity=CDoorAI` (10 maps / 333 doors; per-map counts pinned in
    tests against the IDA census — Curse of the Temple alone has 186, hence
    `DOOR_TABLE_MAX = 192` and the section growth). `load_doors` copies the
    active map's centers into `door_table` on match change and resets
    `door_blocked[]` + the per-bot `route_block_door[]` latches.
  - **State readback — PER-FRAME, not scan-coupled.** The periodic
    `scan_portal_active` grid walk only maintains `door_entity[]` (up to
    `DOOR_ENTITY_SLOTS_PER_DOOR`=3 non-character entities within
    `DOOR_ENTITY_MATCH_RADIUS_SQ` of each anchor, raw `+0x4C/+0x50` match like
    flags, cached REGARDLESS of solid state so an open door can be seen to
    re-close). The page-flip hook (`door_refresh_state`) then re-reads the
    cached entities' SOLID bit (`entity+0x1C & 0x40000` — set while closed,
    cleared by the open path) EVERY frame into `door_blocked[]`, flagging
    `door_dirty` on any change. Deriving state inside the walk was
    live-tested and REJECTED: the walk interval counts FRAMES, so with the
    overlay visible (low FPS) 120 frames stretched to many seconds and the
    rings looked permanently stale (toggling the overlay off restored FPS,
    let a scan through, and "fixed" it). Characters are excluded from the
    cache by the same shield as the flag-anchor cache (a bot standing in an
    OPEN doorway is SOLID but is not a door).
  - **Overlay markers**: every door draws as a small oval in the door color;
    a CLOSED door gets a second double-radius ring (in 8-bit palettized mode
    all B=255 markers share a hue, so the ring — not the color — signals state).
  - **Routing consumer 1 — door-aware failed-edge fast retry**: when the
    progress watchdog marks a failed edge, `door_capture_wedge` latches the
    nearest currently-blocked door within `DOOR_WEDGE_MATCH_RADIUS_SQ` of the
    wedged bot into `route_block_door[slot]`; the follower then clears the
    marker (and the ping-pong retry budget) the moment that door reads
    passable again, instead of waiting out the blind
    `WP_ROUTE_BLOCK_RETRY_HITS` cadence — the exact residual grind loop seen
    live on Hydroplant Bouncefest. The latch resets wherever the marker
    resets (cold-acquire, reacquire, suspension expiry, blind retry, respawn,
    match change).
  - **Routing consumer 2 — door-aware CTF rerouting with DIRECTIONAL
    passability** (`cfg.DOOR_ROUTE_AWARE_ENABLED`): the single BFS field
    always funnels a bot down the shortest path, so a bot pinned at closed
    door A never diverted when door B (an alternative route) opened —
    live-reported. `build_edge_doors` (once per match from `detour_df90`;
    doors, openers and graph are static) records per graph edge the nearest
    door within `DOOR_EDGE_RADIUS_SQ` of the edge SEGMENT into `edge_door[]`
    AND computes `edge_pass[]` — per-edge, PER-TEAM from-i/from-j bits
    (bits0-1 team0, bits2-3 team1) saying whether that team's bot could OPEN
    the door when closed. The bits come from the parsed opener topology
    (`door_data.py`): a closed door is traversable from side S for team T
    iff a BOT-USABLE opener usable by T (walk-in touching/pass-through
    trigger, authored active — self-trigger walk-up doors, one-side
    proximity volumes, arming triggers that CActivateAction the "Dooropening
    poly" pads; NOT collide switches / spawn triggers / relays / timers)
    lies on side S (sign of `dot(opener-door, node-door) + 1.0`; the bias
    makes an opener ON the door grant both sides). Opener actions are
    `COpenDoorAction` AND `CToggleDoorAction` (the Torture Chamber pillar
    walls and Doom ship light walls are switch-TOGGLED); `#a-b#` template
    targets (`lights #1-13#`) expand to the numbered door instances; openers
    wrapped in a same-team conditional (CConditionalAction whose Try is
    CIsOnSameTeamAction) are restricted to the part's `Team Number` — Doom
    ship's team doors are openable only by their own team. Doors with NO
    authored opener of any kind are engine bump-open ⇒ both sides, both
    teams; doors with only non-bot-usable openers (switch-toggled pillar
    walls, spawn doors, timer jaws) are impassable while closed until live
    state flips them. `build_flag_routes` fills TWO open fields (team-major
    `flag_dist_open`, row = team*FLAG_ROUTE_MAX + base) via the shared
    `bfs_run` body — traversing a closed-door edge only in directions that
    team can open (BFS expands u→v = bot walks v→u, so the gate tests side
    v's bit through `door_mask_i/j = 1/2 << team*2`). `ctf_next_hop` selects
    the field and masks by `bot_team[slot]` and applies the same directional
    gate on the direct step (from = cur); when the current node cannot reach
    the goal at all it falls back to the FULL field, i.e. the old
    walk-at-the-door behaviour. `door_dirty` from the per-frame refresh
    triggers `rebuild_open_routes` (both team fields; route nodes/full field
    are static) from the page-flip hook, debounced by
    `DOOR_ROUTE_REBUILD_COOLDOWN_FRAMES`. Offline-simulated on the shipped
    graphs: Hydroplant's four one-way doors classify inside-passable only;
    Torture Chamber's bases seal at 12-16/29 nodes with the pillars closed
    and open to 28/29 when one gate is switched open (the live-reported
    reroute scenario); Doom ship's team doors split by team. No new patch
    sites.
  - **PHYSICAL-STATE override** (`cfg.DOOR_ROUTE_PHYSICAL_STATE`, now default
    OFF — superseded by `BOT_PARTICIPANT_POS_ENABLED`, see below):
    the directional `edge_pass` scheme above lets a bot route THROUGH a
    closed door its team could open — but a bot far from the host's camera
    can open NO door (touch/switch triggers are camera-gated and never fire
    far away), so a team-1 carrier on Battle on the Ice committed to the
    openable door on its way home and pressed it forever (live-reported: got
    home only when the host approached and the door woke). With the flag ON,
    `bfs_run` + `ctf_next_hop` treat EVERY currently-blocked door as impassable
    (skip on `cnh_blk` alone, `edge_pass`/`door_mask` ignored) and route AROUND
    it via live `door_blocked[]`; a door is USED only once it reads open (the
    epoch reroute picks it up the frame it flips). Offline-verified: Battle on
    the Ice + Doom ship reach the enemy base around all-closed doors (138/139,
    62/64); Torture Chamber / Temple Melee / Curse stay full-field-fallback
    (walk-at-door) exactly as before because those bases are truly sealed with
    no around-path. The two team fields build identically under this flag
    (`edge_pass`/`door_mask` become vestigial but the build machinery is kept
    for flag-OFF). NOW OFF (default False): with `BOT_PARTICIPANT_POS_ENABLED`
    the world near each bot is natively simulated, so walk-up/touch door
    triggers fire for far bots and the directional `edge_pass` routing can use
    team-openable closed doors again — live-verified working ("works
    perfectly" on the team-gated doors that motivated the flag).
  - **Mid-life reroute epoch** (`route_epoch` global + `bot_route_epoch[slot]`;
    flag-route block): `ctf_next_hop` only runs on node ARRIVAL, so a bot
    committed to a full-field walk-at-the-door path (open field couldn't reach
    the goal when it last routed) never re-evaluated when a DIFFERENT door
    opened mid-edge — it stayed pinned on the old route until it died and
    respawned (live-reported on Torture Chamber; the R-snapshot showed a
    team-1 bot at node 28 with `route_use_open=0` and `current_wp=18` steering
    into the closed pillar gate while the just-opened blue gate made node 28's
    open distance finite). Fix: `rebuild_open_routes` increments `route_epoch`;
    the follower, when a routed bot's `bot_route_epoch[slot]` lags, syncs it and
    sets `current_wp=-1` so the existing cold-acquire re-runs `ctf_next_hop`
    against the freshly rebuilt open field THAT think. Both epochs reset to 0 on
    match change (`detour_df90`) so bots do not force-acquire at match start;
    debounced by the rebuild cooldown, gated on `flag_routing_active`. This is
    the deterministic complement to the per-bot suspension (whose progress
    timeout can be starved by wall-slide micro-progress at a sealed door).
  - **Closed-door commitment recovery** (`door_reroute` block at
    `s542360_wp_have_cur`): the door-BLIND commit paths (cold-acquire nearest
    node, reacquire, retreat, and the epoch cold-acquire which picks the nearest
    node by Euclidean distance) plus any next-hop taken while a door was open
    can leave a bot latched onto a node reachable only ACROSS a now-closed door.
    Only `ctf_next_hop` is door-aware, and it re-runs only on ARRIVAL — which the
    closed door prevents — so the bot grinds the door until death/respawn
    (live-reported; R-snapshots showed a bot on the PREV side of a closed pillar
    gate with `current_wp` on the far node, `route_use_open=1`, `wp_try=0`,
    `stuck=0`). Each think, if the latched `(prev,cur)` edge is bound to a
    currently-blocked door AND the bot is still nearer prev than cur (has not
    crossed), back `current_wp` up to prev and fall into the arrival/advance path
    so `ctf_next_hop` re-plans door-aware from the reachable node (offline-
    verified: 15→14 across closed door 9 re-plans 15→16; 5→6 across closed door
    15 re-plans 5→1). Fires only in that exact stuck state — door open or
    already-crossed is a no-op. NOTE: `.zaxbot` code headroom is ~2.8 KB below
    `SCRATCH_OFF` (hook_entry_size 29919 of 32768 after the portal-routing
    layer; the boundary moved 0x7000→0x8000 with the section at 0x16000).
    When it runs low again, bump `SCRATCH_OFF`+`NEW_SECTION_SIZE` together
    (build asserts on overflow). The `rstate`
    R-snapshot chunk (goal/carry/missing-policy/suspend/epoch, 0x170 B from
    `flag_routing_active`) was added for diagnosing route commitment.
- Switches are DETECTED (`cfg.SWITCH_DETECT_ENABLED`; static pipeline mirror
  of doors, parsed in `door_data.py` alongside the door topology):
  - **What a switch is**: a Level Part carrying `Activity=CollideTriggerAI` —
    the bumpable wall/floor switches. Census (2026-07-18): 116 across 16 MP
    maps, and EVERY one is `Triggered By Players=1` / `Projectiles=0` /
    `Trigger Only Once=0` / authored active — all repeatable player-BUMP
    switches (no shoot-switches in MP), so a bot fires one by steering into
    it, and with `BOT_PARTICIPANT_POS_ENABLED` that works far from the host.
  - **Classes** (`switch_static_flags` byte, `door_data.SWITCH_FLAG_*`):
    door open/togglers (0x01; TOGGLE 0x04 warns re-bump RE-CLOSES — Torture
    Chamber's 4 pillar togglers bind to ALL 43 pillar doors, Doom ship's
    light walls, Battle on the Ice team doors, Curse first/last/spike
    doors, Hydroplant's 4 one-ways, Jungle Ruins rocket/middle doors), trap
    switches (CLOSES_DOORS 0x02: Curse jaws/spikes, Temple Melee/Corridor
    "Player N door" lockouts — often also 0x01 because the trap re-OPENS via
    a delayed action), SK deposit bins (CANNED 0x08, the Greed 'Bin NN'
    parts), script relays (RELAY 0x10), PLAYER_BUMP 0x20.
  - **(switch, door) pairs**: per map, each door-opening switch is bound to
    every door instance its `COpenDoorAction`/`CToggleDoorAction` targets
    resolve to (template `#a-b#` names expanded; door indices reference the
    same map's `door_table` order). Packed as u32 `switch_idx|door_idx<<16`;
    `load_switches` (called from `detour_df90` AFTER `load_doors`) copies the
    active map's centers + class bytes + pairs into `switch_table` /
    `switch_flags` / `switch_pairs`. Per-map peaks: 19 switches (Foundry),
    158 pairs (Curse). Tests pin the full per-map census.
  - **Overlay**: every switch draws as an oval; door-opening switches get a
    second double-radius ring (same B-driven palette caveat as doors — the
    ring, not the hue, is the signal). R-snapshot chunk `switch` dumps the
    whole live block (counts + centers + pairs + flags).
  - **SWITCH-SEEK routing** (`cfg.SWITCH_SEEK_ENABLED`, flag-route block):
    when a routed CTF bot's goal is OPEN-FIELD UNREACHABLE from its node
    (sealed Torture Chamber base) or the open detour is
    `SWITCH_SEEK_SHORTCUT_GAIN`+ hops worse than the full field (a red bot
    inside the blue base with the team-gated blue door shut), `ctf_next_hop`
    requests a per-team seek. The page-flip eval (`switch_seek_eval`, ONE
    bounded BFS per frame) walks candidates — OPENS_DOORS class, ≥1 paired
    door currently BLOCKED (doubles as toggle-safety: a toggler with open
    doors is never bumped shut), bound graph node — and scores each viable
    one by the DETOUR metric `seek_walk(requester→switch) +
    full_dist(switch→goal)`, where viability = the requester's node reaches
    the switch in a team-door-gated `bfs_run` rooted at the switch node (the
    Battle on the Ice constraint: the blue-base switch is reachable for a
    red bot only from inside). Best-of-round activates (`seek_active[team]` =
    idx+1, field re-built for the winner); participating bots
    (`bot_seek[slot]`, re-earned each arrival) descend `seek_dist[team]` and
    FINAL-APPROACH the switch center at its node to physically bump it. The
    opened door then flows through door_dirty → `rebuild_open_routes`, which
    CLEARS all seek state (stale by definition) and bumps the epoch — bots
    re-request if still blocked, which also CHAINS seeks (Torture: a sealed
    blue bot first opens its own base's toggler, escapes, then the next
    round handles the far side). A seek with no door change for
    `SWITCH_SEEK_TIMEOUT_FRAMES` blacklists that switch until the next
    door-state change. Offline-verified on the shipped graphs (pinned in
    tests): sealed Torture bot → own-base toggler (3-hop walk; after it, the
    route to the enemy base is fully open); red-at-blue-base carrier → the
    blue-door switch 1 hop away, NOT the red-door switch across the map.
    Known noise: an attacker near its own base may bump a near switch whose
    door its team could open anyway (harmless 1-2 hop detour; the opened
    door excludes the switch from the next round).
- General world-entity enumeration (`detours/entity_scan.py:scan_entities`,
  gated by `cfg.SCAN_ENTITIES_ENABLED`). The long-standing blocker for object
  detection was that there is no flat entity list: `mgr+0x290` is players,
  `mgr+0x2BC` is the LAYER list (count `mgr+0x2C0` == 1 in MP). Real entities
  (triggers, switches, doors, flags, collectors, pads, pickups, hazards) live one
  level down, inside each layer's **spatial grid**. The walk (decompiled from the
  engine's by-name finder `sub_57A7E0`, live-validated): `mgr` → `[[mgr+0x2BC]]`
  = active `CLayer` (vtbl `0x5F8BAC`) → grid at `layer+0x50` (rows@+0x60,
  cols@+0x64, cells@+0x68); each 16-byte cell is `[vtbl 0x600A90, list@+4,
  count@+8]`; entity = `list[k]`. An entity straddling cells is de-duplicated via
  the engine's own visit-id protocol (bump `dword_622200`, stamp `entity+0x2C`,
  skip if already `>=`). `scan_entities` reads `scan_class_desc` (0 = every
  entity, else a class descriptor matched with `sub_416790`) and writes
  `(ptr, x, y, flags)` records into `scan_table` (flags = `entity+0x1C`; Active =
  `ax.ENTITY_ACTIVE_BIT` = `0x800000`, set by `CActivateAction`/cleared by
  `CDeactivateAction`, e.g. Jungle Ruins' two-lock key puzzle gates the
  teleports). `scan_diag` runs it once per match from `detour_df90`. This is the
  foundation for: per-portal active-state (does the bot route to this pad?),
  switches/doors/CTF flags/SK collectors/trap zones, and un-dormenting the
  hazard/pickup scans. The walk is bounded (`rows*cols` cells, 256 entities/cell,
  both capped).
  - **First consumer — `scan_portal_active`** (gated by `cfg.PORTAL_ACTIVE_ENABLED`):
    the same grid walk, but instead of a capped collect-table it matches every
    entity against `portal_table` and keeps the NEAREST one's Active bit in
    `portal_active[i]` (1 = the pad nearest portal i is currently usable). Immune
    to `SCAN_ENTITIES_MAX` (no table), so it reaches the teleporter pads wherever
    they sit in the grid — the class=0 `scan_diag` table fills in the low-Y region
    and truncates before the high-Y pads, which is why a position-matched consumer
    (not a collect-table) is the right tool here. The page-flip detour re-runs it
    every `PORTAL_ACTIVE_SCAN_INTERVAL` frames (countdown seeded to 1 on match
    change by `detour_df90`) so the flag tracks dynamic activation/cooldown — e.g.
    Jungle Ruins' two-lock key puzzle flips the pads from inactive to active
    mid-match. Bots gate portal routing on `portal_active[i]`. This is the only
    periodic (not per-frame-body) scan cost.

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
- `0x5693A0` - toggleable visual waypoint authoring overlay before page flip;
  also re-sets each live bot char's Active bit every frame
  (`cfg.BOT_FORCE_ACTIVE_ENABLED`) so the engine keeps simulating bots when they
  walk far from the host's camera (see below).
- `0x53DA40` - gated pickup self-registration for overlay item markers.
- `0x4C11A0` - teleport-portal self-registration (gated by
  `cfg.PORTAL_REGISTER_ENABLED`); records each `CTeleportAction` warp's source
  pad into `portal_table`.
- `0x4C29F0` - CActivateAction per-entity apply; flag-home event (sets
  `flag_present[i]=1` when the activated entity sits on a flag anchor —
  the base checker). Gated by `cfg.CTF_FLAG_EVENTS_ENABLED`.
- `0x4C2D60` - CDeactivateAction per-entity apply; flag-away event
  (`flag_present[i]=0`). Gated by `cfg.CTF_FLAG_EVENTS_ENABLED`.
- `0x5A9960` - CTF score action guard; last-resort backstop that blocks capture
  awards while the scoring team's own flag is away/carried. (The old
  `0x5B3100` flag-use guard was removed — it broke flag drops; see above.)
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
| `sub_5B3100` | `CUseInventoryItemAction::execute`; consumes the carried enemy flag in the capture chain AND the dying carrier's flag in the drop-on-death canned script — the two are indistinguishable here, so this site must NOT carry a home-flag guard (the old detour was removed for breaking drops). |
| `sub_5A9960` | `CGiveTeamAPointAction::execute`; map-script score action. Original body only calls active gametype vtable[+0x68](team, 1); detoured as a last-resort backstop to enforce own-flag-home before awarding CTF capture points. |
| `sub_4C29F0` / `sub_4C2D60` | CActivateAction / CDeactivateAction PER-ENTITY apply (vtable slot 27; entity at `[esp+0x10]`, `ret 0x10`; sets/clears `entity+0x1C & 0x800000` via entity vtbl `+0xE8`/`+0xEC`). Detoured for event-driven `flag_present[]`: the map scripts express "own flag home" as the base checker's activation. Executes (slot 23) funnel through the by-name multi-target resolver `sub_41AED0`. |
| `VT_CACTIVATE_ACTION_VA = 0x5F6374` / `VT_CDEACTIVATE_ACTION_VA = 0x5F63E4` | the two action vtables (each apply is reachable only through its own vtable). |
| `"Red Checker"` / `"Blue Checker"` | per-map base touch trigger (CTouchingOvalTriggerAI) authored exactly on the flag spawn anchor; Enter Action = canned `Returned a Flag` (the capture chain). Deactivated while that team's flag is away. Names verified on all 7 CTF-capable maps. |
| `"Red Flag"` / `"Blue Flag"` | inventory item definition names resolved through `sub_523DF0(dword_6C0C08, name, -1)`; compare against carried `CInventoryItem + 8` to know which exact flag is carried. |
| `"Multiplayer Flag"` | inventory group name resolved through `sub_591FC0(dword_6C0800, name, -1)`; `sub_425290(inv, [0x714454]) != -1` means the character carries any CTF flag. |
| `sub_4C11A0` | relocate/teleport executor (`__thiscall`: `ecx`=action, `[esp+4]`=entity at SOURCE pos; reads dest via `sub_4F4AC0` later). The chokepoint every `CRelocate`/`CTeleportAction` funnels through (both override execute vtable slot 27 with `sub_5A5A60` → `sub_4C1060` → here). Detour site for runtime portal detection. |
| `CTeleportAction vtbl = 0x6033B0` | genuine warp teleporter (parent chain `CSwitchMapAction 0x6032C4` → `CRelocateAction 0x603338` → `CTeleportAction`); the portal detour filters `[action]` to this. |
| `CTouchingPolygonTriggerAI vtbl = 0x5EDC20` / `CTouchingOvalTriggerAI = 0x5EDB58` | touch-trigger volumes (Enter Action @+0x20, Exit @+0x24, Repeat @+0x28; "Triggered By" filter bytes @+0x10..+0x14). Portals can be a trigger's Enter Action OR a script/event-driven `CTeleportAction` referenced by name — hence runtime execute-hooking instead of per-map structure walking. |
| `sub_4FB0A0` | `__thiscall(char/entity, &out_pos)` world-position getter, `ret 4` |
| `sub_4F37E0` | MP world update (virtual): builds one activation POINT per participant from `part+0xC0/+0xC4` (layer idx gate at `+0xDC`) |
| `sub_4EA350` | point list → screen-sized activation rects (`dword_6C1BDC` array, `dword_6C1BE0` count) |
| `sub_4E74A0` | layer update driver: `sub_57A100` grid-collects Active entities in viewport+participant rects, `sub_57A030` updates the collection |
| `participant + 0xC0/+0xC4` | participant "last known position" floats — the activation point; bots' mirrored per frame from `char+0x4C/+0x50` (`BOT_PARTICIPANT_POS_ENABLED`) |
| `participant + 0xDC` | participant layer index (-1 = not in world) |

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
6. `fnstsw ax` OVERWRITES AX. In any FPU-compare loop (`fcomp` +
   `fnstsw ax; sahf`), EAX must be dead or saved around the readout —
   `build_edge_doors` once kept the vertex pointer in EAX through it, so
   every door after the first was measured against a corrupted pointer and
   only 1 of 8 Torture Chamber gate edges bound (bots ignored door state;
   found via the `door_*`/`edge_*` R-snapshot chunks, fixed with
   `push eax / fnstsw ax; sahf / pop eax` — pop preserves EFLAGS). Prefer
   `fcomip` (sets EFLAGS directly, no AX) when the stack order allows.

## Open work

- Smoother wall handling: confirm via an `ai_move` R-dump that the controller
  block vector (`+0x14/+0x18`, now mirrored into `bot_wander_x/y[slot]`) is
  populated for bots near walls, then add a geometric slide — project the
  desired heading onto the wall tangent `s = desired - ((desired·B)/|B|²)·B`
  and steer along `s` — on top of the existing angle sweep so the bot tracks
  walls without the brief sweep jitter. (The sweep is the guaranteed fallback.)
- Graph authoring tools / coverage: place nodes at corners and junctions so
  the straight node-to-node segments stay in walkable space (corner-cutting is
  what triggers the wall-slide). Consider auto-densifying long edges.
- CTF dropped-flag pursuit on top of the event-driven away/home resolver:
  `flag_present[]` is now exact for stolen AND dropped flags (checker
  activate/deactivate events); attackers randomly search or wait near that
  base, while carriers with a missing home flag search instead of touching the
  empty capture base. The patch still does not route to the dropped position
  (the dropped copy is a script-created `CEntityAnimated` named
  `Red Flag`/`Blue Flag` with sequence `Not Home` — findable in the grid by
  name+sequence if pursuit is ever added). The old "attacker at a far ENEMY
  base cannot steal until the host wakes the area" gap is CLOSED by
  `BOT_PARTICIPANT_POS_ENABLED` (the bot's own activation rect keeps the
  enemy-base flag + PassThrough steal trigger simulated). SK bots still
  need collector-aware return paths.
- Reintroduce hazard/pickup awareness as GRAPH-AWARE routing (route through
  nodes near pickups, around lava) rather than the removed vector-field
  perturbation that pushed the heading into walls.
- Portal routing — DONE for build-time-resolvable destinations (see the
  portal-routing bullet in "Current state"): pads are directed BFS edges, CTF
  bots route through them (Hydro Vengence cross-arena flag runs), roaming/DM
  bots occasionally wander into them, and any per-think position jump >192px
  cold-reacquires the nearest node. Remaining: destinations for RUNTIME-only
  portals (Jungle Ruins' script "Upper"/"Lower" and anything only
  `detour_4C11A0` sees) — capture the exit position just after `sub_4F4AC0`
  (or read `action+0x08/+0x0C`) and write it into `portal_dest_table` +
  `portal_has_dest` so those pads graduate from wander-only to routed edges
  the first time something teleports; those are DM-only today, so nothing
  currently routes through them anyway.
- Populate or hook DirectPlay player data so PC2 sees chosen bot names
  (and team colors in CTF/SK).
- Door awareness — DETECTION + DIRECTIONAL REROUTING DONE (see the door
  bullet in "Current state" and the `door-runtime-model` memory): static
  positions + opener topology from Data.dat, per-frame open/closed via the
  cached-entity SOLID readback, overlay markers, failed-edge fast retry, and
  door-aware CTF rerouting (directional open-field BFS with full-field
  fallback; one-way doors traversable only from their opener side).
  "Bots open far doors" is now handled the right way: with
  `BOT_PARTICIPANT_POS_ENABLED` each bot is an engine-native activation
  source, so walk-up/touch/proximity door triggers near a far bot think and
  fire exactly as if a real player walked up (no message sender needed).
  SWITCH-SEEK is DONE (see the switch bullet in "Current state"): sealed or
  door-shortcut-blocked bots route to the best reachable door-opening switch,
  bump it, and the door_dirty/epoch machinery re-plans through the opened
  door — chaining across rounds. Remaining refinements: the final-approach
  wedge (goal node reachable but the last straight leg to the flag blocked
  by a pillar) does not trigger a seek directly, only the wedge/suspension
  roam; and the pair-blocked candidate filter could additionally skip doors
  the bot's own team can already open per `edge_pass` (minor attacker-side
  detour noise). Do NOT blanket-wake door triggers near bots (Active-bit
  forcing) — same hazard class as the checker re-arm bug; the
  participant-rect path is safe because the grid collect masks on the
  Active bit.
