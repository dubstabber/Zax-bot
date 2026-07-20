"""Teleporter detection + portal routing (pads as directed graph edges)."""

# --- Teleport/portal detection -------------------------------------------
# Portal source trigger centers are extracted from Data.dat at patch-build time
# by parsing .zax map text records. Runtime code only compares the active map
# name against this compact static table and copies the matching points into
# portal_table on match change, so no heap-wide scanning happens in-game.
PORTAL_TABLE_MAX        = 32  # live overlay points, float[2] each
PORTAL_STATIC_MAP_MAX   = 8   # shipped Data.dat currently has 4 portal maps
PORTAL_STATIC_POINT_MAX = 32  # shipped Data.dat currently has 10 portal points
PORTAL_MAP_NAME_SLOT    = 96  # fixed ASCII bytes per map path, including NUL

# --- Teleport/portal ROUTING (bots use portals as directed graph edges) ----
# When a portal's build-time parse also resolves its teleport DESTINATION
# (Data.dat `New Location` -> a positioned Level Part, e.g. Hydro Vengence's
# four warm/cold pads), the portal becomes a DIRECTED edge for CTF routing:
# bind_portal_nodes (per match, after wp_load + load_portals) binds the source
# pad and the destination point each to their nearest graph node, and bfs_run
# relaxes source_node -> dest_node in every distance field it fills (full,
# per-team open, switch-seek). On Hydro Vengence the two arenas are only
# connected through the pads, so without this the enemy base is BFS-
# unreachable and CTF bots can neither steal nor return flags. At node
# arrival, when the pad at the current node is the shortest next hop,
# ctf_next_hop reports a portal hop instead of a neighbour node and the
# follower latches a PAD FINAL-APPROACH: steer at the pad center (same
# watchdog as the flag final approach — no progress ramps the wall-slide,
# a full timeout clears the latch and suspends routing). The teleport itself
# is detected position-side: any per-think jump farther than
# sqrt(PORTAL_JUMP_REACQUIRE_DIST_SQ) drops the whole nav latch and
# cold-acquires the NEAREST node at the arrival point — this also covers
# bots knocked through script teleporters they never chose. Portal fields
# are NOT rebuilt when a pad's active state flips mid-match (only door
# changes rebuild); a route through a currently-inactive pad ends in the
# standard blocked-route machinery (watchdog -> suspension -> roam), and the
# live portal_active[] gate below keeps the next-hop/latch itself honest.
PORTAL_ROUTING_ENABLED = True
# Roaming bots (DM matches, and CTF bots whose routing is suspended or whose
# goal is missing) occasionally step INTO an adjacent active pad: at each
# node arrival that falls back to the random neighbour pick, if the current
# node is some active pad's nearest node, roll RNG(0..99) < this and latch
# the pad approach instead of a neighbour. 0 disables. This is the whole
# "teleports are part of the wander space" behaviour asked for DM maps —
# no destination knowledge needed (the jump re-acquire recovers the graph).
PORTAL_WANDER_CHANCE = 25
# The wander-entry roll is SKIPPED while the bot's routing is suspended
# (suspension roam is a LOCAL unstick — live snapshots caught a suspended
# CARRIER bouncing arena-to-arena on the roll) and for this many thinks after
# any teleport (each pad's exit node IS the return pad's node, so the very
# next arrival would re-roll the coin — the observed pad ping-pong). Routed
# pad hops are unaffected (they only fire on strictly-descending distance).
PORTAL_WANDER_COOLDOWN_FRAMES = 600
# Pad-press patience (mirror of WP_DOOR_PRESS_PATIENCE): a pad final-approach
# whose progress watchdog times out gets this many fresh watchdog cycles —
# the wall-slide sweep keeps hunting for the thin trigger sliver around the
# collidable teleporter prop — before the latch is dropped and routing
# suspends. 3 cycles ≈ 2 s of pressing at 60 Hz.
PORTAL_PRESS_PATIENCE = 3
# Post-teleport RETURN-PAD heading veto (the anti-ping-pong virtual wall).
# Live proute snapshots pinned the residual bounce loop: the teleport drops
# the bot at the exit marker inside a collision pocket around the teleporter
# prop, ~28 px from the RETURN pad's thin trigger sliver; the wall-slide
# sweep rotates the blocked heading and the first direction that actually
# moves the bot walks it across that sliver — an ENGINE re-teleport, no bot
# decision involved, so the wander gates could not stop it. While the
# post-teleport cooldown runs, the emitted heading is vetoed (rotated on,
# lava-veto style) whenever its lookahead point lands within sqrt(this) of
# any pad center the bot has NOT deliberately latched (the latched pad must
# stay enterable — returning through a pad is often the correct route).
# 40 px rejects every sliver-ward heading from the exit pocket while leaving
# the directly-away and along-the-ledge escapes open; the lookahead distance
# reuses LAVA_LOOKAHEAD_PX and the sweep step LAVA_SWEEP_STEP_DEG.
PORTAL_VETO_RADIUS_SQ = 40.0 * 40.0
# A bot that moves farther than sqrt(this) between two consecutive movement
# thinks has been teleported (engine step is ~1.7 px/frame; knockback stays
# well under 100 px). Fires the post-teleport nearest-node re-acquire and
# clears any pad latch. Keep far above real per-frame movement and far below
# the shortest shipped teleport span (Hydro pads jump ~1600 px).
PORTAL_JUMP_REACQUIRE_DIST_SQ = 192.0 * 192.0

