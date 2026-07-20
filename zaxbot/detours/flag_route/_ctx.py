"""Shared routing context — the VA locals + feature gates every
flag-route section consumes.

``build_ctx`` runs the (former) emit prologue verbatim and returns a
SimpleNamespace of every local, so each section module can unpack just
the names it uses. Gate-dependent names are pre-seeded to ``None``;
sections only touch them under the corresponding gate (``weighted`` /
``door_route`` / ``seek`` / ``portal_route``), and a misuse fails the
build loudly (``le32(None)``).
"""

from types import SimpleNamespace

from ... import config as cfg
from ...layout import ScratchLayout


def build_ctx(layout: ScratchLayout) -> SimpleNamespace:
    # Gate-dependent names default to None (assigned under their gates
    # below).
    edge_len_va = None
    bfs_inq_va = None
    elen_quantum_va = None
    flag_dist_open_va = None
    edge_door_va = None
    edge_pass_va = None
    cnh_blk_va = None
    door_mask_i_va = None
    door_mask_j_va = None
    door_blocked_va = None
    door_count_va = None
    bfs_start_va = None
    bfs_skip_va = None
    route_use_open_va = None
    TEAM_ROW = None
    portal_node_va = None
    portal_dest_node_va = None
    portal_has_dest_va = None
    portal_count_va = None
    route_portal_hop_va = None
    portal_active_va = None
    switch_node_va = None
    switch_table_va = None
    switch_flags_va = None
    switch_pairs_va = None
    switch_count_va = None
    switch_pair_count_va = None
    seek_active_va = None
    seek_node_va = None
    seek_pending_va = None
    seek_req_node_va = None
    seek_req_goal_va = None
    seek_tried_va = None
    seek_fail_va = None
    seek_timer_va = None
    seek_best_va = None
    seek_best_score_va = None
    seek_eval_s_va = None
    seek_req_open_va = None
    seek_dist_va = None
    bot_seek_va = None
    SEEK_ROW = None

    routing_active_va = layout.va('flag_routing_active')
    route_cur_va      = layout.va('route_cur')
    route_carry_va    = layout.va('route_carry')
    route_goal_va     = layout.va('route_goal_flag')
    route_node_va     = layout.va('flag_route_node')
    flag_dist_va      = layout.va('flag_dist')
    bfs_queue_va      = layout.va('bfs_queue')
    bfs_head_va       = layout.va('bfs_head')
    bfs_tail_va       = layout.va('bfs_tail')
    bfs_u_va          = layout.va('bfs_u')
    bfs_du_va         = layout.va('bfs_du')
    bfs_disti_va      = layout.va('bfs_disti')
    bfr_i_va          = layout.va('bfr_i')
    flag_table_va     = layout.va('flag_table')
    flag_team_va      = layout.va('flag_team')
    flag_count_va     = layout.va('flag_count')
    flag_present_va   = layout.va('flag_present')
    missing_policy_va = layout.va('route_missing_policy')
    missing_goal_va   = layout.va('route_missing_goal')
    route_suspend_va  = layout.va('bot_route_suspend')
    verts_va          = layout.va('overlay_vertices')
    vcount_va         = layout.va('overlay_vertex_count')
    edges_va          = layout.va('overlay_edges')
    ecount_va         = layout.va('overlay_edge_count')
    wp_scratch_va     = layout.va('wp_scratch')
    bot_slot_va       = layout.va('bot_slot_tmp')
    bot_char_va       = layout.va('bot_char_tmp')
    bot_team_va       = layout.va('bot_team')

    VMAX  = cfg.OVERLAY_VERTEX_MAX
    RMAX  = cfg.FLAG_ROUTE_MAX
    ROW   = VMAX * 4                       # flag_dist row stride (bytes) per base

    # Weighted routing (physical-length SPFA): per-edge quantized lengths +
    # per-node in-queue flags. Present whenever the overlay edge tables are
    # (same layout gate), i.e. always on routing-capable builds.
    weighted = layout.has_field('edge_len') and layout.has_field('bfs_inq')
    if weighted:
        edge_len_va     = layout.va('edge_len')
        bfs_inq_va      = layout.va('bfs_inq')
        elen_quantum_va = layout.va('elen_quantum')
        assert (VMAX & (VMAX - 1)) == 0, 'SPFA ring mask needs power-of-two VMAX'

    # Door-aware rerouting: a SECOND per-base BFS field (flag_dist_open) that
    # SKIPS every graph edge crossing a currently-blocked door, so bots route
    # AROUND closed doors when an alternative exists (live-reported gap: two
    # blocked ways to the enemy flag — opening the second one never diverted
    # the bot committed to the first). ctf_next_hop prefers the open field and
    # falls back to the full field whenever the goal is unreachable without
    # passing a closed door, preserving the old walk-at-the-door behaviour for
    # proximity/touch-opened doors. edge_door[] (static per match) + live
    # door_blocked[] make the per-edge test two integer reads.
    door_route = (
        cfg.DOOR_ROUTE_AWARE_ENABLED
        and layout.has_field('flag_dist_open')
        and layout.has_field('edge_door')
        and layout.has_field('edge_pass')
        and layout.has_field('cnh_blk')
        and layout.has_field('door_blocked')
        and layout.has_field('door_count')
        and layout.has_field('bfs_start')
        and layout.has_field('bfs_skip')
        and layout.has_field('route_use_open')
    )
    if door_route:
        flag_dist_open_va = layout.va('flag_dist_open')
        edge_door_va      = layout.va('edge_door')
        edge_pass_va      = layout.va('edge_pass')
        cnh_blk_va        = layout.va('cnh_blk')
        door_mask_i_va    = layout.va('door_mask_i')
        door_mask_j_va    = layout.va('door_mask_j')
        door_blocked_va   = layout.va('door_blocked')
        door_count_va     = layout.va('door_count')
        bfs_start_va      = layout.va('bfs_start')
        bfs_skip_va       = layout.va('bfs_skip')
        route_use_open_va = layout.va('route_use_open')
        TEAM_ROW = RMAX * ROW          # open-field stride per team (team-major)

    # Switch-seek routing (detection-layer consumer). All state per team; the
    # seek field is a bfs_run pass rooted at the sought switch's node with the
    # SAME team door gating as the open field, so descending it is exactly the
    # open-field walk semantics.
    seek = (
        door_route
        and cfg.SWITCH_SEEK_ENABLED
        and layout.has_field('seek_active')
        and layout.has_field('seek_dist')
        and layout.has_field('switch_node')
        and layout.has_field('switch_table')
        and layout.has_field('switch_flags')
        and layout.has_field('switch_pairs')
        and layout.has_field('bot_seek')
    )
    # Portal routing: teleport pads with build-time-resolved destinations are
    # DIRECTED graph edges (source pad node -> destination node). bfs_run
    # relaxes them in every field it fills (the BFS runs from the goal
    # outward, so a portal whose DEST node is the dequeued u lowers its
    # SOURCE node: the bot at source walks INTO the pad and comes out at
    # dest). ctf_next_hop then reports a "portal hop" (route_portal_hop =
    # pad idx+1) whenever the pad bound to the current node carries a
    # strictly smaller distance through its destination than any neighbour —
    # the follower latches a pad final-approach off that. Live pad usability
    # (portal_active) gates only the NEXT-HOP side; the fields themselves are
    # not rebuilt on pad-state flips (a stale route into an inactive pad ends
    # in the standard watchdog -> suspension -> roam machinery).
    portal_route = (
        door_route
        and cfg.PORTAL_ROUTING_ENABLED
        and layout.has_field('portal_node')
        and layout.has_field('portal_dest_node')
        and layout.has_field('portal_has_dest')
        and layout.has_field('route_portal_hop')
    )
    if portal_route:
        portal_node_va       = layout.va('portal_node')
        portal_dest_node_va  = layout.va('portal_dest_node')
        portal_has_dest_va   = layout.va('portal_has_dest')
        portal_count_va      = layout.va('portal_count')
        route_portal_hop_va  = layout.va('route_portal_hop')
        portal_active_va     = (layout.va('portal_active')
                                if layout.has_field('portal_active') else 0)

    if seek:
        switch_node_va       = layout.va('switch_node')
        switch_table_va      = layout.va('switch_table')
        switch_flags_va      = layout.va('switch_flags')
        switch_pairs_va      = layout.va('switch_pairs')
        switch_count_va      = layout.va('switch_count')
        switch_pair_count_va = layout.va('switch_pair_count')
        seek_active_va       = layout.va('seek_active')
        seek_node_va         = layout.va('seek_node')
        seek_pending_va      = layout.va('seek_pending')
        seek_req_node_va     = layout.va('seek_req_node')
        seek_req_goal_va     = layout.va('seek_req_goal')
        seek_tried_va        = layout.va('seek_tried')
        seek_fail_va         = layout.va('seek_fail')
        seek_timer_va        = layout.va('seek_timer')
        seek_best_va         = layout.va('seek_best')
        seek_best_score_va   = layout.va('seek_best_score')
        seek_eval_s_va       = layout.va('seek_eval_s')
        seek_req_open_va     = layout.va('seek_req_open')
        seek_dist_va         = layout.va('seek_dist')
        bot_seek_va          = layout.va('bot_seek')
        SEEK_ROW = VMAX * 4            # seek_dist stride per team

    ns = dict(locals())
    ns.pop('layout', None)
    return SimpleNamespace(**ns)
