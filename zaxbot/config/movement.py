"""Bot movement policy: speeds, waypoint following, edge following and
the off-graph progress-timeout recovery."""

# --- Bot movement / wander policy (DM-only first pass) -------------------
# Master switch — set False to revert to the original zero-vector behavior
# (bots stand still). Useful for A/B-comparing fire/aim regressions.
MOVEMENT_ENABLED = True

# Bot picks a random world-space target within ±WANDER_TARGET_RADIUS px of
# its current position, walks toward it, then re-rolls when the timer expires
# or stuck detection trips. Targets aren't bounded to the map — out-of-bounds
# picks are corrected by stuck detection retargeting within a second or so.
WANDER_TARGET_RADIUS         = 600.0
WANDER_TARGET_TIMEOUT_FRAMES = 600     # ≈10 s at 60Hz

# Stuck detection: a frame counts as "not moving" when (x-last_x)²+(y-last_y)²
# is under STUCK_DELTA_SQ. Used by the random-wander fallback; waypoint
# following has its own progress-to-node timeout below. CRITICAL: this must be
# BELOW the bot's normal step (~1.7px/frame) or a steadily-walking bot is
# falsely tagged stuck. 0.25 (0.5px/frame) means only a truly-stationary bot
# accumulates.
STUCK_FRAMES_THRESHOLD = 30
STUCK_DELTA_SQ         = 0.25

# Mild item attractor: if a CPickupAI entity is within sqrt(radius_sq) and
# line-of-sight passes, blend WEIGHT * (pickup - bot) into the movement
# vector. sub_4303F0 (engine collision) handles the actual pickup on
# walk-over, so the attractor only nudges; it doesn't need to land on the
# pixel. Scan is staggered per bot at ITEM_SCAN_INTERVAL_FRAMES to spread the
# entity-iteration cost across frames.
ITEM_ATTRACTOR_RADIUS_SQ   = 40000.0   # 200px reach
ITEM_ATTRACTOR_WEIGHT      = 0.7
ITEM_SCAN_INTERVAL_FRAMES  = 30

# Proactive hazard avoidance: at match start, scan world entities of class
# CDamageExpandingRadiusAI and cache (x, y, default_radius_sq). The movement
# detour then treats each cached hazard as a repulsor scaled by inverse
# distance, blended into the base direction.
#
# Currently DORMANT — the entity-array offset hypothesis (mgr+0x2BC/0x2C0)
# was wrong (that field pair only stores 1 active char, not the full entity
# list), so the proactive scan finds nothing. The reactive cur_damage-based
# avoidance below is the active hazard-handling path. Re-enable once the
# real entity iterator is identified.
HAZARD_REPULSION_RADIUS_SQ = 90000.0   # 300px reach
HAZARD_REPULSION_WEIGHT    = 2.0
HAZARD_DEFAULT_RADIUS_SQ   = 90000.0   # used as the per-entity bubble until we read it off the AI

# Reactive hazard avoidance: when `[char+0x7C]` (cur_damage) increases, the
# bot took damage from SOMETHING — lava, fire, projectile, etc. We
# immediately bias the wander target opposite to the bot's recent motion,
# commit to the flee for HAZARD_FLEE_FRAMES, and suppress stuck-based and
# timer-based retargets during the flee. This gives the bot a deliberate
# back-off rather than a 50/50 random retarget (which would keep the bot
# wandering back onto lava). Higher values commit longer to the flee
# direction; lower values resume wander sooner.
HAZARD_FLEE_FRAMES = 120

# Per-frame velocity magnitude written to the movement vector after
# normalize. The engine reads our |v| and computes a ratio
# `(|v| - model_min) / (model_max - model_min)` clamped to [0, 1], then
# scales position by that ratio against the model's per-frame step. Without
# knowing the bot model's exact min/max bounds, this knob is empirical:
# too high ⇒ engine clamps to full-speed (bot teleports off the map and
# crashes the collision lookup); too low ⇒ engine sees |v| < min and
# treats as no movement.
#
# 1.0 = the human player's full movement speed. The engine's walking model is
# NORMALIZED to [0,1]: the original sub_542360 (the host's own movement path)
# emits a velocity whose magnitude saturates at exactly 1.0 while a movement key
# is held (it clamps an input term to a6 then divides by a6 → ≤1.0). sub_543B60
# then maps that magnitude through the walk/run animation tiers, and its run-tier
# ramp reaches a full per-frame step (v72=1.0) only at |v|=1.0. So a bot that
# emits |v|=1.0 moves at exactly the host's speed — which is the goal (emulate
# real players). 3.0 was 3× the player max (the "too fast" report); the 3.0 in
# static_data.py is a stale pre-normalization default. (The "300..4000 px/sec"
# in AGENTS.md is the unrelated *projectile* CModel max at proto+0x60, not this
# model.) Drop a touch below 1.0 only for deliberately slower-than-host bots.
BOT_MOVE_SPEED = 1.0

