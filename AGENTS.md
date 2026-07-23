# AGENTS.md - Zax bot-support binary patch

This project adds host-side bot support to *Zax: The Alien Hunter*
(Reflexive Entertainment, 2001) by runtime-patching `Zax.exe`. The game is a
fullscreen-DirectDraw, single-thread C++ program with working DirectPlay
multiplayer but no built-in bots.

All addresses in `zaxbot/addresses.py` and this document are verified against
one specific build: the **Polish release, version 1.1, build 1309** (in-game
version string `Zax Wersja 1.1 Kompilacja 1309`). Untested on other language
releases or build numbers — a different build may lay out `Zax.exe`
differently, which would invalidate the hardcoded VAs and prologue bytes
throughout `addresses.py`.

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
  - `config/` - per-feature knob modules (`movement`, `door`, `sk`, ...);
    the `__init__` facade re-exports everything, so `cfg.X` works for every
    knob. Put a new knob in the module owning its feature.
  - `layout/` - scratch-field layout: `model.py` (field classes + per-bot
    tables), per-feature block modules, `builder.py` (assembles the layout
    in offset-load-bearing order), `from_config.py` (production capacities
    from `cfg`).
  - `static_data/` - build-time scratch packing: `common.py` (prompts/tags/
    name+color writers), `tables.py` (per-feature static tables),
    `scratch.py` (`write_static_scratch_data`), `from_config.py` (the
    cfg->kwargs mapping used by `hook/entry.py`).
  - `patch_manifest.py` - enabled redirects into `.zaxbot`.
  - `portal_data.py` / `flag_data.py` / `door_data.py` - build-time Data.dat
    parses (teleporters+routes, CTF flag anchors, doors/openers/switches).
  - `sk_data.py` - build-time Data.dat parse of SK minerals + team-bound bins.
  - `item_data.py` - build-time parse of filler items (health/energy/shield).
  - `hook/` - dispatcher, mode detection, spawn, snapshot, `bot_menu.py` (the
    B-key graphical bot menu built from the engine widget tree); `entry.py` is
    the build orchestrator (emit order is load-bearing).
  - `detours/` - one module per detour. The three big ones are packages
    split per stage/domain, each preserving the original emit order:
    - `world_scan/` - per-match world-data loaders + periodic scans
      (`portals`, `flags`, `doors`, `plasma`, `switches`, `sk`, `items`,
      `goody`, `hazard_pickup`, `mines` — `load_mine` + the `mine_tick`
      placement pass).
    - `flag_route/` - routing: `_ctx.py` (shared VA/gate context),
      `fields` (BFS/SPFA builders), `goal`, `next_hop`, `seek`, `drop`,
      `sk_routes`.
    - `bot_movement/` - the follower: `follow_ctx.py` (shared context),
      `setup`, `divert`, `follow_entry`, `follow_pursuits`,
      `follow_recovery`, `follow_final_approach`, `follow_watchdog`,
      `follow_arrive`, `follow_steer`, `vector_emit`, `tuning`.
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

Historical Linux/Wine results are still useful but not definitive. The severe
Windows-only low-FPS regression with the overlay visible (2-6 FPS on Windows
11, no drop under Wine) was ROOT-CAUSED and fixed 2026-07-23: every CGraphics
primitive self-locks the DirectDraw back buffer per call when the global lock
flag `dword_713318` is clear (`sub_568D90`: Lock → one line → Unlock — and an
oval is 10-25 line segments), and the overlay pass runs at the page flip AFTER
the engine's own frame-wide lock bracket (`sub_40F5F0`: `sub_567BB0` lock →
render → `sub_567C90` unlock) has released it — so every overlay primitive
paid a full Lock/Unlock, a multi-ms GPU/GDI sync on native Windows but a
near-free system-memory pointer fetch under Wine. `detours/overlay.py` now
mirrors the engine's bracket: one `sub_567BB0` before the draw pass (every
primitive then takes the locked fast path), one `sub_567C90` before resuming
into the flip — the unlock is LOAD-BEARING, `sub_5693A0` refuses to Flip
while the flag is set and the windowed Blt would fail. Any FUTURE draw pass
added to a hook must batch the lock the same way. The visual waypoint
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
- `.zaxbot`: VA `0x71A000`, raw `0x231000`, size `0x2A000`, RWX (grown from
  `0xA000` for the CTF flag static tables, then to `0xC000` for the CTF routing
  BFS distance field, then to `0xD000` for far-base CTF flag entity force-ticks,
  then to `0xF000` for the door detection static tables, then to `0x11000` for
  the door-aware routing field + anchor-entity cache, then `0x12000` for its
  per-team split, then to `0x14000` for the switch detection tables, then to
  `0x16000` for the portal routing layer, then to `0x18000` for the
  dropped-flag pursuit layer, then to `0x26000` for the SK layer — 1856
  static mineral anchors, team-indexed bin tables, the mineral field + 16
  per-bin BFS rows, and the 512-slot pickup table — then to `0x27000` for
  the graphical bot menu, then to `0x28000` for the enemy-carrier chase
  layer — sighting intel + per-flag chase BFS rows — then to `0x29000` for
  the proximity-mine layer's code room, then to `0x2A000` for the
  weapon-pickup goody layer).
- Scratch starts at `0x726000` (`SCRATCH_OFF = 0xC000`; the code/scratch
  boundary moved from `0x5A00` at the door layer, from `0x6800` at the switch
  layer, from `0x7000` at the portal routing layer, from `0x8000` when
  the dropped-flag ROUTED pursuit landed with ~456 code bytes left, from
  `0x9000` at the SK layer with ~3.0 KB left, from `0xA000` at the
  bot-menu layer, then from `0xB000` at the proximity-mine layer).
- B opens a GRAPHICAL bot menu (`build_bot_menu` in `zaxbot/hook/bot_menu.py`),
  built from the engine's own widget tree exactly like the in-game Esc quit
  dialog (base `CWindow` vtable `0x5EAAC4` cloned into scratch with slot 0 =
  `menu_dtor` and slot 21 = `menu_cmd` overridden; parent = the DESKTOP ROOT
  `*(dword_6C02CC + 0x34)` = `sub_4CDF30(uimgr)`, NOT `*dword_713F14` — that is
  the `CGame` world manager, not a `CWindow`, and using it as a widget parent
  crashes `sub_40C6E0`). It shows a title bar with the engine's NATIVE
  top-right X close box (`sub_4038A0` → `dialog+0x100`, glyph `0x18`,
  auto-re-anchored top-right by the base set-rect on every resize; also stored
  at `dialog+0x120` so the base key handler `sub_403E40` case 27 presses it on
  **Esc**) + a vertical button stack (anchor 12): DM/SK a single **Add Bot**,
  CTF **Add Blue Bot** + **Add Red Bot**, plus a **Close** button. A final
  alignment pass grows the window to the widest button (anchor 12 centers
  against the ADD-time client width and `sub_40E590` only ever grows, which
  left the widest button clipped off the left edge) and re-centers each button
  via `sub_40D680`, the engine dialogs' own post-add reposition. The dialog's
  command handler (`menu_cmd`) maps a button to `chosen_team` +
  `do_spawn_with_team` (menu stays open, so several bots can be added), or
  closes via vtable slot 5 (Close button, X box, or Esc); `menu_dtor` resets
  the `menu_open` guard on every teardown. The old on-screen text prompt +
  digit-key selection state machine was removed (its `menu_state`/
  `prompts_table`/`max_for_mode`/`prompt_*` scratch fields are now vestigial
  reserved space). See `docs/02-keyboard-and-message-pump.md`. R writes a
  runtime snapshot.
- Spawn injects a synthetic DirectPlay "player added" queue entry at
  `dpmgr + 0x44D`, calls `sub_480800(ecx=dpmgr, edi=host_char)`, reads the
  participant from `[queue_slot + 7]`, clears the queue entry, then calls
  `sub_59DF90(mgr, a2, botidx, 0, 0)` to create and place the character.
- Bots are real remote-classified participants: visible character, scoreboard
  entry, damage/death, kill registration, and PC2 visibility work.
- Bot display names are set on host through `sub_4E1930(*(part+0x1C), name)`.
  PC2 does not reliably see the chosen name because the synthetic DirectPlay
  player-data store is not populated.
- A spawned bot is announced EXACTLY like a real player joining: the spawn
  success path mirrors the host-side join handler `sub_5AC230` — CString-slot
  init (`sub_4DEC90`), `sub_4E09B0(&slot, fmt, name)` sprintf with the LIVE
  `"%s joined the game"` global CString (`0x71407C`, registered by static
  initializer `sub_5AC1C0` into the localization-replaceable string list, so
  translated builds keep their wording), `sub_59B260(text, -1)` broadcast,
  then release (`sub_4DEFD0`). Falls back to the generic scratch msg when no
  participant was bound. Constants in the CString block of `addresses.py`;
  slot = `join_msg_cstr` at the layout tail.
