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