# --- Waypoint following (graph navigation) -------------------------------
# Master switch. When True AND a graph is loaded for the current map
# (overlay_vertex_count > 0), bots steer straight at the current waypoint node
# and advance along real edges. When False, or on a map with no graph, bots
# idle — the old random-wander/attractor potential field that perturbed the
# heading into walls was REMOVED. This is the panic switch for the follow
# feature (mirrors MOVEMENT_ENABLED, which gates all bot movement on top).
WP_FOLLOW_ENABLED = True

# At a junction (a node with several connected edges) pick a RANDOM connected
# neighbour (preferring one that isn't the node we just came from) so bots roam
# the whole graph instead of all taking the first branch. Set False for a
# deterministic "first non-prev neighbour" choice (reproducible R-dumps).
WP_RANDOM_NEIGHBOR = True

# Wall-slide: the engine moves a bot purely by the emitted ANGLE and refuses to
# move it at all when that angle points into geometry (no auto-slide). So when
# a bot is physically wedged for a few frames, the follower sweeps the emitted
# angle by this many degrees per ramp step until a heading clears the wall and
# the bot slides along it (then the deflection decays back to straight-at-node).
# Smaller = finer/smoother sweep but slower to escape; bigger = faster escape
# but coarser. ~30 deg with the in-asm ramp cap (11) sweeps ~330 deg, enough to
# clear any blocked half-plane.
WP_SLIDE_TURN_STEP_DEG = 30.0

# Radius-based arrival: a bot "reaches" its current target vertex when the
# squared distance to it drops below this, at which point it advances to the
# next node along an edge. This is the PRIMARY (and only) arrival test — a
# plane-cross-only test was tried before and BROKE following, because engine
# collision routinely stops a bot a few pixels short of the node so it never
# crosses the plane and never advances. Keep this comfortably larger than the
# bot's collision radius so a bot wedged near a corner still counts as
# arrived. R snapshots on Molten Ice showed bots physically wedged 44px from a
# corner waypoint, so 40px was too strict; 64px is still below the long corridor
# waypoint spacing but large enough to accept collision-limited corner arrival.
WP_REACHED_RADIUS_SQ = 4096.0

# Conditional "good enough" arrival for wedged bots. Normal movement still uses
# WP_REACHED_RADIUS_SQ so tight graph corners are not skipped early. But if the
# bot is already visibly stuck or not making waypoint progress and is within
# this larger radius, accept the node and advance. The latest R-dump showed a
# far CTF bot pinned ~75px from its target node with the normal 64px radius just
# out of reach; previous dumps had the same pattern at ~100px.
WP_STUCK_REACHED_RADIUS_SQ = 16384.0

# --- Edge following (hug the connection line) ----------------------------
# When latched (prev->current edge known), steer toward a point ON the
# prev->current segment instead of straight at the node, so the bot converges
# back onto the connection line after any drift (flee, wall-slide, early node
# advance) rather than cutting diagonally. This is critical on narrow lava
# corridors where "a few px off the line" means stepping into lava. Set False
# to revert to straight-at-node steering (the bot will follow more loosely).
WP_EDGE_FOLLOW_ENABLED = True
#
# Look-ahead as a FRACTION of the current edge length: the bot targets its own
# projection onto the segment plus this much further toward `current` (clamped
# to the segment, never past the node, so it can't corner-cut onto the next
# edge). Smaller = hugs the line tighter (slower to advance); bigger = leads
# more toward the node. ~0.15 is a smooth lead; drop toward 0 for the tightest
# line-hugging on very narrow paths.
WP_EDGE_LOOKAHEAD = 0.15

# --- Off-graph recovery (progress-timeout escape) ------------------------
# The bot routinely ends up physically SEPARATED from its target node by a
# wall — a bad spawn, a lava-death respawn into a pocket, or explosion/player
# knockback drops it off the authored graph. With pure edges-only steering and
# no recovery it then PINS forever: it micro-oscillates against the wall (so
# stuck_count, thresholded on raw movement, never climbs) while making zero
# progress toward its target. R-dumps showed exactly this — a latched bot
# frozen 328px off its edge in a corner across many seconds, and a committed
# bot dying on lava 245px short of an unreachable node.
#
# The fix is a PROGRESS-based pin-detector (immune to the micro-oscillation
# that defeats stuck_count): track the minimum distance² to the current target
# node achieved so far; every frame that doesn't strictly beat that minimum
# increments a stall counter. When the bot has made no real progress for this
# many frames it is genuinely wedged off-graph, so it first re-acquires the
# nearest node from the bot's current position. If that nearest node is the same
# failed target, the detour preserves the stall state and lets the angle sweep
# continue instead of resetting the escape attempt every timeout window.
# Edge-following while genuinely progressing is untouched.
# ~2.5s at 60Hz. Lower = recovers faster but may interrupt legitimately-slow
# progress (squeezing past the host); higher = visible pin before recovery.
WP_PROGRESS_TIMEOUT_FRAMES = 30

# Historical knob for the removed random-wander relocate burst. The scratch slot
# is now repurposed for WP_STUCK_REACHED_RADIUS_SQ to avoid shifting the runtime
# layout; the name is kept only for compatibility with older notes/tests.
WP_RELOCATE_FRAMES = 150

