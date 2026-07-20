"""Door detection tables + door-aware CTF rerouting knobs."""

# --- Door detection --------------------------------------------------------
# Door positions are extracted from Data.dat at patch-build time by parsing
# each multiplayer .zax map for Level Parts carrying Activity=CDoorAI (see
# zaxbot/door_data.py and the door-runtime-model notes). load_doors copies the
# active map's door centers into door_table on match change — identical to the
# portal/flag pipelines, no heap-wide scanning in-game.
#
# Door STATE is PER-FRAME fresh. The periodic grid walk (scan_portal_active,
# every PORTAL_ACTIVE_SCAN_INTERVAL frames) only maintains a per-anchor entity
# CACHE (door_entity[], up to DOOR_ENTITY_SLOTS_PER_DOOR non-character
# entities within sqrt(DOOR_ENTITY_MATCH_RADIUS_SQ) of each door anchor); the
# page-flip hook then re-reads the cached entities' SOLID flag
# (entity+0x1C & 0x40000 — set while the door is closed, cleared by the open
# path) EVERY frame into door_blocked[]. Deriving state from the walk itself
# was live-tested and rejected: the walk interval is counted in FRAMES, so
# with the overlay visible (low FPS) 120 frames stretched to many seconds and
# the door rings looked permanently stale (toggling the overlay off restored
# FPS, let a scan through, and "fixed" it). Trigger pads / markers cached at
# the same anchor are non-solid so they never false-positive; live player
# characters are excluded from the cache exactly like the CTF flag anchor
# cache (a bot standing in an open doorway is SOLID but is not a door).
# Consumers:
# - failed-edge fast retry: a marker set while wedged against a blocked door
#   clears the moment that door reads passable again, instead of waiting out
#   the blind WP_ROUTE_BLOCK_RETRY_HITS cadence.
# - door-aware CTF rerouting (DOOR_ROUTE_AWARE_ENABLED below): a second BFS
#   field excludes closed-door edges so bots actively route around them.
# Bots are NOT hard-barred from closed doors — many doors open on approach
# (proximity trigger / touch-open), so the open-field routing always falls
# back to the full field when no door-free path exists.
DOOR_DETECT_ENABLED = True
# Entities cached per door anchor. The door entity itself plus up to two
# co-located authored pieces (arming pad / touch trigger); mirrors the
# FLAG_ENTITY_SLOTS_PER_FLAG eviction rationale.
DOOR_ENTITY_SLOTS_PER_DOOR = 3
# Live per-map door table. Curse of the Temple authors 186 CDoorAI doors —
# the largest shipped MP count — so the cap must sit above that.
DOOR_TABLE_MAX        = 192
DOOR_STATIC_MAP_MAX   = 12   # shipped Data.dat has 10 MP door maps
DOOR_STATIC_POINT_MAX = 384  # shipped Data.dat has 333 MP door points
DOOR_MAP_NAME_SLOT    = 96   # fixed ASCII bytes per map path, including NUL
# An entity "sits on" a door anchor when within sqrt() of it. The authored
# Level Part position IS the entity's raw +0x4C/+0x50, so this only needs to
# absorb float noise — keep it tight so nearby genuinely-solid scenery can't
# claim the anchor.
DOOR_ENTITY_MATCH_RADIUS_SQ = 24.0 * 24.0
# When the progress watchdog marks a failed edge, the nearest currently-
# blocked door within sqrt() of the BOT (it is physically pressed against the
# obstacle at that moment) is recorded alongside the marker. Generous enough
# to cover door half-width + bot collision radius; a wrong latch is self-
# correcting (marker just falls back to the blind retry cadence).
DOOR_WEDGE_MATCH_RADIUS_SQ = 96.0 * 96.0

# --- Door-aware CTF rerouting ----------------------------------------------
# The single per-match BFS field always funnels a bot down the SHORTEST path,
# so a bot pinned at closed door A never diverted to an alternative corridor
# the moment door B opened (live-reported: two blocked ways to the enemy
# flag; opening the second one did not reroute the bot). With this on,
# build_flag_routes also computes flag_dist_open — the same per-base BFS but
# SKIPPING every graph edge that crosses a currently-blocked door — and
# ctf_next_hop prefers the open field, falling back to the full field
# whenever the goal is unreachable without passing a closed door (so
# approach-openable doors — proximity pads, touch-open — still get walked
# at exactly like before). door_blocked[] changes (per-frame refresh) mark a
# dirty flag; the page-flip hook then rebuilds ONLY the open field, debounced
# by DOOR_ROUTE_REBUILD_COOLDOWN_FRAMES (touch-open-door maps flip state
# constantly; the BFS is integer-cheap but there is no reason to run it every
# frame). Edge->door adjacency is STATIC per match (doors and the graph don't
# move): build_edge_doors records, per graph edge, the nearest door within
# sqrt(DOOR_EDGE_RADIUS_SQ) of the edge SEGMENT (point-segment distance).
DOOR_ROUTE_AWARE_ENABLED = True
DOOR_EDGE_RADIUS_SQ = 40.0 * 40.0
DOOR_ROUTE_REBUILD_COOLDOWN_FRAMES = 30
# Directional passability. A CLOSED door edge is traversable from side S iff
# a bot-usable opener (walk-in trigger — touching/pass-through volumes the
# bot fires just by moving; NOT collide switches / spawn triggers / relays /
# timers) sits on side S of the door, where side is the sign of
# dot(opener - door, node_S - door) with a +1.0 bias so an opener exactly ON
# the door (self-trigger walk-up doors) grants BOTH sides. Doors with no
# authored opener of any kind are engine bump-open => both sides. Doors with
# only non-bot-usable openers are impassable while closed — live state flips
# them the moment something opens them (spawn doors, switch doors, timer
# jaws). Team-gated self-trigger doors (Doom ship) are treated optimistically
# as openable — a wrong-team bot falls back to the wedge machinery.
# Openers per map are small (Curse of the Temple peaks at ~22); the static
# table holds every MP map's bindings (53 shipped). Each record is
# (x f32, y f32, door_idx u32, team_mask u32) — the mask restricts same-team-
# conditional walk-up doors (Doom ship / Battle on the Ice / Curse) to their
# own team; unconditional openers carry mask 3.
DOOR_OPENER_TABLE_MAX  = 48   # live per-map opener records
DOOR_OPENER_STATIC_MAX = 96   # build-time records across all MP maps
# PHYSICAL-STATE routing override. When True, the open-field BFS and
# ctf_next_hop treat EVERY currently-closed door as impassable and route
# AROUND it (using the live door_blocked[] state), ignoring the edge_pass
# team-openability bits above. Rationale: a bot far from the host's camera
# cannot open ANY door — the touch/switch triggers are camera-gated and never
# fire — so routing a carrier THROUGH a team-openable-but-closed door stranded
# it pressing a door that never opens until the host approached (live-reported
# on Battle on the Ice: a team-1 carrier committed to the openable door on its
# way home instead of the 12-hop door-free path). Routing around closed doors
# and only USING them once they read open (the epoch reroute picks them up the
# frame they flip) is robust regardless of camera distance and still honours
# team gating (a closed enemy-team door is avoided either way). Set False to
# restore the directional edge_pass behaviour (route through doors your team
# could open) — only worthwhile once bots can trigger far doors themselves.
DOOR_ROUTE_PHYSICAL_STATE = False

