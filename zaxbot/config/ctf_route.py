"""CTF flag routing + dropped-flag pursuit."""

# --- CTF flag routing (bots navigate the waypoint graph toward flags) ----
# Master gate. When on and the active match is CTF with a graph + flags, bots
# route through the authored waypoint graph toward a flag base instead of
# roaming randomly: NOT carrying -> head to the ENEMY base; carrying the enemy
# flag -> head to OWN base to capture. The path is a true shortest path — a
# per-base BFS hop-distance field (flag_dist) is precomputed once per match at
# load (build_flag_routes, from detour_df90); at each node arrival the follower
# steps to the neighbour with the smallest distance to the goal base (strictly
# decreasing => guaranteed progress). Falls back to the random neighbour pick
# (wp_advance) whenever routing can't apply (non-CTF, no graph, no flags, goal
# unreachable from here). If an attacker sees the enemy flag absent from its
# base, the bot rolls a stable temporary policy for that missing-flag episode:
# search by random waypoint roaming, or keep routing toward the missing flag's
# base to wait/patrol nearby. If a carrier's OWN flag is absent from home, it
# always searches instead; the far-base force-tick is also gated on
# flag_present[home] so bots cannot score at an empty home base. Routing to the
# live dropped-flag position remains future work. See ctf-flag-detection,
# ctf-flag-carry-detection.
CTF_FLAG_ROUTING_ENABLED = True
# --- CTF dropped-flag pursuit ---------------------------------------------
# When a flag is away from its base (flag_present[i] == 0) the periodic grid
# walk (scan_portal_active cadence) also looks for the DROPPED world copy —
# the script-created CEntityAnimated the drop-on-death canned script names
# exactly "Red Flag" / "Blue Flag" (Data.dat "Does player have a flag";
# entity name read via [ent+0x18]+8, the sub_4FBF20 CString chain) — and
# records its position in flag_drop_pos[] / flag_drop_valid[] and binds its
# nearest graph node into flag_drop_node[] (drop_route_refresh then fills a
# per-drop BFS hop field, drop_dist, rooted at that node). Pursuit is
# TWO-PHASE (v2 — the v1 straight-steer-only pursuit was live-diagnosed
# giving up after one 30-frame watchdog window and cooling down 4 s, the
# "runs at it, then ignores it" report; and it beelined into walls):
#   * ROUTED: a latched bot beyond the direct radius descends drop_dist at
#     every node arrival (drop_next_hop overrides ctf_next_hop while latched
#     and not suspended), so walls are routed AROUND via the graph.
#   * DIRECT: within CTF_DROP_DIRECT_RADIUS_SQ — or standing on the drop's
#     own graph node — steer straight at the copy through the standard
#     watchdog, with CTF_DROP_PRESS_PATIENCE fresh cycles before giving up.
# LATCHING: any bot within CTF_DROP_PURSUE_RADIUS_SQ opportunistically
# diverts; a bot whose GOAL flag is the missing one (route_missing_goal —
# attackers whose steal target is dropped, carriers whose home flag is
# dropped) latches from ANY distance, replacing the blind search/wait roam
# with a route to where the flag actually lies. Touching a dropped flag is
# beneficial for EITHER team (same team returns it home, the enemy picks it
# up), so there is no team filter. The name match is exact and gated on
# flag_present[i]==0, which also excludes the 7 authored at-base blue-flag
# icons that carry the same name — they are consumed the moment the flag is
# stolen, so no world entity collides with the name while a flag is away
# (census pinned in tests). Stale-position windows are bounded by one scan
# interval (PORTAL_ACTIVE_SCAN_INTERVAL).
CTF_DROPPED_FLAG_ENABLED = True
# Opportunistic divert radius (squared px): a bot passing within sqrt(this)
# of a drop takes it even when its own goal lies elsewhere.
CTF_DROP_PURSUE_RADIUS_SQ = 350.0 * 350.0
# The divert ends (touch assumed) within sqrt(this) — mirror of
# PICKUP_REACHED_RADIUS_SQ: the flag's own PassThrough/touch script consumes
# the copy on overlap, so the bot only needs to overlap it.
CTF_DROP_REACHED_RADIUS_SQ = 24.0 * 24.0
# Straight-steer phase radius (squared px). Beyond it the bot node-routes via
# drop_dist; within it (or at the drop's own bound node, where the graph can
# take it no closer) it walks straight at the copy. Keep near the waypoint
# spacing scale — the v1 failure was straight-steering over 250+ px of
# geometry.
CTF_DROP_DIRECT_RADIUS_SQ = 160.0 * 160.0
# A latched OPPORTUNISTIC pursuit is silently dropped beyond sqrt(this)
# (knockback, detour drift). Objective bots (route_missing_goal == the drop)
# are exempt — they route from anywhere.
CTF_DROP_ABANDON_RADIUS_SQ = 700.0 * 700.0
# Direct-phase press patience: a progress-timeout grants this many fresh
# watchdog cycles (wall-slide keeps sweeping) before the retry cooldown.
# Live snapshots caught a drop lying in a collision pocket where the bot's
# closest approach was ~47 px across two cycles — a third cycle buys one
# more full sweep before the 4 s blacklist (rare-awkward-drop mitigation).
CTF_DROP_PRESS_PATIENCE = 3
# After ENDING a pursuit by reaching the spot, don't re-latch for this many
# thinks. MUST exceed PORTAL_ACTIVE_SCAN_INTERVAL: the consumed copy's stale
# position survives in flag_drop_valid until the next scan clears it, and a
# shorter cooldown would re-latch the bot onto the ghost.
CTF_DROP_GRAB_COOLDOWN_FRAMES = 150
# After direct-phase patience is exhausted (drop nearby but physically
# unreachable even with the slide sweep), blacklist pursuing for this many
# thinks so the bot resumes the graph. It retries automatically afterwards
# if the flag still lies there.
CTF_DROP_RETRY_COOLDOWN_FRAMES = 240
# --- CTF attacker/defender roles ------------------------------------------
# Spawned CTF bots ALTERNATE roles per team: the 1st bot spawned onto a team
# is an ATTACKER (classic steal-the-flag behaviour), the 2nd a DEFENDER, the
# 3rd an attacker again, and so on — one blue + one red bot are BOTH
# attackers. The role is derived at SPAWN-SUCCESS time from the LIVE team
# composition (count of already-living same-team bots, & 1), NOT from a raw
# attempt counter: live snapshots (2026-07-22, four sessions) caught
# role_spawn_count holding 6 phantom increments per session — failed adds
# (team/session full) consumed parity, so a failure landing between two
# successes gave a team A,A instead of A,D (the reported "more defenders on
# one team, more attackers on the other, depending on which team I play" —
# the human's team fills differently). role_spawn_count remains as a
# success-only diagnostic. bot_role[slot] is a BIT FIELD: bit0 = defender,
# bit1 = the attacker's route lane (see CTF_LANE_SPLIT_ENABLED). Non-CTF
# spawns are always role 0 (the role only gates CTF goal selection).
#
# A DEFENDER holds near its OWN base instead of raiding: while its current
# node's distance to the home base (the per-match flag_dist BFS field, in
# WP_EDGE_LEN_QUANTUM units) is within the per-map defend radius it reports
# NO goal — random near-base waypoint roaming; the moment it drifts beyond
# the radius, ctf_pick_goal flips its goal to the HOME base and normal
# routing walks it back inside (self-correcting tether — the bot patrols the
# base region). A defender that picks something up still behaves correctly:
# carrying ANY flag uses the untouched carrier machinery (route home,
# capture/return), and the opportunistic dropped-flag pursuit (350 px) makes
# it return its own dropped flag near the base. Defenders skip the roam
# portal-wander roll (a teleporter ride is desertion, not patrolling).
CTF_DEFENDER_ENABLED = True
# Defend radius = this percentage of the MAP'S span as seen from the base —
# max finite flag_dist[base][*] over the graph (computed per base per match
# by build_flag_routes), so bigger maps give proportionally bigger patrol
# zones. In BFS distance units (WP_EDGE_LEN_QUANTUM px each).
CTF_DEFEND_RADIUS_PCT = 30
# Lower clamp for tiny/degenerate maps, in the same quanta (16 = ~256 px).
# Must stay <= 127 (imm8 compare in the builder).
CTF_DEFEND_RADIUS_MIN = 16
# --- Attacker route-lane split --------------------------------------------
# With several attackers per team, the deterministic BFS descent sent them
# all down the IDENTICAL shortest path — a single-file conga line
# (user-reported: "many bots choose the same path; at most 2 should share a
# route"). Each team's attackers are split into two LANES at spawn (packed
# into bot_role bit1: attacker ordinal 0,1 -> lane 0; 2,3 -> lane 1; 5th+
# wraps): lane 0 descends the goal field exactly as today (minimum-distance
# neighbour = the shortest path); lane 1 still requires every hop to
# STRICTLY DESCEND the field (progress stays guaranteed — monotone descent
# terminates at the goal) but picks the LARGEST descending neighbour, so at
# every fork it peels onto the alternative branch. Where the graph offers no
# choice (corridors) both lanes converge — correct, there is only one way.
# CTF maps are near-symmetric, so forks with two descending branches are
# exactly the left/right route splits. Carriers ALWAYS take lane 0 (deliver
# on the shortest path), as do defenders, seek descents and the
# drop/chase/SK pursuits (objective-direct by design).
CTF_LANE_SPLIT_ENABLED = True
# --- CTF carrier STANDOFF tether ------------------------------------------
# A bot carrying the enemy flag while its OWN flag is also away cannot
# capture until the home flag returns (the vanilla checker rule), so
# whole-map search roaming just walks the carrier away from where the
# capture will happen. With this on, the carrier-with-missing-home path in
# ctf_pick_goal applies the SAME per-frame tether as the defender role
# (shared cpg_tether helper + the map-scaled defend_radius): route toward
# the HOME base while beyond the radius, random near-base roam inside it —
# so the carrier hovers around its base, ready to capture the instant its
# flag returns. The dropped-flag pursuit still outranks this (missing_goal
# keeps being written): if the home flag is DROPPED, the carrier routes to
# it from any distance to return it. The tether flips to no-goal inside the
# radius (goal-node dist 0), so the carrier still never final-approaches
# the empty home base. Standoff carriers (and defenders) also skip the roam
# portal-wander roll — a pad ride would dump them across the map.
# Requires CTF_DEFENDER_ENABLED (shares its radius infrastructure).
CTF_CARRIER_STANDOFF_ENABLED = True
# --- CTF enemy-carrier CHASE ----------------------------------------------
# "If a bot sees an enemy flag carrier nearby, chase it." The perception scan
# (pick_target) already walks every char with a team filter, so a candidate
# that passes the filter AND carries a flag is by construction an ENEMY
# carrying THIS BOT'S OWN team flag (nobody can carry their own flag — the
# same-team touch returns it). While the bot's home flag is away, each such
# sighting within CTF_CHASE_RADIUS_SQ with clear LOS stamps shared per-flag
# intel (chase_pos/chase_ttl, keyed by the home flag idx — any teammate's
# sighting refreshes it) and latches the SEEING bot's pursuit
# (bot_chase_flag). Movement is TWO-PHASE like the dropped-flag pursuit v2
# (the v1 straight-steer lesson: a target behind a wall must be routed
# AROUND, not ground at): beyond the direct radius the bot descends a
# per-flag BFS row rooted at the carrier's bound graph node (chase_dist,
# rebuilt by the page flip when the carrier changes nodes; pad hops emitted
# like every other descent), and only steers STRAIGHT at the carrier inside
# CTF_CHASE_DIRECT_RADIUS_SQ (or standing on the carrier's own node).
# Because the target MOVES, the direct-phase stall signal is the physical
# stuck detector (position delta), not dsq improvement — a fleeing carrier
# grows dsq while the chaser runs at full speed. Killing the carrier drops
# the flag, and the dropped-flag pursuit (which outranks the chase) takes
# over to return it. Carriers themselves never chase (deliver first), and a
# chase never latches while the bot's own flag sits at home (no carrier can
# exist then — the check costs nothing in the common case).
CTF_CHASE_ENABLED = True
# Sighting radius² (px²): a carrier seen (LOS-verified) within this latches
# the chase. Slightly beyond FIRE_RANGE_SQ (300 px) so chasers close into
# weapon range.
CTF_CHASE_RADIUS_SQ = 400.0 * 400.0
# Straight-steer phase radius² — mirror of CTF_DROP_DIRECT_RADIUS_SQ; keep
# near the waypoint spacing scale so the straight leg cannot span geometry.
CTF_CHASE_DIRECT_RADIUS_SQ = 160.0 * 160.0
# Silently unlatch beyond this d² (carrier outran the chaser / teleported).
# Generous: a ROUTED path legitimately moves away from the target around
# walls while the carrier also moves.
CTF_CHASE_ABANDON_RADIUS_SQ = 700.0 * 700.0
# Sighting memory (frames): the shared intel stays live this long after the
# LAST sighting by anyone; the pursuit ends when it expires. Ticked once per
# frame at the page flip.
CTF_CHASE_TTL_FRAMES = 90
# After a direct-phase pinned timeout (physically stuck a full watchdog
# window — wall micro-feature or body-block), this bot stops chasing for
# this many thinks (it keeps SHOOTING; fire targeting is independent).
CTF_CHASE_COOLDOWN_FRAMES = 240
# Guard the engine score action itself: a map-script capture point award is
# suppressed when the scoring team's own flag is away from base or carried by a
# player. Last-resort backstop behind the event-driven flag_present[] — with
# checker wake-ups gated correctly this should never fire. The old companion
# guard on CUseInventoryItemAction was REMOVED: the drop-on-death canned script
# consumes the dying carrier's flag through the same action, so that guard
# wrongly blocked flag drops whenever both flags were out.
CTF_SCORE_GUARD_ENABLED = True
# Flag bases the BFS distance field is precomputed for (CTF always has 2).
# flag_dist costs FLAG_ROUTE_MAX * OVERLAY_VERTEX_MAX dwords of scratch.
FLAG_ROUTE_MAX        = 2
# Per-bot routing suspension. BFS routing is deterministic, so a bot whose
# shortest path is physically blocked (closed door the camera-gated engine
# never opens, geometry pinch the slide can't clear) would be funnelled back
# into the same blocked segment forever — visible as a carrier pinned at
# "certain waypoints" until the goal changes. After a routed progress-timeout
# (including the CTF final approach to the flag itself), the bot gives up
# routing for this many frames and roams the graph randomly (the same
# behavior that visibly un-sticks it when the flag state changes), then
# routing resumes automatically.
WP_ROUTE_SUSPEND_FRAMES = 240
# Wedge-cluster HARD RESET (live-diagnosed 2026-07-20, Battle on the Ice R
# snaps 1-3): a bot on the WRONG SIDE of a wall/door whose latched nodes sit
# across it cycles the local recovery forever — the alternate-neighbour path
# only explores neighbours of prev (all across the wall), retreat swaps within
# the same pair, and the unlatched reacquire re-picks the Euclidean-nearest
# node (also across the wall; live: cur flipped 77<->47 with prev=78 while the
# bot stood north of the closed south door, and the reachable around-route via
# node 48 was never tried). After this many consecutive recovery actions
# WITHOUT a single node arrival, the follower cold-acquires the nearest node
# EXCLUDING the wedge cluster (failed cur, prev, and the failed-edge marker's
# two nodes) via wp_find_nearest_ex — on the live geometry that excludes
# {47,77,78} and picks 48, the entry to the around-route. The marker is KEPT
# through the reset as wedge memory. Any genuine arrival resets the counter.
# Must stay <= 127 (imm8).
WP_WEDGE_RESET_CYCLES = 3
# FIGHT-STALL suppression (user-reported 2026-07-20: "bots that hold the flag
# do not always escape to the base when engaged in fighting"): a routed
# progress stall with a live enemy this close (d^2, px^2) is usually the fight
# itself — knockback and body-blocking — not geometry. Arming the routing
# suspension there made ctf_pick_goal report no goal, so a flag CARRIER
# roamed randomly mid-fight instead of pressing home. While the fire scan's
# per-bot enemy-near stamp is set, the progress-timeout skips the suspension
# (markers, alternates and the wedge hard reset still run). 240 px ~ just
# beyond melee/body-block range; raise toward FIRE_RANGE if carriers still
# loiter in longer-range duels.
FIGHT_STALL_RADIUS_SQ = 240.0 * 240.0
# Physical-length routing quantum: every graph edge's traversal cost in the
# shared BFS (bfs_run — full/open/seek/drop fields alike) is its pixel length
# divided by this, rounded, min 1. Hop counting was live-refuted on Hydroplant
# Bouncefest: the through-door route and the around-the-top route are both 9
# hops, so routing (and the seek benefit gate) saw zero gain from opening the
# switch-doors even though the door route is 681 px (~26%) shorter. With
# 16 px quanta a 3000 px map maxes out around ~200 units — far from the
# 0xFFFFFFFF unreachable sentinel. Teleport pads keep cost 1 (near-free,
# strongly preferred — matches their old +1 hop).
WP_EDGE_LEN_QUANTUM = 16.0
# The failed-edge marker must be RETRIED, not kept forever. Live CE analysis
# of the reported "carrier stuck near a door" showed the exact loop: the
# marker held the door edge (15,17) long after the door became passable;
# routing wanted 17->15 every arrival, the marker forced the random fallback,
# and node 17's only other neighbour bounced the bot straight back — an
# arrival-level ping-pong with zero timeouts, so the suspension never fired.
# Manually clearing the marker in CE made the bot walk through and capture
# within seconds. After this many consecutive routed hops forced off the
# marked edge, the marker is cleared so the edge is retried: if it is open the
# bot simply passes; if it is still blocked the wedge timeout re-marks it and
# the roam suspension takes over.
WP_ROUTE_BLOCK_RETRY_HITS = 3