- Each bot name owns a deterministic `(color1, color2)` pair from
  `BOT_COLORS` in `zaxbot/config/spawn.py`. Coloring is split across two phases:
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
  and fall back to DM. `zaxbot/config/spawn.py` exposes a `FORCE_MODE` knob for
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
  - **Wedge-cluster HARD RESET** (`bot_wedge_cycles[slot]` +
    `wp_find_nearest_ex` + `wpfn_excl[4]`; `cfg.WP_WEDGE_RESET_CYCLES`,
    R-chunk `wedge`): the progress-timeout recovery is LOCAL — alternate
    neighbour of prev, retreat swap, or Euclidean-nearest reacquire — and a
    bot on the WRONG SIDE of a wall/door whose latched nodes sit across it
    cycles those forever (live 2026-07-20, Battle on the Ice snaps 1-3: a
    team-1 bot north of the closed south team door held prev=78 with cur
    flipping 77↔47 — all three nodes across the entrance wall; node 78 is ON
    the entrance line so its 64 px arrival ball pokes through the wall and
    "arrives" bots from the wrong side, after which the door-aware machinery
    disengages because the latched in-base edge carries no door — while the
    genuinely reachable around-route entry (node 48, team-1 open field into
    the base is finite via the east side) was never tried). Every recovery
    action (alternate taken, retreat, fresh-nearest reacquire) increments the
    per-bot counter; a NORMAL-radius node arrival resets it. STUCK-radius
    arrivals (the 128 px `WP_STUCK_REACHED_RADIUS_SQ` ball) deliberately do
    NOT reset it since 2026-07-20: they jump to `s542360_wp_arrived_gate`
    (the label just past the reset, still through the door-side gate) —
    live Ice snapshots caught a blue CARRIER north of the wall "arriving" at
    in-base node 78 from 123 px through the wall every few seconds, zeroing
    the counter, so the hard reset below never fired and the bot ground the
    wall until the suspension turned it into a roamer. At
    `WP_WEDGE_RESET_CYCLES` (or when the keep-sweeping same-nearest state
    exceeds 4 timeout windows) the follower cold-acquires the nearest node
    EXCLUDING the wedge cluster — failed cur, prev, and the failed-edge
    marker's two nodes — via `wp_find_nearest_ex` (FLT_MAX-seeded scan
    skipping `wpfn_excl[0..3]`). The failed-edge marker is KEPT through the
    reset as wedge memory so consecutive resets widen the exclusion set. On
    the live geometry the first reset excludes {47,77,78} and picks 48
    (pinned in tests).
  - **Door-side ARRIVAL GATE** (`s542360_wp_arrived_gate` in
    `follow_arrive`, gated by the door tables; reuses
    `DOOR_WEDGE_MATCH_RADIUS_SQ`): EVERY node arrival — normal, stuck,
    prev-swap, cdr re-plan — is refused when the arrived node lies within
    the wedge radius of a currently-BLOCKED door AND the bot is across that
    door (`dot(bot − door, node − door) < 0`); the refusal falls into the
    no-progress path (ecx=slot → `s542360_wp_no_progress_popped`) so the
    bot keeps steering at the node — i.e. INTO the door, which is what
    fires its walk-up opener — with the watchdog/door-press-patience
    machinery armed. Root cause fixed (live 2026-07-20, "blue carrier
    grinds the wall at the south team gate"): node 47 sits 30 px behind
    Ice's door 1, so the 64/128 px arrival ball claimed arrival while the
    carrier was still OUTSIDE the closed door; the next hop then targeted
    an in-base node (snap: prev=47 cur=78) whose straight line from the
    bot's REAL position crossed the wall west of the doorway — permanent
    grind, then suspension roam. The gate is inert the frame the door
    reads open (per-frame SOLID refresh), so legit arrivals through an
    open door are untouched; nodes beyond the wedge radius (78 at 155 px)
    stay ungated — their cross-wall stuck-arrivals are the hard reset's
    job (which now escalates, see above). Predicate + live geometry pinned
    in `test_door_side_arrival_gate_on_battle_on_the_ice`.
    **Companion — node-gate PRESS-PATIENCE latch** (`door_capture_node_gate`
    in `world_scan/doors.py`, called from the routed-timeout path): the
    press-patience branch used to engage only when `door_capture_wedge`
    found a blocked door within the wedge radius of the BOT — but the
    first live pass of the arrival gate (2026-07-20 follow-up snaps)
    caught the carrier timing out while grinding the wall 136 px WEST of
    the doorway (target 47 latched, try=28): no bot-near latch, so the
    recovery alternated onto cross-wall node 78 and armed the suspension
    (the residual "sometimes stuck"). Now, when the bot-radius capture
    finds nothing, the timeout latches by the ARRIVAL-GATE predicate
    instead (target node within the wedge radius of a blocked door + bot
    across it) and presses with fresh watchdog cycles — the wall-slide
    walks the bot along the wall into the doorway/trigger (live: the
    slide moved it 80 px toward the door in one window; productive
    sliding resets `wp_try`, so patience is only consumed while truly
    pinned). Patience also refills on every NORMAL-radius arrival (the
    node-gate path exercises it far more than the old bot-at-door case,
    so a stale count must not starve the next door). Wrong-team bots
    exhaust `WP_DOOR_PRESS_PATIENCE` and fall back to today's
    suspension/alternate recovery. Pinned in the same test.
  - **Fight-stall suspension skip** (`bot_enemy_near[slot]`,
    `cfg.FIGHT_STALL_RADIUS_SQ`): the fire detour's `pick_target` stamps a
    per-bot flag when the chosen target is within 240 px; while set, a routed
    progress-timeout skips ARMING the routing suspension (markers, alternates
    and the hard reset still run). Reason (user-reported 2026-07-20): combat
    knockback/body-blocking stalls progress, and the suspension made
    `ctf_pick_goal` report no goal — so a flag CARRIER roamed randomly
    mid-fight instead of pressing toward its base. The stamp is cleared each
    frame at the top of the fire detour and skipped on force-tick recovery
    frames (far bots keep today's suspension behaviour).
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
- Bots have PER-NODE MOVEMENT DIVERGENCE (`cfg.WP_DIVERGE_ENABLED`; built
  2026-07-23, live pass pending — the "player-like looseness" layer). Every
  waypoint node carries a level byte in `wp_node_level[]` (layout/diverge.py
  tail block): 1 = STRICT (byte-equivalent to the old behaviour — edge-hug
  steering, 64 px arrival; the safe default for narrow lava paths), 2 =
  LOOSE, 3 = FREE. The level of the node a bot is APPROACHING drives three
  things in the follower: (a) `follow_steer` skips the prev→current
  EDGE-HUG for level >= 2 and steers straight at the node; (b) the straight
  steer targets node + a PER-BOT lateral offset (`bot_div_x/y[slot]`,
  re-rolled via `wp_div_roll` whenever `bot_div_node[slot]` != cur+1 —
  self-healing, plus a df90 clear so a stale roll can't alias across match
  change), uniform in ±`WP_LVL2/3_OFFSET_MAX` px (44/64) so pack-routed
  bots fan out over the same route; (c) the arrival test loads
  `wp_lvl_radius_sq[level&3]` (64/88/112 px) instead of the flat
  `WP_REACHED_RADIUS_SQ` — freer nodes corner-cut earlier. Offsets stay
  well inside the same level's arrival ball (test-pinned: off·√2 < radius)
  so the dsq-to-NODE progress watchdog and arrival machinery are
  unaffected, and all radii stay below the 128 px STUCK ball whose
  wedge-exempt arrival semantics remain a distinct tier. Levels are edited
  in-game: J-select a node, then +/- (main row or numpad; overlay+MP gated
  like every editor key) with on-screen confirmation, and the overlay draws
  a small line-drawn digit (glyph segment table `wp_lvl_glyphs`, packed at
  build time) under every node. Persistence: `.zwpt` VERSION 2 appends one
  level byte per vertex after the edges; the loader accepts v1 (levels
  default to all-1 strict) so existing graphs load unchanged, `wp_load`
  default-fills + sanitizes (clamp to 1..3), `wp_drop` inits new nodes to
  1, `wp_delete` mirrors its swap-with-last in the level table. Final
  approaches (flag/pad/switch/drop/bin) are untouched — divergence only
  shapes node-to-node travel.
- Shot prediction is fully wired. `compute_proj_speed` reads the active
  weapon's projectile speed from `[CModel + 0x60]` via
  `sub_48D8F0(dword_6CFDD8, [def + 0x20])`; NULL projectile key or zero
  velocity ⇒ `is_hitscan` (Semi Auto Pistol, Alien Electrical Weapon).
  `apply_lead` solves the exact intercept quadratic with muzzle-offset
  compensation (`cfg.MUZZLE_OFFSET = 20px`); `bot_fire_aim` rolls
  `cfg.LEAD_PROBABILITY` (default 0.5) per shot to mix prediction with
  straight-shooting for a less robotic feel.
- `zaxbot/config/spawn.py` can force newly spawned bots to equip an inventory item
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
    graph: arenas disconnected without pads, enemy base reachable with them,
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
  the grid scan anymore. DROPPED flags are now a pursuit target — see the
  dropped-flag pursuit bullet below.
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
    then a WEIGHTED shortest-path pass (`bfs_run`, SPFA — queue-based
    Bellman-Ford over the UNDIRECTED edge list with per-node `bfs_inq`
    dedup flags and a VMAX ring queue) fills `flag_dist[base][node]`
    (`0xFFFFFFFF`=unreachable). DISTANCES ARE PHYSICAL LENGTHS in
    `WP_EDGE_LEN_QUANTUM` (16 px) units, not hops: `build_edge_lens` (per
    match from `detour_df90`, after `wp_load`) quantizes each edge's pixel
    length into `edge_len[e]` (min 1, so next-hop descent stays strict),
    and every `bfs_run` field (full, per-team open, seek, drop rows) adds
    it per edge. Hop counting was live-refuted on Hydroplant Bouncefest:
    the through-door route and the around-the-top route TIE at 9 hops but
    differ by 681 px (1899 vs 2580), so hop routing — and the seek benefit
    gate — saw zero gain from opening the switch-doors (the user-reported
    "bots don't know the switch shortens the path"). Teleport pads keep
    relax cost 1 (near-free, strongly preferred). Arms
    `flag_routing_active`.
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
    STANDOFF-TETHER near their base (`cfg.CTF_CARRIER_STANDOFF_ENABLED`,
    2026-07-21; shares the defender role's `cpg_tether`/`defend_radius`
    machinery): goal = HOME while beyond the map-scaled radius, no goal
    inside it — the carrier hovers at its base ready to capture the instant
    its flag returns, instead of search-roaming the whole map
    (user-reported). `route_missing_goal[slot]` is still written, so the
    dropped-flag pursuit's any-distance latch still routes the carrier to
    its home flag when that lies DROPPED. The inside-radius flip (goal-node
    dist 0) preserves the old invariant: a carrier is never
    routed/final-approached into an empty home base, and the home
    force-tick stays off via its `flag_present[home]` gate. With the knob
    off, the old unconditional search mode. The policy clears when the flag
    becomes present again or the bot switches goals. Do NOT add
    BFS/pathfinding for an unknown flag location.
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
    clears it. EXCEPTION: while `bot_enemy_near[slot]` is set the timeout
    does NOT arm the suspension (see the fight-stall bullet in the follower
    section — a combat stall must not turn a carrier into a random roamer).
    The CTF **final approach also has its own watchdog** now — it
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
- CTF bots have ALTERNATING ATTACKER/DEFENDER roles
  (`cfg.CTF_DEFENDER_ENABLED`; built 2026-07-21, live-confirmed working the
  same day; the carrier STANDOFF tether landed right after and its live
  pass is pending):
  - **Assignment** (spawn side, `hook/spawn.py` — at the SUCCESS TAIL, just
    before the join-message broadcast, gated on `botp != 0`): the role is
    derived from the LIVE team composition — `bit0 = (living same-team
    bots) & 1`; a team's 1st bot attacks, its 2nd defends, 3rd attacks, ...
    Teams count independently (one blue + one red bot are both attackers).
    It was originally a raw per-team ATTEMPT counter at the team write, but
    R snapshots (2026-07-22, four sessions) caught `role_spawn_count` 6
    increments ahead of the living bots every session: adds that failed
    late (session/team full) consumed parity, so a failure landing between
    two successes gave a team A,A — the reported "more defenders on one
    team, more attackers on the other, tracking which team I play" (the
    human's team fills first, so its adds fail differently). Live-count
    also self-heals across any drift. `role_spawn_count[2]` remains as a
    success-only diagnostic. `bot_role[slot]` is a BIT FIELD: bit0 =
    defender (consumers test the bit, not the whole value), bit1 = the
    attacker's ROUTE LANE — `(living same-team attackers >> 1) & 1` at
    spawn, i.e. attacker ordinals 0,1 -> lane 0, 2,3 -> lane 1 (see the
    route-lane bullet below). Non-CTF spawns are always role 0; the role
    gates ONLY CTF goal selection, so DM/SK are untouched.
  - **Defender behavior** (`ctf_pick_goal` in `flag_route/goal.py`, entered
    on the not-carrying path): while the bot's current node lies within
    `defend_radius[home]` of its OWN base in the full `flag_dist` field it
    reports NO goal — the follower's random `wp_advance` roams it around the
    base; beyond the radius the goal flips to the HOME base and the normal
    routing machinery (door-aware fields, portals, seek) walks it back in.
    ctf_pick_goal runs per frame, so this is a self-correcting tether: a
    roam step out of the zone is pulled back at the next arrival, and the
    patrol naturally covers the base region. The branch BYPASSES the
    flag_present/missing-policy machinery (a defender guards home
    regardless of flag state — its own stolen flag is exactly when home
    matters), and since the goal node's own distance is 0 (always inside),
    a non-carrier can never final-approach the flag. A CARRYING defender
    takes the untouched carrier path (route home, capture/return) — so a
    defender that opportunistically grabs a drop near its base (the 350 px
    dropped-flag latch has no team/role filter) still delivers/returns it.
    Defenders skip the roam PORTAL-WANDER roll (`follow_arrive`): a pad exit
    is usually far outside the patrol zone, so the coin would just bounce
    them out and back. The switch wander-bump stays enabled (local, useful).
    The tether itself is the shared `cpg_tether` helper (eax = base idx →
    eax = base-or-−1 by the radius test) — the carrier STANDOFF (see the
    routing bullet above) is its second caller, and CARRIERS on the roam
    fallback also skip the pad roll (gated on `flag_routing_active` so the
    stale `route_carry` global can never eat the DM/SK pad coin).
  - **Per-map radius** (`build_flag_routes` at `bfr_next`, per base):
    `defend_radius[i] = max(CTF_DEFEND_RADIUS_MIN, max_finite(flag_dist[i])
    * CTF_DEFEND_RADIUS_PCT / 100)` — the map's span AS SEEN FROM THAT BASE
    (max finite BFS distance, `WP_EDGE_LEN_QUANTUM` units), so bigger maps
    give proportionally bigger patrol zones. Pinned offline on all shipped
    CTF graphs: the zone is a strict subset of the map and the ENEMY base
    node always lies outside it.
  - Scratch: `bot_role`/`role_spawn_count`/`defend_radius` are one
    contiguous tail block (`layout/role.py`; df90 clears it with one
    rep-stosd — contiguity pinned in tests) + the `tag_role` R-chunk dumps
    it whole.
- Carriers ESCAPE, they don't fight (`cfg.CTF_CARRIER_ESCAPE_ENABLED`;
  built 2026-07-22, user-requested "the carrier who just stole the flag
  should be more engaged in returning/escaping than in fighting"):
  while `bot_carry[slot]` is set — a per-bot mirror of `ctf_pick_goal`'s
  live carry test, written per think (the `route_carry` GLOBAL is fresh
  only for whichever bot ran cpg last, and the consumers below run BEFORE
  this bot's cpg in the think; one-think staleness is fine), cleared on
  respawn, match change and the suspension early-out — the carrier:
  skips the combat strafe WEAVE (beeline home at full effective speed
  instead of the ~26%-slower dodge dance; it still shoots — fire is
  independent of movement), takes NO goody diverts (a damaged carrier
  must not detour to health packs mid-escape; an existing latch is
  dropped once via gd_clear the think the flag is grabbed — the
  latched-check before the jump is load-bearing, an unconditional clear
  would reset the node watchdog every think and starve the wall-slide),
  and makes NO roam switch wander-bumps (roll skipped, an in-progress
  press handed over). Deliberately KEPT for carriers: the dropped-flag
  pursuit (returning its own dropped flag unlocks the capture), the
  wall-slide/wedge/door/pad machinery (they ARE the escape), the
  fight-stall suspension skip, and the standoff tether (escape-to-base
  applies whether the home flag is present or not). DM/SK untouched
  (mirror stays 0 outside CTF).
- ATTACKER ROUTE-LANE SPLIT (`cfg.CTF_LANE_SPLIT_ENABLED`; built
  2026-07-22, live pass pending — the "not all attackers down the same
  corridor" layer): the deterministic BFS descent sent every attacker down
  the IDENTICAL shortest path (user-reported single-file conga). Each
  team's attackers carry a LANE bit (`bot_role` bit1, assigned at spawn:
  ordinals 0,1 -> lane 0; 2,3 -> lane 1; wraps). In `ctf_next_hop`, lane 0
  descends exactly as before (minimum-distance neighbour); lane 1 still
  requires every hop to STRICTLY DESCEND the active field — the progress
  guarantee is untouched (monotone descent terminates at the goal; pinned
  offline on all shipped CTF graphs from every reachable node) — but picks
  the LARGEST descending neighbour, so at every fork it peels onto the
  alternative branch; in corridors both lanes converge (only one way
  exists). `cnh_curd` (per-call fixed descent threshold — the running best
  in ECX becomes a MAX under lane 1 and can no longer double as the
  descent gate) + `cnh_lane` (per-call mode) live in `layout/lane.py`.
  Lane 1 NEVER applies to: carriers (shortest way home), defenders, seek
  descents, or the drop/chase/SK pursuit rows (objective-direct). CTF maps
  are near-symmetric, so descending forks are exactly the left/right route
  splits (offline: the max/min descents from the far node genuinely
  diverge on 3+ shipped maps).
- Bots WEAVE while fighting (`cfg.FIGHT_STRAFE_ENABLED`; built 2026-07-22,
  live pass pending — the dodge layer, user-reported "bots dodge
  vertically instead of horizontally": route jitter along the mostly
  vertical engagement axis looked like dodging, while real projectile
  dodging needs the PERPENDICULAR): while an enemy is inside fight range —
  the fire scan's per-bot `bot_enemy_near` stamp (240 px), which now also
  stamps the per-bot engagement vector `bot_enemy_dx/dy` (the global
  best_dx/dy are per-call temps another bot's scan overwrites) — the emit
  stage adds a perpendicular-to-the-enemy component to the desired vector
  BEFORE normalization: `v' = v + k*perp(e)`, `k = FIGHT_STRAFE_GAIN *
  |v|/|e|`, sign flipping every `2^FIGHT_STRAFE_FLIP_SHIFT` frames of the
  TRUE per-frame `frame_tick` (incremented once per page flip) with a slot
  offset so bots desync. Do NOT clock this off `frame_counter` — that
  increments once per BOT THINK (N bots advance it N per frame), which
  flipped the side nearly every frame and made bots visibly VIBRATE in
  place while dodging (live-reported first pass, fixed 2026-07-22).
  The heading swings ±atan(GAIN) (~42° at
  0.9) to alternating sides — bots zigzag ACROSS the line of fire while
  still progressing toward their goal (the watchdogs keep seeing
  progress, so no false recovery triggers), and the lateral magnitude
  scales with |v| so close-in final approaches (flag touch, switch/pad
  press) shrink the weave naturally. Suppressed while the wall-slide
  sweep owns the heading (stuck/wp_try at the slide trigger) — a weave
  into geometry would fight the sweep's escape rotation. Far bots never
  weave (the fire detour's recovery-tick path skips the scan, so the
  stamp stays clear).
- CTF bots PURSUE DROPPED FLAGS (`cfg.CTF_DROPPED_FLAG_ENABLED`; the "don't
  walk past the flag lying on the ground" layer). Two halves:
  - **Detection** (`entity_scan.py`, inside the `scan_portal_active` periodic
    grid walk): while a flag is AWAY (`flag_present[i] == 0`), the dropped
    world copy the drop-on-death canned script creates is the ONLY entity
    named exactly `"Red Flag"` / `"Blue Flag"` — the 7 authored at-base blue
    icons that carry the same name are the pickup flags themselves, consumed
    the moment the flag is stolen (census pinned in tests; red at-base flags
    are authored unnamed). The walk exact-matches each entity's name
    (`[ent+0x18]+8`, the `sub_4FBF20` CString chain — see
    `ax.ENTITY_NAME_CSTR_OFF`) against `drop_names[team]` and records the
    copy's raw `+0x4C/+0x50` into `flag_drop_pos[i]`/`flag_drop_valid[i]`.
    Valid flags are cleared+rebuilt each scan, so a consumed drop goes stale
    for at most one `PORTAL_ACTIVE_SCAN_INTERVAL`. Runs AFTER the walk's
    character shield, so a player named like a flag can never register. The
    name compare only executes while a flag is away (two loads per entity
    otherwise); no new patch sites.
  - **Pursuit — TWO-PHASE, graph-routed** (`bot_movement.py` +
    `flag_route.py: drop_next_hop / drop_route_refresh`). v1 steered
    STRAIGHT at the drop from up to 350 px; live `dpursuit` snapshots
    pinned its failure loop — one 30-frame no-improvement watchdog window
    ended the pursuit ~250 px short (wall between), the 240-think retry
    cooldown made the bot "ignore" the flag, then it re-latched and ground
    the wall again ("runs at it, then ignores it"). v2:
    - **Latch**: within `CTF_DROP_PURSUE_RADIUS_SQ` (350 px)
      opportunistically — or from ANY distance when the drop is this bot's
      missing GOAL flag (`route_missing_goal[slot]`: an attacker whose
      steal target is dropped, a carrier whose home flag is dropped). The
      position is known, so the blind search/wait roam is replaced by a
      real route to it. No team/carry filter: touching a drop is
      beneficial for either team, and a carrier returning its own dropped
      flag is exactly the play that unlocks its capture.
    - **ROUTED phase** (farther than `CTF_DROP_DIRECT_RADIUS_SQ`, 160 px):
      the scan binds each drop to its nearest graph node
      (`flag_drop_node`); `drop_route_refresh` (page flip, right after the
      periodic scan) rebuilds a per-drop `bfs_run` hop row (`drop_dist`,
      full-field semantics, bfs_skip=0) whenever that node changes; at
      each node arrival `drop_next_hop` descends the row INSTEAD of
      `ctf_next_hop` (respects `bot_route_suspend` — the suspension roam
      still un-sticks wedges) and EMITS PAD HOPS exactly like the ctf pass
      (`route_portal_hop` + return cur). The pad pass is load-bearing, not
      an optimization: a cross-arena drop descent on Hydro funnels into the
      pad-entry node, whose only WALKABLE neighbour ascends — without the
      pad hop, drop_next_hop returned -1 there, the random fallback bounced
      the bot off the pad node and the next descent snapped it back — the
      live-reported "moves between two waypoints only" shuttle (dpursuit
      snapshot: 0↔25 orbit with failed-edge marker (0,25); offline sim
      pinned in tests). Walls are routed AROUND; a graph-unreachable or
      unbound/stale row falls back to normal goal routing with the latch
      dormant. Opportunistic latches silently drop beyond
      `CTF_DROP_ABANDON_RADIUS_SQ`; objective bots are exempt.
    - **DIRECT phase** (within 160 px, or targeting the drop's own bound
      node AND physically within the stuck-arrival radius of it — the
      arrival gate is load-bearing: `cur == drop node` alone fires the
      moment the routed hop ASSIGNS the node, and live snapshots caught a
      fresh-out-of-teleport bot straight-steering from the exit pocket past
      the return pad, wedging on the pad veto until the slide sweep crossed
      the trigger sliver → engine re-teleport → cross-arena re-route →
      infinite teleport ping-pong when the drop lay near a pad): steer
      straight at the copy through the standard watchdog with
      its own progress tracker (`bot_drop_best` — wp_best_dsq stays with
      the node logic) and PRESS PATIENCE (`CTF_DROP_PRESS_PATIENCE` fresh
      cycles, mirror of the door/pad patience) before
      `CTF_DROP_RETRY_COOLDOWN_FRAMES` blacklists it. Reaching
      `CTF_DROP_REACHED_RADIUS_SQ` ends the pursuit with
      `CTF_DROP_GRAB_COOLDOWN_FRAMES` (> one scan interval, so the
      consumed copy's stale position cannot re-latch before the scan
      clears it).
    - Latch also drops on respawn/death/teleport-jump/match change, a
      stale idx, the drop disappearing, or the flag returning home
      (event-instant). Far bots work because `BOT_PARTICIPANT_POS_ENABLED`
      keeps the drop's touch script simulated around them. Overlay: a
      valid drop draws as an oval PLUS a double-radius ring (the ring —
      not the hue — distinguishes it from the single-oval base anchors).
    R-snapshot chunk `dpursuit` dumps drop valid/pos/node + per-bot
    latch/cd/patience/best + route roots + knobs.
- CTF bots CHASE ENEMY FLAG CARRIERS they see (`cfg.CTF_CHASE_ENABLED`;
  built 2026-07-22, first live pass pending). Three pieces:
  - **Sighting** (`bot_perception.py`, inside the existing `pick_target`
    loop — no new patch sites): a candidate that passes the CTF team filter
    AND carries a flag (`chr_carrying`, the factored inventory-group test)
    is BY CONSTRUCTION an enemy holding the scanning bot's OWN team flag
    (nobody can carry their own). Within `CTF_CHASE_RADIUS_SQ` (400 px)
    with a clear engine LOS sweep, the sighting stamps SHARED per-flag
    intel — `chase_pos`/`chase_ttl[home]`, keyed by the bot's home flag
    idx, refreshed by ANY bot's sighting — and latches the SEEING bot's
    pursuit (`bot_chase_flag[slot] = home+1`), gated on the per-bot
    cooldown and the bot not itself carrying. The whole check is disabled
    per call unless CTF routing is armed AND the home flag is AWAY
    (`chase_scan_tmp = -1`; no carrier can exist while it sits home), so
    the common-case cost is one load+cmp per candidate. The old FPU
    best-gate (`fcomp`) became an unsigned float-bits integer compare so
    the x87 stack is empty across the chase engine calls.
  - **Two-phase pursuit** (the drop-pursuit-v2 shape — the v1 lesson: a
    target behind a wall must be routed AROUND, never straight-steered
    at): the page flip (`chase_route_refresh`) ticks the TTL, binds
    `chase_pos` to its nearest graph node per frame while intel is live,
    and rebuilds the per-flag `chase_dist` BFS row ONLY when that node
    changes (~one bounded `bfs_run` every 1-2 s per carrier; full-field
    semantics, `chase_root` guards staleness). ROUTED phase: at node
    arrivals `chase_next_hop` descends the row (below the drop-pursuit
    latch, above SK/ctf in the dispatch; respects `bot_route_suspend`;
    emits PAD HOPS exactly like the ctf/drop passes). DIRECT phase
    (within `CTF_CHASE_DIRECT_RADIUS_SQ` = 160 px, or physically at the
    carrier's bound node via the stuck-arrival gate): steer straight at
    the last-seen position — and because the target MOVES, the stall
    signal is the PHYSICAL stuck detector (`stuck_count`), NOT dsq
    improvement (a fleeing carrier grows dsq while the chaser runs at
    full speed; a dsq watchdog would false-trigger the slide sweep). A
    pinned full watchdog window abandons with
    `CTF_CHASE_COOLDOWN_FRAMES`; fire targeting is independent, so the
    bot keeps shooting a visible carrier throughout.
  - **Priorities + hygiene**: pad approach > drop pursuit > CHASE > goody
    > switch bump (a flag on the ground outranks the carrier holding
    one — killing the carrier DROPS the flag and the drop pursuit takes
    over to return it; the chase block unlatches itself when a drop
    latches). Latch drops on: TTL expiry, `flag_present[home]` flipping
    back (event-instant — no carrier exists), the bot grabbing any flag
    itself, `CTF_CHASE_ABANDON_RADIUS_SQ` (700 px), respawn/death,
    teleport-jump, match change (`detour_df90` clears the block, then
    re-poisons `chase_node`/`chase_root` to -1 — a zeroed root is a VALID
    node idx and could alias a fresh bind onto LAST match's row).
    Defenders chase too (that IS the intercept play; the tether pulls
    them back after). R-snapshot chunk `chase` dumps the whole block.
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
- **CTF flags cannot be duplicated into two inventories** (`cfg.CTF_FLAG_
  GIVE_GUARD_ENABLED`, `detours/flag_give_guard.py`, site `0x5B4DA0`).
  Live 2026-07-20 (8v8): two same-team bots each carried the red flag. Root
  cause is a vanilla same-frame race the bots make common: the "Picked up a
  Flag" canned script's enemy-touch branch runs `CDeleteAction($trigger)` +
  `CGiveDefaultInventoryItemAction($Instigator)`, and when TWO characters
  overlap the flag's `CPassThroughTriggerAI` in the same frame each toucher
  executes the whole script (the delete is deferred/idempotent) — each gets
  a flag item. Goal routing and the dropped-flag pursuit deliberately send
  several bots at the same flag, so bots arrive frame-synchronized where
  humans rarely do. The guard detours the action's per-target give: when
  the given def is the Red/Blue Flag (compare `action+0x10` against the
  `sub_523DF0`-resolved keys — same id space) AND any live character already
  carries that def (`sub_426860` count sweep over `mgr+0x290`, cap 32), the
  give returns without granting; the rest of the script chain is idempotent
  so the second toucher simply gets nothing. Blocked events count into
  `flag_give_block_count` (R-chunk `wedge`).
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
    OPEN doorway is SOLID but is not a door). The cache also carries a
    **CEntityAnimated CLASS GATE** (`sub_416790` is-a against the
    `sub_48DE10` descriptor): all 333 MP CDoorAI parts are authored
    `Level Part=CEntityAnimated` (census pinned in tests), while Hydroplant
    Bouncefest authors TWO always-solid unnamed CEntityBase wall-corner
    models at the EXACT anchor position of every door — they filled cache
    slots and, since the refresh ORs the cached SOLID bits, pinned
    `door_blocked=1` forever (live-diagnosed 2026-07-19: permanent double
    rings on genuinely open doors, on this map only; evicting the props
    live flipped blocked to [0,0,0,0] within a frame and the un-gated scan
    re-polluted it 2 s later). CEntityBase is CEntityAnimated's PARENT, so
    the is-a test rejects the props and admits any animated subclass.
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
    the follower, when a routed bot's `bot_route_epoch[slot]` lags, syncs it
    and — only for a bot NOT latched onto an edge (`prev_wp == -1`) — sets
    `current_wp=-1` so the cold-acquire re-runs `ctf_next_hop` THAT think. An
    EDGE-LATCHED bot KEEPS its target: the original blanket invalidate was
    live-refuted on Battle on the Ice (2026-07-20 snapshots) — its
    self-closing south team door flips `door_blocked` every few seconds, and
    the Euclidean nearest-node cold-acquire re-latched the node BEHIND a bot
    that had just crossed the doorway (node 47 sits 30 px on the far side;
    the 64 px arrival radius then "arrived" it across the closed door and
    re-planned from the wrong side) — one third of the reported
    backwards-and-forwards shuttle. A latched bot re-plans against the
    rebuilt field at its next arrival, and a now-blocked current edge is
    handled the SAME think by the commitment recovery below (which is why
    dropping the invalidate is safe for the original Torture scenario: the
    recovery backs the bot off the pillar-gate edge to node 28 and the
    arrival re-plan takes the blue gate). Both epochs reset to 0 on
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
    currently-blocked door AND the bot has NOT crossed the door, back
    `current_wp` up to prev and fall into the arrival/advance path so
    `ctf_next_hop` re-plans door-aware from the reachable node (offline-
    verified: 15→14 across closed door 9 re-plans 15→16; 5→6 across closed door
    15 re-plans 5→1). Fires only in that exact stuck state — door open or
    already-crossed is a no-op. The crossed test is the DOOR-SIDE sign
    `dot(bot − door_center, v_cur − v_prev) > 0`, NOT node proximity: doors
    are rarely at the edge midpoint (Battle on the Ice: 30 px from node 47,
    170 px from node 48), so the original "nearer prev than cur" test
    mislabelled a bot standing just past the doorway as not-crossed and
    walked it back INTO the closed door (2026-07-20 snapshots, part of the
    self-closing-door shuttle). Degenerate dot <= 0 (bot exactly on the door
    line) backs up — the safe side. NOTE: `.zaxbot` code headroom is ~1.7 KB
    below `SCRATCH_OFF` (hook_entry_size 47391 of 49152 after the bot-menu,
    CTF role/standoff, enemy-carrier chase, route-lane, combat-strafe,
    pickup need-gate, carrier-escape, proximity-mine, overlay-batched-lock
    and movement-divergence layers; the boundary sits at 0xC000 with the
    section at 0x2A000). When it runs low again, bump
    `SCRATCH_OFF`+`NEW_SECTION_SIZE` together (build asserts on overflow). The `rstate`
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
    `SWITCH_SEEK_SHORTCUT_GAIN`+ UNITS worse than the full field (GAIN=20
    quanta ≈ 320 px since the weighted-routing change — a red bot inside
    the blue base with the team-gated blue door shut, or the Hydroplant
    blue-base bot whose door route is 42 quanta shorter than the
    around-route the hop metric could not distinguish), `ctf_next_hop`
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
    FINAL-APPROACH the switch center at its node to physically bump it.
    Participation carries a per-bot ON-THE-WAY gate
    (`SWITCH_SEEK_JOIN_SLACK`, 24 quanta): a bot joins only when
    `seek_dist[cur] + full(switch→goal) <= full(cur→goal) + SLACK` (a bot
    whose full-field goal distance is unreachable joins unconditionally).
    Unconditional joining was live-refuted on Battle on the Ice (2026-07-20
    R snapshots, the "bots shuttle backwards and forwards past self-closing
    doors" report): the south team door there re-closes seconds after its
    walk-up opener fires, each re-close re-activated its adjacent switch 1
    (node 46, the only viable candidate — switch 0 sits sealed inside the
    enemy base), and EVERY team bot that could reach it joined
    (`bot_seek=[1,1,1,1,1]` with bots at nodes 12/13; slot 1 visibly
    backtracked 14→54, then flipped forward again on the next rebuild).
    The slack is sized from that map's gap: south-side node 47 detours 16
    quanta (must join — the switch is its way through) while node 48, just
    NORTH of the doorway, detours 38 (must not turn back). Pinned in
    tests with the runtime's directional edge_pass semantics. The
    opened door then flows through door_dirty → `rebuild_open_routes`, which
    CLEARS all seek state (stale by definition) and bumps the epoch — bots
    re-request if still blocked, which also CHAINS seeks (Torture: a sealed
    blue bot first opens its own base's toggler, escapes, then the next
    round handles the far side). A seek with no door change for
    `SWITCH_SEEK_TIMEOUT_FRAMES` blacklists that switch until the next
    door-state change. Offline-verified on the shipped graphs (pinned in
    tests, weighted-metric scores): sealed Torture bot → a reachable pillar
    toggler that unseals its base (the weighted metric picks the physically
    nearest one); red-at-blue-base carrier → the blue-door switch inside
    the base, NOT the red-door switch across the map; Hydroplant blue-base
    bot → the blue-side switch 0 (score 117 vs the 161-unit around-route —
    the scenario the hop metric could never gate on).
    Known noise: an attacker near its own base may bump a near switch whose
    door its team could open anyway (harmless short detour; the opened
    door excludes the switch from the next round).
  - **Roam-time WANDER-BUMP** (`cfg.SWITCH_WANDER_ENABLED`, the "switches are
    part of the random path" layer; live-motivated on Hydroplant Bouncefest
    2026-07-19 — seek only serves ROUTED bots, that map's seek gate can
    structurally never trip (base-to-base is equal-cost around every door),
    and a two-flags-away standoff had both bots in permanent missing-flag
    roam, so switches were NEVER touched): a bot taking the roam fallback
    (DM roam, goal-less CTF search, routing fallback) that arrives at a node
    hosting a door-opening switch with >=1 paired door currently blocked
    (same toggle-safety as seek candidates) rolls RNG(0..99) <
    `SWITCH_WANDER_CHANCE` (35) via `switch_wander_check` (world_scan.py)
    and latches `bot_switch_target[slot]`; the follower final-approaches the
    switch CENTER through the standard watchdog + press patience
    (`SWITCH_WANDER_PRESS_PATIENCE`, mirror of the pad/door patience).
    Success = the switch's blocked-paired-door census CHANGED since latch
    (`switch_blocked_census`, shared helper — registers for openers AND
    togglers, and stops the press before a re-bump re-closes a toggler);
    success or exhausted patience arms `bot_switch_cd[slot]`
    (`SWITCH_WANDER_COOLDOWN_FRAMES`, 900) so a bot cannot orbit one switch.
    Deliberately NOT gated on routing suspension (unlike the portal wander
    roll): a bump is local and can open the exact door the bot is wedged at.
    A dropped-flag pursuit outranks the bump; latch drops on
    respawn/death/teleport-jump/match change/stale idx. `switch_node[]` is
    now bound per match in `load_switches` (not just `build_flag_routes`,
    which is CTF-only) so DM roamers bump too; the per-bot state block
    clears there as well. R-snapshot chunk `swander` dumps the whole block
    (latch/cd/patience/census + chance knob).
- Salvage King (SK / "Greed") bots EXECUTE THE MODE GOAL
  (`cfg.SK_ENABLED`; built 2026-07-19, offline-verified on all 9 shipped SK
  graphs, first live pass pending): collect minerals, carry them to their OWN
  bin, deposit for points, and steal death piles. Engine facts (IDA session
  2026-07-19): there is NO engine collector concept — bin ownership is pure
  map script (`CIsOnSameTeamAction($Instigator, $Trigger)` inside the bin's
  'Drop Ore in Container' canned: toucher entity team `+0x24` must equal the
  bin part's authored `Team Number`), and the authored `'Bin NN'` parts carry
  `Team Number = NN-1` covering `[0, MaxPlayers)` contiguously — the SAME id
  space the patch assigns SK bots (team = botidx), so each bot's one scoring
  bin is known statically. The deposit itself is
  `CSalvageKingScoreMineralsAction` (apply `sub_561AB0`): consumes the WHOLE
  carried load, score += 1/ore + 3/crystal (gametype floats `+0xC8/+0xCC`)
  into `stats+0xF6`. Carried counts are read via the engine's own getter
  `sub_426860` (`__usercall` ECX=char, EDX=def key from
  `sub_591FC0(dword_6C0C08, name, -1)`; keys per match in
  `sk_def_ore/sk_def_crystal` — the exact calls the SK stats sync
  `sub_5616B0` makes; SK also mirrors them into `stats+0xEE/+0xF2` words).
  Pieces:
  - **Static data** (`sk_data.py` → `static_data.write_sk_static_table`,
    census pinned in tests): minerals are `Model=Items/Money/{Ore deposit N,
    Crystal NN}` CEntityBase pickups, `Used In=MultiPlayer/Salvage King`
    (SK matches ONLY), respawn-in-place 10-15 s, DENSE — 107..386 per map,
    1856 across the 9 SK-capable maps (8 Greed maps + Jungle Ruins).
    `load_sk` (detour_df90, after `load_switches`) copies the active map's
    minerals, scatters bins into the TEAM-indexed `sk_bin_table/valid/node`,
    resolves the item-def keys, binds graph nodes, and clears all live SK
    state.
  - **Routing** (`build_sk_routes`, df90 when `detect_mode()==SK`; arms
    `sk_routing_active`): a MULTI-SOURCE mineral field (`sk_ore_dist` — one
    `bfs_run_seeded` pass with every mineral-bearing node at distance 0; the
    new seeded entry into `bfs_run` skips its single-seed prologue) plus one
    `bfs_run` row per authored bin (`sk_bin_dist`, team-major). Minerals
    respawn in place, so presence tracking is deliberately NOT attempted —
    both fields build once per match, no rebuilds, no periodic cost.
  - **Behavior** (`sk_next_hop` replaces the arrival next-hop while armed;
    `sk_update_phase` maintains a per-bot COLLECT/RETURN hysteresis latch:
    carry==0 clears, carry >= `bot_sk_thresh[slot]` sets — the threshold is
    RANDOMIZED per run via the engine RNG in
    `[SK_RETURN_CARRY_RAND_LO, SK_RETURN_CARRY_RAND_HI]` (30..100), rolled
    lazily on first pickup and RE-ROLLED by `sk_roll_thresh` on every
    RETURN→empty transition — the frame a deposit banks the load, or a
    death while latched): COLLECT
    descends the mineral field; INSIDE a mineral zone (dist 0) it returns -1
    so the random roam sweeps the dense cluster and collects by walk-over.
    RETURN descends the bot's own-bin row; at the bin node the DEPOSIT FINAL
    APPROACH steers at the bin center (collidable prop — same press
    machinery as the switch bump, `SK_DEPOSIT_PRESS_PATIENCE` cycles then
    routing suspension) and the per-think `sk_update_phase` sees the emptied
    inventory the same frame the canned action scores, ending the press
    naturally. Pad hops are emitted exactly like ctf/drop descents (Jungle
    Ruins is an SK map with pads). Respects `bot_route_suspend`; all latches
    drop on respawn/death/teleport/match change.
  - **Death piles** (`detours/sk_pile_register.py`, `detour_5A6E60`): every
    MP death runs `CDropAllOreAndCrystalsAction` (apply `sub_5A6E60`, ret
    0x10, victim at `[esp+8]`), which clones an UNNAMED pile entity (model
    `Ore_Crystals01` template) within 500 px of the corpse holding the whole
    load — touching grants everything to either team. No name ⇒ the CTF-
    style scan match cannot see piles, so the detour self-registers the
    corpse position into an 8-slot TTL ring (`sk_pile_pos/valid`) + its
    nearest graph node (`sk_pile_node`), skipping empty-handed deaths via
    the same `sub_426860` gate the apply uses. TTL
    (`SK_PILE_TTL_FRAMES`, 45 s, ticked by `sk_pile_tick` from the page
    flip) bounds stale entries a human grabbed first. Pursuit runs through
    the generalized GOODY layer below.
- Bots pursue GOODIES through the graph — piles AND filler items
  (`cfg.ITEM_PURSUIT_ENABLED`; the two-phase upgrade of the straight-steer
  pile divert, which ground walls when a pile registered across one —
  user-reported, same bug class as CTF drop-pursuit v1):
  - **Static goody anchors** (`item_data.py` → `load_items`, per match,
    ALL modes): every MP map's pickups whose model path starts
    `Items/Medical|Energy|Shields/` (272 fillers across 17 maps; category
    by prefix) PLUS category 3 = WEAPON pickups (220; user-requested
    2026-07-23) matched by an explicit gun-GRANTING model set — NOT the
    `Items/Weapons/` prefix, because 255 of that prefix's 475 parts are
    AMMO packs (`PU Semi Auto Ammo`/`PU Grenade Canister`/`PU Missile 5
    Pack`/`PU Proximity Mine`), which stay walk-over-only. `PU Light
    Pistol` (70) IS in the set: the actual MP spawn loadout is the far
    weaker **Modified Laser Welder** (user-corrected 2026-07-23), so even
    the pistol is an upgrade. Census pinned in tests (492 total).
    Keys/minerals excluded. All respawn in place (10-15 s), so like
    minerals there is NO presence tracking — a consumed anchor costs one
    cooldown-bounded empty visit.
  - **NO patch-side weapon equip** — the ENGINE auto-switches to a
    picked-up weapon it considers better (user-confirmed 2026-07-23,
    "always worked good for the bots"). A brief `weapon_equip_tick`
    built on the opposite assumption FROZE the game at CTF bot spawn the
    same day and was removed: its Primary-group walk assumed
    `sub_425350` terminates with -1, but that iterator WRAPS (constraint
    #7 / addresses.py), so a welder-only fresh bot spun the scan
    forever on the first page flip. The welder-heavy fights that
    motivated it were actually the weapon-NEED count wrapping too (a
    lone welder counted as `WEAPON_NEED_MIN_OWNED`, clearing bit3, so
    weapons were never pursued at all — confirmed by the pre-freeze
    snapshot: goody layer live, filler pursuits resolving, zero weapon
    latches); fixed with the `sub_425470` last-item loop guard.
  - **Routing fields**: `build_item_routes` (df90, mode-independent, arms
    `item_routing_active`) fills one MULTI-SOURCE `bfs_run_seeded` row per
    CATEGORY (`item_dist`, cat-major); `sk_pile_route_refresh` (page flip)
    rebuilds the pile row (`sk_pile_dist`) whenever `sk_pile_dirty` is set
    — pile registration, TTL expiry, or a bot grabbing one. One bounded
    SPFA per pile event, never per frame.
  - **Behavior** (`s542360_gd_*` in bot_movement + the `goody_scan_piles/
    items` helpers): the latch `bot_pile_target` holds the pursuit KIND
    (1 = pile, 2+cat = item). ENTRY is opportunistic and RANKED (user rule
    2026-07-23: weapons over ammo/regular pickups, below the objective
    pursuits): piles first (within `SK_PILE_PURSUE_RADIUS_SQ`, SK only),
    then WEAPONS alone within `WEAPON_PURSUE_RADIUS_SQ` (350 px — arming
    up is worth a longer walk), then the any-category filler fallback
    within `ITEM_PURSUE_RADIUS_SQ`; shared per-bot cooldown; the CTF drop
    pursuit / enemy-carrier chase / pad approach still outrank the whole
    block by dispatch order — exactly the user's exception list. The
    weapon entry is a DISTANCE-WEIGHTED ROLL (user refinement same day:
    "the closer the weapon, the higher the chance"): chance =
    `weapon_chance_max` × (R²−d²)/R² — adjacent gun ≈ certain grab,
    radius-edge gun almost never diverts, and an attacker whose route
    closes on the pickup re-rolls with rising odds every
    `WEAPON_ROLL_RETRY_FRAMES` (45; a lost roll arms the SHARED goody
    cooldown with that short value and skips the window's filler entry
    too — a latch while the cd runs would violate the cd-implies-no-latch
    invariant). Entries are NEED-GATED (`cfg.ITEM_NEED_GATE_ENABLED`,
    built 2026-07-22 from the user's greed report): `goody_update_need`
    refreshes `goody_need_mask` (bit0 health / bit1 energy / bit2 shield
    / bit3 WEAPON) once per goody think from the bot's LIVE state, and
    the item scan skips clear-bit categories. Bit3's need test: fewer
    than `WEAPON_NEED_MIN_OWNED` (3) items in the engine's "Primary"
    group (counted via the group iterate `sub_425350`, key lazily cached
    in `primary_hash` like spawn.py's force-weapon path) — spawn loadout
    is the lone welder, so fresh bots hunt guns and armed bots stop
    detouring. No engine pickup-useful predicate exists for whole
    weapons; the count IS the need test.
    The tests are the ENGINE'S OWN pickup-useful predicates, not
    re-derived rules — health: `cur_damage(char+0x7C) != 0` (float never
    negative, bits==0 iff full; `sub_48D030/48D150` = cur/max health
    confirm the reduction); energy: `SUB_BATTERY_NEED_VA` (0x5B06E0,
    CBatteryChargeInventoryItem vtbl slot 32 — carried battery charge
    item+0x18 < capacity def+0x4C, NO battery -> no need); shield:
    `SUB_SHIELD_NEED_VA` (0x56F710, same shape over
    CShieldInventoryItem — NO SHIELD CARRIED -> NO NEED, the exact
    "don't target shield blobs without a shield" rule, full -> no need).
    Both are `__stdcall(char) ret 4`, NULL+is-a-guarded, callee-saved
    preserved. A latched category whose need vanishes mid-route (topped
    up on the way) resolves to no target and unlatches cleanly. Each
    think the live target is RE-RESOLVED as
    the nearest pile / nearest item of the latched category, so a category
    descent that reaches a closer same-kind item takes it. ROUTED phase:
    `sk_next_hop`'s kind row-select descends the matching field at each
    arrival (pad hops included; also fires outside SK via the item gate at
    the call site); DIRECT phase (within `GOODY_DIRECT_RADIUS_SQ`, or
    physically at the target's bound node — the drop-pursuit arrival gate)
    presses through the standard watchdog + patience. Reaching a pile
    consumes its ring slot + rebuild; items take
    `ITEM_GRAB_COOLDOWN_FRAMES`. `GOODY_ABANDON_RADIUS_SQ` bounds drift
    (routed paths legitimately move AWAY around walls, so it sits well
    above the entry radii). R-snapshot chunk `goody` dumps the resolved
    target + gates; latches drop on respawn/death/teleport/match change.
  - R-snapshot chunk `skstate` dumps the live block (routing gate, counts,
    bin tables, per-bot phase/carry/patience latches, pile ring). Offline
    tests pin the census, the scratch-block invariants, and — on every
    shipped SK graph — that all graph nodes reach a mineral zone and every
    bin is reachable from every mineral node.
- Bots PLACE PROXIMITY MINES and steer around THEIR OWN (`cfg.MINE_ENABLED`;
  built 2026-07-23; placement + overlay markers live-confirmed same day.
  LIVE-CONFIRMED mine semantics: a deployed mine has NO owner or team
  immunity — it kills its placer (standing on your own mine is suicide)
  and in CTF it kills same-team players even with friendly fire disabled).
  Engine model (IDA session
  2026-07-23, see the `mine-deploy-mechanism` memory): the Proximity Mine is
  a SECONDARY-slot weapon (`Inventory Type=Secondary`, ammo = itself, 1
  round per pickup, reuse delay 0.3 s) whose "projectile" is the deployed
  mine — a CEntityProjectile owned by the placing char, spawned exactly AT
  the char's position; the human right-click enqueues a pending-action event
  whose execute is **`sub_5AB9B0(char)`**, the engine's complete deploy
  (selection lookup, can-fire gate `item->vtbl[+0x98]`, round consume,
  entity create/place/register, New Shot Action). The def's MP script
  warp-deletes a deployed mine after ~15 s — which is why the live-mine
  table is a plain TTL RING (`MINE_TTL_FRAMES` = 900) with no liveness
  scanning. Pieces:
  - **Placement** (`mine_tick`, page flip, `world_scan/mines.py`): per live
    bot, a cooldown counts down; at 0 the bot re-arms a short retry window
    (`MINE_PLACE_RETRY_FRAMES`) and attempts: carried-rounds gate
    (`sub_426860` on the per-match `mine_def_key`), RNG roll <
    `mine_place_chance` (scratch knob, default 35), a spacing sweep (no
    live ring mine within `MINE_SPACING_RADIUS_SQ` of the bot — a wedged
    bot must not stack its reserve on one spot), then force-select the mine
    in the Secondary slot (fast path when already selected; else the
    engine's own group iterate `sub_425350`/`sub_424F60` matched on
    `[item+8] == mine_def_key`, the spawn.py select + force-switch
    sequence on the Secondary slot) and call `sub_5AB9B0(char)` through
    the PATCHED entry. Success = the carried-round count DROPPED (the
    deploy has no useful return); success re-arms the long cooldown
    (`MINE_PLACE_COOLDOWN_FRAMES`, 600). Bots roam constantly, so the low
    per-window chance scatters mines organically over the map.
    **CTF TERRITORY GATE** (`cfg.MINE_CTF_TERRITORY_ENABLED`, in `mt_try`
    between the char capture and the rounds gate; user rule 2026-07-23:
    "mine the enemy half, rarely the middle, never the own half"):
    territory is classified by PATH distance from the CTF routing BFS
    fields — at the bot's current node (follower target, else
    `wp_find_nearest`), `own_d`/`enemy_d` = `flag_dist[base][node]` with
    bases mapped through `flag_team[]` vs `bot_team[slot]`. `enemy_d +
    band < own_d` ⇒ enemy half, place; `own_d + band < enemy_d` ⇒ own
    half, deny (CTF defenders hold their rounds until they cross out);
    the `|diff| <= band` strip is the middle ⇒ an EXTRA roll <
    `mine_ctf_mid_chance` (both knobs scratch-packed live-tunable;
    defaults band 16 quanta = 256 px of path, chance 15 ⇒ mid-map mines
    on ~5% of already-successful windows). Path metric, not Euclidean,
    so a spot behind a wall is classified by how you WALK there. Inert
    outside CTF (menu_mode gate), while routing is unarmed, or when
    either distance is unreachable (falls back to place-anywhere).
    Classifier semantics pinned in
    `test_ctf_territory_classification_rule`.
  - **Registration** (`detour_5AB9B0`, `detours/mine_register.py`): the
    deploy chokepoint detour predicts at ENTRY whether THIS call deploys a
    mine (selected Secondary item's `[item+8]` == mine def key AND its
    can-fire virtual passes — exactly the body's own gates, and the mine
    lands at the char's position) and appends (char `+0x4C/+0x50`, TTL
    seed, owner) into the ring. Owner = the `mine_placing_slot` HANDSHAKE
    `mine_tick` sets (slot+1) around its deploy call and resets after — 0
    = no bot placement in flight = the HOST HUMAN (stored -1). The
    original `bot_chars[]` pointer sweep was live-refuted (2026-07-23
    snapshots: `place_count` 5 with every ring owner -1): that table is
    captured at SPAWN and each respawn creates a NEW char object, so
    every post-respawn bot mine mis-attributed to the human and the
    own-mine veto never matched — the reported "bot still steps on its
    own mine". PC2 mines deploy client-side and are NOT observed (open).
    Uses its own `mreg_*` temps — `mine_tmp_*` are LIVE across the detour
    when the deploy is bot-initiated.
  - **Avoidance — OWN MINES ONLY** (`s542360_mine_veto` in
    `vector_emit.py`, between the portal and plasma vetoes;
    `cfg.MINE_AVOID_ENABLED` + `cfg.MINE_AVOID_OWN_ONLY`): mirror of the
    portal veto — any candidate heading whose `LAVA_LOOKAHEAD_PX`
    lookahead point lands within sqrt(`MINE_AVOID_RADIUS_SQ`) (96 px) of a
    LIVE ring mine rotates onward (`lava_sweep_step` per try, full circle
    cap). No cooldown gate (mines are always dangerous), and the exempt
    entry is POSITIONAL: a mine whose bubble already CONTAINS the bot is
    skipped — otherwise every escape heading would be vetoed, and that
    exemption is also what lets the owner walk off its own just-placed
    mine (the deploy drops it at the bot's feet). The veto (and the
    placement spacing sweep) filter on `mine_owner[] == bot slot`
    (user-requested 2026-07-23): a mine kills its own placer, so a bot
    must never step on its own — but avoiding everyone else's mines
    would make bots immune to the host player's mines. Ownership is
    SLOT-keyed, so a respawned bot still avoids its previous life's
    mines; host-human mines carry owner -1 and never match a bot slot.
  - Overlay: every live ring mine draws as an oval + double-radius ring
    (portal color — B-driven palette, the ring is the signal). R-snapshot
    chunk `mines` dumps keys, cursor, counters, per-bot cooldowns, the
    whole ring and the packed knobs. `load_mine` (df90) clears
    `mine_def_key..mine_pos` with one rep-stosd — the packed knobs sit
    AFTER the cleared run so a match change can't zero the live-tunable
    chance (layout pinned in `MineTests`).
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
- `0x5B4DA0` - CGiveDefaultInventoryItemAction per-target give; duplicate-flag
  guard (gated by `cfg.CTF_FLAG_GIVE_GUARD_ENABLED`): suppresses a Red/Blue
  Flag give when any live character already carries that flag def. Two
  characters overlapping the flag's pass-through trigger in the SAME frame
  each execute the "Picked up a Flag" canned script (the world flag's
  CDeleteAction is deferred/idempotent), which live-produced two same-team
  red-flag carriers 2026-07-20 — pack-routed bots make that race common.
  Non-flag gives replay the prologue untouched; blocked gives count into
  `flag_give_block_count` (dumped in the R-chunk `wedge`, cumulative per
  process run).
- `0x5A6E60` - CDropAllOreAndCrystalsAction per-target apply; SK death-pile
  self-registration into the `sk_pile` ring (gated by `cfg.SK_ENABLED`;
  fast-skips outside armed SK matches).
- `0x5AB9B0` - the engine's secondary-item deploy `sub_5AB9B0(char)`;
  proximity-mine self-registration into the `mine_pos`/`mine_ttl` ring
  (gated by `cfg.MINE_ENABLED`; fast-skips while no match has resolved the
  mine def key). Catches host-human right-clicks AND bot placements (the
  `mine_tick` placement path calls through the patched entry).
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
| `sub_5B4DA0` | `CGiveDefaultInventoryItemAction` per-target give (vtable `0x604A4C` slot 28, single xref — patching the function IS patching the class; `__thiscall`, ECX=action, `[esp+4]`=resolved `$Instigator`, ret 4; base execute = `sub_5B3B80`). `action+0x10` holds the item-def KEY (reader `sub_5B4650` fills it from `sub_482DE0` = `item+8`) — the SAME id space `sub_523DF0(registry, name, -1)` resolves and `sub_426860(ECX=char, EDX=key)` counts, so the dup-flag guard compares/sweeps with existing helpers. The only path a CTF flag enters an inventory ("Picked up a Flag" enemy-touch branch). |
| `sub_4C29F0` / `sub_4C2D60` | CActivateAction / CDeactivateAction PER-ENTITY apply (vtable slot 27; entity at `[esp+0x10]`, `ret 0x10`; sets/clears `entity+0x1C & 0x800000` via entity vtbl `+0xE8`/`+0xEC`). Detoured for event-driven `flag_present[]`: the map scripts express "own flag home" as the base checker's activation. Executes (slot 23) funnel through the by-name multi-target resolver `sub_41AED0`. |
| `VT_CACTIVATE_ACTION_VA = 0x5F6374` / `VT_CDEACTIVATE_ACTION_VA = 0x5F63E4` | the two action vtables (each apply is reachable only through its own vtable). |
| `"Red Checker"` / `"Blue Checker"` | per-map base touch trigger (CTouchingOvalTriggerAI) authored exactly on the flag spawn anchor; Enter Action = canned `Returned a Flag` (the capture chain). Deactivated while that team's flag is away. Names verified on all 7 CTF-capable maps. |
| `"Red Flag"` / `"Blue Flag"` | inventory item definition names resolved through `sub_523DF0(dword_6C0C08, name, -1)`; compare against carried `CInventoryItem + 8` to know which exact flag is carried. |
| `"Multiplayer Flag"` | inventory group name resolved through `sub_591FC0(dword_6C0800, name, -1)`; `sub_425290(inv, [0x714454]) != -1` means the character carries any CTF flag. |
| `sub_4C11A0` | relocate/teleport executor (`__thiscall`: `ecx`=action, `[esp+4]`=entity at SOURCE pos; reads dest via `sub_4F4AC0` later). The chokepoint every `CRelocate`/`CTeleportAction` funnels through (both override execute vtable slot 27 with `sub_5A5A60` → `sub_4C1060` → here). Detour site for runtime portal detection. |
| `CTeleportAction vtbl = 0x6033B0` | genuine warp teleporter (parent chain `CSwitchMapAction 0x6032C4` → `CRelocateAction 0x603338` → `CTeleportAction`); the portal detour filters `[action]` to this. |
| `CTouchingPolygonTriggerAI vtbl = 0x5EDC20` / `CTouchingOvalTriggerAI = 0x5EDB58` | touch-trigger volumes (Enter Action @+0x20, Exit @+0x24, Repeat @+0x28; "Triggered By" filter bytes @+0x10..+0x14). Portals can be a trigger's Enter Action OR a script/event-driven `CTeleportAction` referenced by name — hence runtime execute-hooking instead of per-map structure walking. |
| `sub_4FB0A0` | `__thiscall(char/entity, &out_pos)` world-position getter, `ret 4` |
| `sub_426860` | `__usercall(ECX=char, EDX=item-def KEY) -> EAX carried count` — the engine's own carried-mineral getter (walks `char->vtbl[+0x90]()` inventory, matches `sub_482DE0(item) == key`, returns `item->vtbl[+0xA4]()`); preserves ebx/esi/edi/ebp |
| `sub_48D030` / `sub_48D150` | `__thiscall(char) -> double` current / max health (cur = max − cur_damage@+0x7C clamped ≥ 0; max = model proto + 0x20) — so "full health" reduces to `[char+0x7C]` bits == 0 |
| `sub_5B06E0` / `sub_56F710` | pickup-USEFUL predicates (`__stdcall(char) ret 4`, AL=1): energy (carried CBatteryInventoryItem charge item+0x18 < capacity def+0x4C) / shield (same over CShieldInventoryItem; none carried ⇒ 0). Vtbl slot 32 of the charge-pickup classes (0x604538 / 0x5FFE18); NULL+is-a-guarded, callee-saved preserved. Consumed by `goody_update_need` |
| `sub_425860` | `__thiscall(inventory, class_desc) -> item*` — find carried inventory item BY CLASS descriptor (the by-class sibling of the by-def-key `sub_426860`) |
| `sub_591FC0(dword_6C0C08, "Ore Deposits"/"Crystals", -1)` | the item-def KEY resolve the SK stats sync (`sub_5616B0`) makes and caches in `dword_713160`/`dword_71315C`; `load_sk` mirrors it per match into `sk_def_ore`/`sk_def_crystal` (engine strings at `0x60B7D4`/`0x60B7C8`) |
| `sub_561AB0` | `CSalvageKingScoreMineralsAction` per-target apply: consumes the toucher's WHOLE mineral load, `stats+0xF6 += OrePointsValue(+0xC8)*ore + CrystalPointsValue(+0xCC)*crystals`, checks the score limit. NO ownership check here — ownership is the map-script `CIsOnSameTeamAction` gate (toucher entity team `+0x24` == bin part `Team Number` == `NN-1`) |
| `sub_5A6E60` | `CDropAllOreAndCrystalsAction` per-target apply (`this`=ECX, victim at `[esp+8]`, `ret 0x10`; prologue `83 EC 10 53 55 56`): spawns the UNNAMED death pile (clone of the `Ore_Crystals01` model template, placed within 500 px via `sub_4EB7B0`) holding the victim's whole load; bails when nothing carried. Detoured for pile self-registration |
| `sub_5AB9B0` | secondary-item deploy (`__stdcall(char) ret 4`, prologue `83 EC 1C 53 55 56 57`): Secondary-group selection lookup, can-fire gate `item->vtbl[+0x98]` (pure checks: `sub_42A4A0` → def vtbl+0x8C `sub_5B8020` — reuse delay + rounds + `!(char+0x1C & 0x10000000)`), round consume (item vtbl+0x5C), deployed entity from `[def+0x20]` via `sub_5176F0(key, CEntityProjectile-desc `sub_491930`, char, 0, -1)` placed AT `sub_4FB0A0(char)`, layer-register `sub_4EB6F0`, then the def's New Shot Action `[def+0x54]` (MP branch deletes the mine after ~15 s). Only in-image caller = the pending-action event execute `sub_5AB970` (human right-click enqueues at `0x5448be`, in an IDA-undefined region). Detoured for mine registration |
| `"Proximity Mine"` / `"Secondary"` | item-def name @`0x6251E0` (resolve `sub_523DF0(dword_6C0C08, name, -1)` — the `[item+8]`/`sub_426860` key space) / inventory-group name @`0x60B788` (resolve `sub_523DF0(dword_6C0800, name, -1)` — the engine's own call at `0x544876`) |
| `sub_425350` / `sub_425470` / `sub_424F60` | inventory-group iterate: `sub_425350(inv; prev_id or -1, group_key) -> next id, ret 8` — **WRAPS past the group end** (returns the FIRST item again after the last; -1 only for an empty group) — / `sub_425470(inv; -1, group_key) -> LAST item id, ret 8` — the engine's own loop-termination guard (`0x543a48`/`0x544973`); every group walk must stop on `id == last` — / `sub_424F60(inv; item_id) -> CInventoryItem*, ret 4` |
| `stats + 0xEE/+0xF2/+0xF6` | SK replicated WORDs: carried ore / carried crystals (mirrored by SK gametype vtable `+0xA0` = `sub_5616B0`) / SK score |
| `VT_SK_VA + 0xA0/+0xA8` | SK-only vtable slots: carried-mineral stats sync (`sub_5616B0`) / scoreboard cell renderer (`sub_561760`) |
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
7. The inventory-group iterator `sub_425350` WRAPS: with prev = the
   group's last item it returns the FIRST item again, and -1 only for an
   EMPTY group. Any walk that terminates on -1 alone spins forever on a
   single-item group — this froze the game at bot spawn (2026-07-23,
   weapon-equip tick; a welder-only bot cycled its own weapon endlessly)
   and silently overcounted the weapon-need test. Mirror the engine's own
   loops (`0x543a48`/`0x544973`): fetch `sub_425470(inv; -1, key)` = the
   LAST item id first and stop when the walk reaches it.

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
- CTF dropped-flag pursuit — GRAPH-ROUTED (v2) DONE (see the dropped-flag
  pursuit bullet in "Current state"): the periodic grid walk name-matches
  the dropped copy while `flag_present[i] == 0`, binds it to a graph node,
  and latched bots descend a per-drop `bfs_run` row to it (straight steer
  only within the 160 px direct radius, with press patience), crossing
  teleport pads via the same pad-hop emission as goal routing (the missing
  pad pass was the live-reported two-waypoint shuttle at Hydro pad-entry
  nodes — fixed). Bots whose GOAL flag is the drop route to it from
  anywhere. Remaining refinements: the drop row uses full-field semantics
  (closed doors are walked at, not routed around — the wedge machinery
  covers them); and a drop lying inside a prop's collision pocket can be
  physically untouchable from outside (live-observed once: closest approach
  ~47 px across full sweep cycles) — pursuit retries every
  `CTF_DROP_RETRY_COOLDOWN_FRAMES`, which bounds the cost. The old
  "attacker at a far ENEMY base cannot steal until the host wakes the area"
  gap is CLOSED by `BOT_PARTICIPANT_POS_ENABLED` (the bot's own activation
  rect keeps the enemy-base flag + PassThrough steal trigger simulated —
  and equally keeps a dropped copy's touch script simulated near a far
  pursuing bot). SK bots still need collector-aware return paths.
- Reintroduce hazard/pickup awareness as GRAPH-AWARE routing (route through
  nodes near pickups, around lava) rather than the removed vector-field
  perturbation that pushed the heading into walls.
- SK mode awareness — DONE (see the Salvage King bullet in "Current state"):
  collect/return phases over the multi-source mineral field + per-bin rows,
  team-bound bin deposits through the press machinery, death-pile
  registration + opportunistic pursuit. The old pickup-overlay gap ("only
  ~80-90% of ores marked") was the 96-slot pickup table saturating — every
  SK map exceeds it (The Foundry: 502 pickups); now 512. FIRST LIVE PASS
  PENDING. The pile straight-steer wall grind was live-reported and fixed
  by the graph-routed GOODY pursuit layer (see its bullet in "Current
  state"), which also gave every mode an opportunistic graph-safe filler
  divert (health/energy/shield); per-run deposit thresholds randomize in
  [30, 100]. NEED-gated filler pursuit is DONE (2026-07-22; see the goody
  Behavior bullet — engine-native pickup-useful predicates gate the item
  scan per category). Candidate refinements: prefer crystals (3× points)
  when zones tie; SK-aware fire/aim target priority (attack carriers
  near their bin).
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
- Proximity mines — PLACEMENT + OWN-MINE AVOIDANCE DONE (see the mine
  bullet in "Current state"; placement/overlay live-confirmed 2026-07-23;
  avoidance narrowed to the bot's OWN mines the same day per user — a
  mine kills anyone including its placer and CTF teammates regardless of
  the friendly-fire setting, and bots must stay killable by other
  players' mines). CTF TERRITORY PLACEMENT DONE (2026-07-23, live pass
  pending): the BFS-classified enemy-half/middle/own-half gate in
  `mine_tick` — see the Current state bullet. Remaining: possible finer
  CTF rules (e.g. bias toward flag-route chokepoints or the enemy-base
  approach specifically, rather than anywhere in the enemy half);
  PC2-placed mines are invisible to the ring (remote
  deploys run client-side — would need the entity-replication creation
  path or a periodic model-matched grid scan; irrelevant while avoidance
  is own-only, PC2 can't be a bot); and the avoid/spacing radii are
  first-guess knobs (`MINE_AVOID_RADIUS_SQ` 96 px) — tune live.
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
  door — chaining across rounds. The roam-time WANDER-BUMP is DONE too
  (same bullet): goal-less/roaming bots occasionally press adjacent blocked
  switches, so switch+door interaction no longer requires a routed goal
  (built 2026-07-19, offline-verified; first live pass pending — note "bot
  collision fires a CollideTrigger" is still live-unverified, this layer is
  the test). Remaining refinements: the final-approach
  wedge (goal node reachable but the last straight leg to the flag blocked
  by a pillar) does not trigger a seek directly, only the wedge/suspension
  roam; and the pair-blocked candidate filter could additionally skip doors
  the bot's own team can already open per `edge_pass` (minor attacker-side
  detour noise). Do NOT blanket-wake door triggers near bots (Active-bit
  forcing) — same hazard class as the checker re-arm bug; the
  participant-rect path is safe because the grid collect masks on the
  Active bit.
