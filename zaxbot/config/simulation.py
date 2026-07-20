"""Far-from-camera simulation: force-active/force-tick, participant
activation points, the world entity scanner and per-portal state."""

# --- Keep bots simulated when far from the host's camera -----------------
# The engine advances an entity's components (incl. the bot walking-controller
# think sub_543B60) only when the char's ACTIVE bit (char+0x1C & 0x800000) is
# set; it DEACTIVATES entities far from the local camera (a one-shot, sticky
# transition — verified live: a cleared bit is NOT re-set per frame). So a bot
# that walks away from the host freezes mid-route (e.g. carrying the flag back
# to its base) until the host approaches. We re-set each live bot char's Active
# bit once per frame from the page-flip hook (cheap 16-slot loop, NOT a
# per-entity hot path), so the engine keeps ticking bots everywhere — in-context
# (no double-tick: the engine still advances each active bot exactly once).
BOT_FORCE_ACTIVE_ENABLED = True
# Setting the Active bit alone is NOT enough: the engine's per-frame update
# DRIVER skips entities far from the local camera entirely. Calling only the
# bot character's component advance (`sub_4FADC0`) is also NOT enough: it reaches
# the walking-controller think, but bypasses the active-entity driver's later
# position sync, so the bot computes movement without changing char+0x4C/+0x50.
# The page-flip hook therefore force-runs the same three per-entity vtable
# stages used by `sub_57A030` (+0x7C, +0x80, +0x8C with EBP=0x10000) for any
# bot the engine skipped this frame. A per-bot bot_ticked flag (0 = skipped,
# 1 = engine ticked, 2 = page-flip recovery tick) prevents double-ticking near
# bots and lets fire/aim suppress stray shots during the recovery tick. Requires
# BOT_FORCE_ACTIVE_ENABLED because those entity stages still gate on Active.
BOT_FORCE_TICK_ENABLED = True
# THE fundamental anti-culling fix: make each bot an engine-native ACTIVATION
# SOURCE, exactly like a real connected player. The MP world update
# (sub_4F37E0, virtual) collects one point per participant — the floats at
# participant+0xC0/+0xC4, on the participant whose layer index at +0xDC is
# valid — and sub_4EA350 turns each point into a screen-sized activation rect;
# sub_4E74A0 then updates every Active entity inside the union of (host
# viewport rect + all participant rects) via the sub_57A100 grid collect. Real
# clients stream their +0xC0 position over DirectPlay; nobody updates a bot's,
# so it stays (0,0) (live-verified) and the world around a far bot is never
# simulated — the root cause of far bots not opening their own team doors, not
# stealing far flags, and freezing mid-route. This flag mirrors each live
# bot's char position (char+0x4C/+0x50) into its participant's +0xC0/+0xC4
# once per frame from the page-flip hook (inside the force-active 16-slot
# loop, so it requires BOT_FORCE_ACTIVE_ENABLED). The engine then simulates
# around bots natively: touch/proximity door triggers think and fire, far
# flag steals/captures work, no force-wake needed. Safe by construction
# against the checker re-arm hazard: sub_57A100 only collects entities whose
# Active bit is SET, so script-deactivated CTF checkers stay asleep — this
# path never touches any entity's Active bit.
BOT_PARTICIPANT_POS_ENABLED = True

