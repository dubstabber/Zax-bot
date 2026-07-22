"""Shared waypoint-follow context — the VA locals + feature gates every
``follow_*`` chunk consumes.

``build_follow_ctx`` runs the (former) ``_emit_waypoint_follow`` prologue
verbatim and returns a SimpleNamespace of every local. Gate-dependent
names are pre-seeded to ``None``; chunks only touch them under the
corresponding gate (``routing`` / ``seek_move`` / ``door_gate`` /
``wedge_reset`` / ``door_reroute`` / ``portal_move`` / ``drop_move`` /
``switch_wander`` / ``sk_move`` / ``goody_move``), and a misuse fails
the build loudly (``le32(None)``).
"""

from types import SimpleNamespace

from ... import config as cfg
from ...layout import ScratchLayout


def build_follow_ctx(layout: ScratchLayout) -> SimpleNamespace:
    # Gate-dependent names default to None (assigned under their gates
    # below).
    route_goal_flag_va = None
    flag_route_node_va = None
    flag_table_va = None
    routing_active_va = None
    route_suspend_va = None
    route_block_hits_va = None
    bot_seek_va = None
    seek_active_va = None
    seek_node_va = None
    switch_table_va = None
    bot_team_va = None
    route_block_door_va = None
    door_blocked_va = None
    door_count_va = None
    door_gate_table_va = None
    door_wedge_radius_sq_va = None
    bot_wedge_cycles_va = None
    wpfn_excl_va = None
    edge_door_va = None
    door_table_va = None
    overlay_edges_va = None
    overlay_edge_count_va = None
    bot_portal_target_va = None
    portal_table_va = None
    portal_count_va = None
    route_portal_hop_va = None
    portal_active_mv_va = None
    flag_drop_valid_mv_va = None
    flag_drop_pos_mv_va = None
    flag_drop_node_mv_va = None
    bot_drop_target_va = None
    bot_drop_cd_va = None
    bot_drop_try_va = None
    bot_drop_best_va = None
    drop_enabled_va = None
    drop_radius_va = None
    drop_reached_va = None
    drop_direct_va = None
    drop_abandon_va = None
    drop_missing_goal_va = None
    flag_present_mv_va = None
    flag_count_mv_va = None
    bot_switch_target_va = None
    bot_switch_cd_va = None
    bot_switch_try_va = None
    bot_switch_snap_va = None
    sww_census_va = None
    switch_table_sw_va = None
    switch_count_sw_va = None
    sk_team_mv_va = None
    sk_suspend_mv_va = None
    sk_active_mv_va = None
    bot_sk_return_va = None
    bot_sk_dep_try_va = None
    sk_bin_table_mv_va = None
    sk_bin_valid_mv_va = None
    sk_bin_node_mv_va = None
    bot_pile_target_va = None
    bot_pile_cd_va = None
    bot_pile_try_va = None
    bot_pile_best_va = None
    sk_pile_valid_mv_va = None
    sk_pile_pos_mv_va = None
    sk_pile_radius_va = None
    sk_pile_reached_va = None
    goody_tx_va = None
    goody_ty_va = None
    goody_node_va = None
    goody_idx_va = None
    goody_scan_rad_va = None
    goody_scan_cat_va = None
    item_active_mv_va = None
    item_cat_mv_va = None
    item_radius_mv_va = None
    goody_direct_va = None
    goody_abandon_va = None
    sk_pile_dirty_mv_va = None
    bot_chase_flag_va = None
    bot_chase_cd_va = None
    chase_pos_mv_va = None
    chase_node_mv_va = None
    chase_ttl_mv_va = None
    chase_dsq_tmp_va = None
    chase_flag_present_va = None
    chase_flag_count_va = None

    bot_pos_va        = layout.va('bot_pos')
    bot_slot_tmp_va   = layout.va('bot_slot_tmp')
    current_wp_va     = layout.va('bot_current_wp')
    prev_wp_va        = layout.va('bot_prev_wp')
    wp_try_va         = layout.va('bot_wp_try')
    wp_best_dsq_va    = layout.va('bot_pickup_y_cache')   # min dsq-to-node
    stuck_count_va    = layout.va('bot_stuck_count')
    failed_edge_va    = layout.va('bot_pickup_valid')     # packed failed-edge marker
    slide_turn_va     = layout.va('bot_flee_ticks')       # wall-slide ramp
    wp_follow_enabled_va    = layout.va('wp_follow_enabled')
    wp_reached_radius_sq_va = layout.va('wp_reached_radius_sq')
    wp_progress_timeout_va  = layout.va('wp_progress_timeout')
    wp_stuck_reached_radius_sq_va = layout.va('wp_relocate_frames')  # repurposed dormant slot
    failed_cur_tmp_va = layout.va('curr_dist_sq')       # timeout spill: failed current node
    prev_tmp_va       = layout.va('cand_tmp')           # timeout spill: previous node
    overlay_vertex_count_va = layout.va('overlay_vertex_count')
    overlay_vertices_va     = layout.va('overlay_vertices')
    wp_scratch_va           = layout.va('wp_scratch')
    edge_follow_enabled_va  = layout.va('wp_edge_follow_enabled')
    edge_lookahead_va       = layout.va('wp_edge_lookahead')
    wp_seg_x_va             = layout.va('wp_seg_x')
    wp_seg_y_va             = layout.va('wp_seg_y')
    wp_tp_va                = layout.va('wp_tp')

    dx_accum_va = layout.va('curr_dist_sq')
    dy_accum_va = layout.va('cand_tmp')

    # CTF final-approach: route_goal_flag (this bot's goal flag idx), its nearest
    # graph node, and the flag base position. Present only on a routing build.
    routing = (cfg.CTF_FLAG_ROUTING_ENABLED
               and layout.has_field('route_goal_flag')
               and layout.has_field('flag_route_node')
               and layout.has_field('flag_table')
               and layout.has_field('flag_routing_active'))
    if routing:
        route_goal_flag_va = layout.va('route_goal_flag')
        flag_route_node_va = layout.va('flag_route_node')
        flag_table_va      = layout.va('flag_table')
        routing_active_va  = layout.va('flag_routing_active')
        route_suspend_va   = layout.va('bot_route_suspend')
        route_block_hits_va = layout.va('route_block_hits')

    # Switch-seek final approach: a bot descending the seek field whose node
    # IS the sought switch's node steers at the switch CENTER to bump it.
    seek_move = (routing
                 and cfg.SWITCH_SEEK_ENABLED
                 and layout.has_field('bot_seek')
                 and layout.has_field('seek_active')
                 and layout.has_field('seek_node')
                 and layout.has_field('switch_table')
                 and layout.has_field('bot_team'))
    if seek_move:
        bot_seek_va     = layout.va('bot_seek')
        seek_active_va  = layout.va('seek_active')
        seek_node_va    = layout.va('seek_node')
        switch_table_va = layout.va('switch_table')
        bot_team_va     = layout.va('bot_team')

    # Door-aware failed-edge handling (detection layer consumer). When the
    # progress watchdog marks a failed edge, door_capture_wedge latches the
    # nearest currently-blocked door to the wedged bot; the fast-retry check
    # below then clears the marker the moment that door reads passable again
    # (periodic grid scan), instead of waiting out the blind
    # WP_ROUTE_BLOCK_RETRY_HITS cadence — the exact residual grind loop seen
    # live on Hydroplant Bouncefest (marker held long after the door opened).
    door_gate = (cfg.DOOR_DETECT_ENABLED
                 and layout.has_field('route_block_door')
                 and layout.has_field('door_blocked')
                 and layout.has_field('door_count'))
    if door_gate:
        route_block_door_va = layout.va('route_block_door')
        door_blocked_va     = layout.va('door_blocked')
        door_count_va       = layout.va('door_count')
        # Door-side ARRIVAL gate (follow_arrive): door centers + the wedge
        # radius double as "is this node at a door / which side is the bot
        # on". Same layout block as door_blocked, so no extra gating.
        door_gate_table_va      = layout.va('door_table')
        door_wedge_radius_sq_va = layout.va('door_wedge_radius_sq')

    # Wedge-cluster HARD RESET state (see the s542360_wp_hard_reset block and
    # cfg.WP_WEDGE_RESET_CYCLES). fight_stall additionally lets the routed
    # progress-timeout skip the suspension while an enemy is in close range
    # (bot_enemy_near stamped by the fire detour's pick_target).
    wedge_reset = layout.has_field('bot_wedge_cycles') and layout.has_field('wpfn_excl')
    if wedge_reset:
        bot_wedge_cycles_va = layout.va('bot_wedge_cycles')
        wpfn_excl_va        = layout.va('wpfn_excl')
    fight_stall = layout.has_field('bot_enemy_near')

    # Closed-door commitment recovery. The door-BLIND commit paths (cold-acquire
    # nearest node, reacquire, retreat) and any next-hop made while a door was
    # open can leave a bot latched onto a target node it must reach ACROSS a
    # now-closed door. Only ctf_next_hop is door-aware, and it re-runs only on
    # ARRIVAL — which never happens when the closed door blocks the last leg —
    # so the bot grinds the door until death/respawn (live-reported edge case;
    # R-snapshots showed a bot on the prev side of closed pillar gates with
    # current_wp on the far node). When this fires we re-route door-aware from
    # the reachable side. Needs the edge list + per-edge door binding.
    door_reroute = (routing and door_gate
                    and layout.has_field('edge_door')
                    and layout.has_field('door_table')
                    and layout.has_field('overlay_edges')
                    and layout.has_field('overlay_edge_count'))
    if door_reroute:
        edge_door_va         = layout.va('edge_door')
        door_table_va        = layout.va('door_table')
        overlay_edges_va     = layout.va('overlay_edges')
        overlay_edge_count_va = layout.va('overlay_edge_count')

    # Portal pad approach + roam wander-entry. bot_portal_target[slot] (pad
    # idx+1) is latched by a routed portal hop (ctf_next_hop via
    # route_portal_hop) or by the roam-time portal_wander_check roll; while
    # latched the follower steers at the PAD CENTER through the same watchdog
    # as the flag final approach. The latch ends via the teleport-jump detect
    # (the pad fired), a progress timeout (pad unreachable/inactive — clears
    # the latch and suspends routing so the next arrivals roam), a stale pad
    # index, or the pad reading inactive. Emitted whenever the portal-routing
    # scratch fields exist; the runtime knobs gate behaviour.
    portal_move = (layout.has_field('bot_portal_target')
                   and layout.has_field('portal_node')
                   and layout.has_field('portal_table')
                   and layout.has_field('route_portal_hop'))
    if portal_move:
        bot_portal_target_va = layout.va('bot_portal_target')
        portal_table_va      = layout.va('portal_table')
        portal_count_va      = layout.va('portal_count')
        route_portal_hop_va  = layout.va('route_portal_hop')
        portal_active_mv_va  = (layout.va('portal_active')
                                if layout.has_field('portal_active') else 0)

    # Dropped-flag pursuit (v2 — two-phase). While a flag is away from its
    # base the periodic grid walk records its dropped world copy's position +
    # nearest graph node (name-matched, see entity_scan.py). A latched bot
    # ROUTES to the drop through the graph (drop_next_hop descends the
    # per-drop drop_dist BFS row at each node arrival — see flag_route.py)
    # and only steers STRAIGHT at the copy within drop_direct_radius_sq or
    # when standing on the drop's own bound node, through the standard
    # watchdog with press-patience. Latching: within drop_pursue_radius_sq
    # opportunistically, or from ANY distance when the drop is this bot's
    # missing GOAL flag (route_missing_goal — attackers whose steal target
    # dropped, carriers whose home flag dropped: the position is known, so
    # the old blind search/wait roam is replaced by a real route). The v1
    # straight-steer-only pursuit was live-diagnosed timing out after one
    # 30-frame watchdog window and cooling down 4 s — the reported "runs at
    # it, then ignores it" loop — and it beelined into walls when the drop
    # sat behind one. Touching a dropped flag is beneficial for EITHER team,
    # so there is no team/carry filter. Deliberately NOT gated on
    # bot_route_suspend for the DIRECT phase (touching a nearby flag is pure
    # upside); the ROUTED next-hop override does respect suspension (the
    # suspension roam exists to unstick deterministic routing).
    drop_move = (routing
                 and cfg.CTF_DROPPED_FLAG_ENABLED
                 and layout.has_field('flag_drop_valid')
                 and layout.has_field('flag_drop_pos')
                 and layout.has_field('flag_drop_node')
                 and layout.has_field('bot_drop_target')
                 and layout.has_field('bot_drop_cd')
                 and layout.has_field('bot_drop_try')
                 and layout.has_field('bot_drop_best')
                 and layout.has_field('drop_pursue_enabled')
                 and layout.has_field('route_missing_goal')
                 and layout.has_field('flag_present')
                 and layout.has_field('flag_count'))
    if drop_move:
        flag_drop_valid_mv_va = layout.va('flag_drop_valid')
        flag_drop_pos_mv_va   = layout.va('flag_drop_pos')
        flag_drop_node_mv_va  = layout.va('flag_drop_node')
        bot_drop_target_va    = layout.va('bot_drop_target')
        bot_drop_cd_va        = layout.va('bot_drop_cd')
        bot_drop_try_va       = layout.va('bot_drop_try')
        bot_drop_best_va      = layout.va('bot_drop_best')
        drop_enabled_va       = layout.va('drop_pursue_enabled')
        drop_radius_va        = layout.va('drop_pursue_radius_sq')
        drop_reached_va       = layout.va('drop_reached_radius_sq')
        drop_direct_va        = layout.va('drop_direct_radius_sq')
        drop_abandon_va       = layout.va('drop_abandon_radius_sq')
        drop_missing_goal_va  = layout.va('route_missing_goal')
        flag_present_mv_va    = layout.va('flag_present')
        flag_count_mv_va      = layout.va('flag_count')

    # Enemy-carrier chase (two-phase, mirror of the drop pursuit's shape).
    # bot_chase_flag[slot] (flag idx+1) is latched by the perception scan's
    # LOS sighting of an enemy carrying this bot's team flag; the shared
    # per-flag intel (chase_pos/chase_node/chase_ttl) is refreshed by any
    # bot's sighting and serviced (TTL tick, node bind, BFS row rebuild)
    # by chase_route_refresh at the page flip. ROUTED phase descends the
    # chase_dist row at node arrivals (chase_next_hop); DIRECT phase
    # straight-steers only inside the direct radius or at the carrier's
    # own bound node. The target MOVES, so the direct-phase stall signal
    # is the physical stuck detector, not dsq improvement.
    chase_move = (routing
                  and cfg.CTF_CHASE_ENABLED
                  and layout.has_field('bot_chase_flag')
                  and layout.has_field('chase_pos')
                  and layout.has_field('chase_dsq_tmp')
                  and layout.has_field('flag_present')
                  and layout.has_field('flag_count'))
    if chase_move:
        bot_chase_flag_va = layout.va('bot_chase_flag')
        bot_chase_cd_va = layout.va('bot_chase_cd')
        chase_pos_mv_va = layout.va('chase_pos')
        chase_node_mv_va = layout.va('chase_node')
        chase_ttl_mv_va = layout.va('chase_ttl')
        chase_dsq_tmp_va = layout.va('chase_dsq_tmp')
        chase_flag_present_va = layout.va('flag_present')
        chase_flag_count_va = layout.va('flag_count')

    # Roam switch wander-bump. bot_switch_target[slot] (switch idx+1) is
    # latched by the switch_wander_check roll at a roam arrival (the fallback
    # path — DM roam, CTF missing-flag search, routing fallback); while
    # latched the follower steers at the SWITCH CENTER through the same
    # watchdog + press patience as the pad approach so the repeatable
    # CollideTrigger fires. Success = the switch's blocked-paired-door census
    # CHANGED since latch (openers AND togglers both register); success or
    # exhausted patience arms the per-bot re-roll cooldown. A dropped-flag
    # pursuit outranks a bump.
    switch_wander = (cfg.SWITCH_WANDER_ENABLED
                     and layout.has_field('bot_switch_target')
                     and layout.has_field('switch_node')
                     and layout.has_field('switch_table')
                     and layout.has_field('sww_census'))
    if switch_wander:
        bot_switch_target_va = layout.va('bot_switch_target')
        bot_switch_cd_va     = layout.va('bot_switch_cd')
        bot_switch_try_va    = layout.va('bot_switch_try')
        bot_switch_snap_va   = layout.va('bot_switch_snap')
        sww_census_va        = layout.va('sww_census')
        switch_table_sw_va   = layout.va('switch_table')
        switch_count_sw_va   = layout.va('switch_count')

    # Salvage King layer: sk_next_hop replaces the arrival next-hop while
    # sk_routing_active (COLLECT descends the multi-source mineral field,
    # RETURN descends the bot's own-bin row); the deposit final approach
    # steers at the bin center once the bot stands on its bin's node; the
    # pile divert opportunistically steers at a registered death pile.
    sk_move = (cfg.SK_ENABLED
               and layout.has_field('sk_routing_active')
               and layout.has_field('bot_sk_return')
               and layout.has_field('sk_bin_table')
               and layout.has_field('sk_pile_valid')
               and layout.has_field('bot_route_suspend')
               and layout.has_field('bot_team'))
    if sk_move:
        sk_team_mv_va       = layout.va('bot_team')
        sk_suspend_mv_va    = layout.va('bot_route_suspend')
        sk_active_mv_va     = layout.va('sk_routing_active')
        bot_sk_return_va    = layout.va('bot_sk_return')
        bot_sk_dep_try_va   = layout.va('bot_sk_dep_try')
        sk_bin_table_mv_va  = layout.va('sk_bin_table')
        sk_bin_valid_mv_va  = layout.va('sk_bin_valid')
        sk_bin_node_mv_va   = layout.va('sk_bin_node')
        bot_pile_target_va  = layout.va('bot_pile_target')
        bot_pile_cd_va      = layout.va('bot_pile_cd')
        bot_pile_try_va     = layout.va('bot_pile_try')
        bot_pile_best_va    = layout.va('bot_pile_best')
        sk_pile_valid_mv_va = layout.va('sk_pile_valid')
        sk_pile_pos_mv_va   = layout.va('sk_pile_pos')
        sk_pile_radius_va   = layout.va('sk_pile_pursue_radius_sq')
        sk_pile_reached_va  = layout.va('sk_pile_reached_radius_sq')
    # Generalized goody pursuit (graph-routed piles + filler items) — the
    # two-phase upgrade of the straight-steer pile divert.
    goody_move = (sk_move
                  and layout.has_field('goody_tx')
                  and layout.has_field('item_routing_active')
                  and layout.has_field('sk_pile_dirty'))
    if goody_move:
        goody_tx_va         = layout.va('goody_tx')
        goody_ty_va         = layout.va('goody_ty')
        goody_node_va       = layout.va('goody_node')
        goody_idx_va        = layout.va('goody_idx')
        goody_scan_rad_va   = layout.va('goody_scan_rad')
        goody_scan_cat_va   = layout.va('goody_scan_cat')
        item_active_mv_va   = layout.va('item_routing_active')
        item_cat_mv_va      = layout.va('item_cat')
        item_radius_mv_va   = layout.va('item_pursue_radius_sq')
        goody_direct_va     = layout.va('goody_direct_radius_sq')
        goody_abandon_va    = layout.va('goody_abandon_radius_sq')
        sk_pile_dirty_mv_va = layout.va('sk_pile_dirty')


    ns = dict(locals())
    ns.pop('layout', None)
    return SimpleNamespace(**ns)