# Runtime portal detection: a detour on the relocate/teleport executor
# (sub_4C11A0) self-registers the SOURCE pad of every CTeleportAction warp into
# portal_table the moment any entity teleports — exactly like pickups self-
# register via the CPickupAI update detour. This catches conditional /
# script-driven portals that the static Data.dat parse cannot (those only fire
# once a map condition activates them, e.g. an objective/lock puzzle), so the
# bot learns every active teleporter on any map. The site fires only on an
# actual teleport, never per frame, so it is not a hot path. Build-gated: when
# False the detour code is still emitted (dead) but the patch site is not
# installed. Filters to genuine CTeleportAction warps (ax.VT_TELEPORT_ACTION_VA)
# so plain CRelocateAction "$return"/non-warp moves are ignored.
# --- World entity scanner (detours/entity_scan.py) -----------------------
# scan_entities walks the layer's spatial grid (mgr -> layer -> cells) and
# collects entities matching a class descriptor (0 = every entity) into a
# result table of (ptr, x, y, flags) records. Foundation for object detection
# (switches, doors, CTF flags, SK collectors, traps) and per-portal active
# state (entity flags & ax.ENTITY_ACTIVE_BIT). SCAN_ENTITIES_MAX caps the
# result table (16 bytes/record). A diagnostic pass (scan from detour_df90 on
# match change, gated below) seeds the table so the scanner can be validated
# end-to-end via the result count / R-snapshot before any bot behaviour reads
# it. The walk is bounded (rows*cols cells, 256 entities/cell, both capped) and
# only runs on match change, so it is not a per-frame hot path.
# 128 records (2KB table) comfortably covers a DM/CTF map's placed entities so
# the class=0 diagnostic doesn't truncate before reaching late-cell entities
# (the table-full guard ends the whole walk). A class-filtered scan needs far
# fewer; raise only if a dense map's count approaches this.
SCAN_ENTITIES_MAX     = 128
SCAN_ENTITIES_ENABLED = True

# --- Per-portal active-state (scan_portal_active) ------------------------
# A grid-walk consumer of the entity enumerator that, instead of collecting a
# capped table, matches every entity against portal_table and records the
# NEAREST entity's Active bit into portal_active[i]. Immune to the
# SCAN_ENTITIES_MAX cap (the table is never built), so it reaches the
# teleporter pads wherever they sit in the grid. portal_active[i] is 1 when the
# entity nearest portal_table[i] (within sqrt(radius)) has flags & ENTITY_ACTIVE_
# BIT set, else 0 — i.e. "is this pad currently usable?" (e.g. Jungle Ruins'
# two-lock key puzzle flips it). The pad entity sits ~at the portal centroid, so
# nearest-within-radius reliably picks it; 128px tolerates the source-vs-centroid
# offset (runtime pads landed ~38px off) while staying far under inter-portal
# spacing (~740px), so distinct portals never cross-match.
PORTAL_ACTIVE_ENABLED        = True
PORTAL_ACTIVE_MATCH_RADIUS_SQ = 128.0 * 128.0
# Re-scan cadence (frames) from the page-flip detour so the flag tracks dynamic
# activation/cooldown. The puzzle is solved mid-match, so a match-change-only
# scan would miss it. 120 = ~2s at 60Hz; the walk is bounded, but it is the only
# periodic (not per-frame) cost, so keep it coarse.
PORTAL_ACTIVE_SCAN_INTERVAL = 120
PORTAL_REGISTER_ENABLED = True
# A newly-observed teleport pad within sqrt() of an existing portal_table entry
# (static or runtime) is treated as the same pad and not re-added. The dedup is
# on the SOURCE position, which varies by where on the pad a player stands when
# they trigger it: live testing on Jungle Ruins (DM) showed the same pad
# registering twice from spots ~57px apart with a 48px radius. 128px comfortably
# merges same-pad hits (a pad is at most ~1-2 tiles wide) while staying far below
# typical inter-portal spacing (the two Jungle Ruins pads are ~740px apart), so
# distinct portals never collapse into one. Raise if a large pad still doubles;
# lower if two genuinely-separate nearby portals merge.
PORTAL_DEDUP_RADIUS_SQ  = 128.0 * 128.0
# Only register pickups that are CURRENTLY collectible. Respawning spawners
# keep ticking sub_53DA40 after the item is taken (item hidden, waiting to
# respawn), so without this their markers/targets would persist on an empty
# pad. The engine marks an item present by setting bits 0x40000|0x20000 in the
# entity flags at +0x1C (sub_53DA40's respawn path sets them; collection clears
# them); these are general "visible/active" flags, so dropped items on the
# ground carry them too and still pass (and when collected their entity is
# destroyed, so they drop out regardless). Register only when
# (flags & PICKUP_ACTIVE_MASK) == PICKUP_ACTIVE_VALUE. Set MASK = 0 to disable
# the filter and register every ticking pickup (debug / fallback).
PICKUP_ACTIVE_MASK      = 0x60000
PICKUP_ACTIVE_VALUE     = 0x60000

