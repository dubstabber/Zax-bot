"""CTF flag routing — ``build_flag_routes`` (per-match BFS) + ``ctf_next_hop``
(per-arrival goal-biased next-hop over the authored waypoint graph).

The follower (``detours/bot_movement.py``) normally advances to a RANDOM
connected neighbour on node arrival (``wp_advance``). In a CTF match these two
subroutines replace that random pick with a true shortest-path step toward a
flag base:

* NOT carrying the flag  -> head to the ENEMY base.
* carrying the enemy flag -> head to OWN base to capture.

**Model.** Goals are the static flag BASE anchors (``flag_table`` + ``flag_team``,
loaded per match by ``load_flags``). Routing is a precomputed BFS field, NOT a
per-frame search:

* ``build_flag_routes`` (once per match, from ``detour_df90`` when the match is
  CTF with a graph + flags): for each routed base i, finds the nearest graph
  node (``wp_find_nearest``) and runs a breadth-first search over the UNDIRECTED
  edge list, filling ``flag_dist[i][node]`` with the hop distance from that base
  node to every node (``0xFFFFFFFF`` = unreachable / no graph). O(V*E) once.
* ``ctf_next_hop`` (each node arrival, from ``s542360_wp_arrived``): picks the
  bot's goal base from its team (``bot_team[slot]``) and carry state
  (``is_carrying``), then returns the neighbour of the current node whose
  ``flag_dist[goal]`` is strictly smaller than the current node's — i.e. one hop
  along a real shortest path, guaranteeing progress. Returns ``-1`` (caller
  falls back to the random ``wp_advance``) whenever routing can't apply: routing
  inactive, no goal base for this team, the current node is unreachable from the
  goal, or the bot already sits on the goal node.

Live flag-base presence (``flag_present[]``) is EVENT-driven: the
``detours/flag_events.py`` detours mirror the map script's base-checker
activation (deactivated on steal, reactivated on return/capture), which is the
vanilla "own flag is home" state. When an attacker sees the enemy flag missing
from its base,
the bot rolls one temporary policy for that missing-goal episode: either search
(``route_goal_flag = -1``, so node arrivals fall back to random roaming), or
wait near the enemy base (keep the goal so BFS moves it toward that home
anchor). A carrier whose OWN flag is missing must not be driven into the empty
home base, because capture is illegal until that flag returns; carrier+missing
home always uses search mode. The policy is cleared when the flag becomes
present again.

``is_carrying`` is the engine's own per-character inventory-group test
(live-verified): ``inv = sub_4267E0(char)``; ``slot = sub_425290(inv, FLAG_GID)``
where ``FLAG_GID = [MULTIPLAYER_FLAG_GID_VA]`` (==8); carrying iff ``slot != -1``.
Every deref is NULL-guarded (``inv == 0`` / ``FLAG_GID == 0`` => not carrying).

All routing data is GLOBAL scratch (not per-bot). ``flag_routing_active`` (set by
``detour_df90``) is the master runtime gate.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    needed = (
        'flag_routing_active', 'route_cur', 'route_carry', 'route_goal_flag',
        'flag_route_node', 'flag_dist', 'bfs_queue', 'bfs_head', 'bfs_tail',
        'bfs_u', 'bfs_du', 'bfs_disti', 'bfr_i', 'flag_table', 'flag_team',
        'flag_count', 'flag_present', 'route_missing_policy',
        'route_missing_goal', 'overlay_vertices', 'overlay_vertex_count',
        'overlay_edges', 'overlay_edge_count', 'wp_scratch', 'bot_slot_tmp',
        'bot_char_tmp', 'bot_team',
    )
    if not all(layout.has_field(f) for f in needed):
        # Layout built without routing fields — inert stubs so call_lbl resolves.
        a.label('build_flag_routes'); a.raw(b'\xC3')
        a.label('build_edge_lens'); a.raw(b'\xC3')
        a.label('rebuild_open_routes'); a.raw(b'\xC3')
        a.label('ctf_pick_goal'); a.raw(b'\xC3')
        a.label('ctf_next_hop'); a.raw(b'\xB8\xFF\xFF\xFF\xFF\xC3')  # mov eax,-1; ret
        a.label('switch_seek_eval'); a.raw(b'\xC3')
        a.label('drop_next_hop'); a.raw(b'\xB8\xFF\xFF\xFF\xFF\xC3')
        a.label('drop_route_refresh'); a.raw(b'\xC3')
        a.label('build_sk_routes'); a.raw(b'\xC3')
        a.label('sk_update_phase'); a.raw(b'\xC3')
        a.label('sk_next_hop'); a.raw(b'\xB8\xFF\xFF\xFF\xFF\xC3')
        return

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

    # =====================================================================
    # build_flag_routes: per-match BFS distance field(s) from each flag base.
    # pushad/popad, no args. Safe to call even with no graph (dist stays INF).
    # On door-aware builds the BFS body lives in the callable ``bfs_run``
    # (parametrized through bfs_start / bfs_disti / bfs_skip) so the same code
    # fills the full field, the open field, and the open-field REBUILDS that
    # fire when door state changes (rebuild_open_routes).
    # =====================================================================
    a.label('build_flag_routes')
    a.raw(b'\x60')                                              # pushad
    # flag_dist[*] = 0xFFFFFFFF
    a.raw(b'\xBF' + le32(flag_dist_va))                        # edi = flag_dist
    a.raw(b'\xB9' + le32(RMAX * VMAX))                         # ecx = RMAX*VMAX
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                             # eax = -1
    a.raw(b'\xFC')                                             # cld
    a.raw(b'\xF3\xAB')                                         # rep stosd
    if door_route:
        # flag_dist_open[*] = 0xFFFFFFFF (both team fields)
        a.raw(b'\xBF' + le32(flag_dist_open_va))               # edi = flag_dist_open
        a.raw(b'\xB9' + le32(2 * RMAX * VMAX))                 # ecx = 2*RMAX*VMAX
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                         # eax = -1
        a.raw(b'\xF3\xAB')                                     # rep stosd
    # flag_route_node[*] = -1
    a.raw(b'\xBF' + le32(route_node_va))                      # edi = flag_route_node
    a.raw(b'\xB9' + le32(RMAX))                                # ecx = RMAX
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                             # eax = -1
    a.raw(b'\xF3\xAB')                                         # rep stosd
    if seek:
        # Fresh seek state per match: node bindings/field to -1, the whole
        # per-team state block (seek_active..seek_timer, contiguous 16 dwords)
        # and bot_seek[] to 0.
        a.raw(b'\xBF' + le32(switch_node_va))                  # edi = switch_node
        a.raw(b'\xB9' + le32(cfg.SWITCH_TABLE_MAX))            # ecx = table max
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                         # eax = -1
        a.raw(b'\xF3\xAB')                                     # rep stosd
        a.raw(b'\xBF' + le32(seek_dist_va))                    # edi = seek_dist
        a.raw(b'\xB9' + le32(2 * VMAX))                        # ecx = both team rows
        a.raw(b'\xF3\xAB')                                     # rep stosd (eax still -1)
        a.raw(b'\xBF' + le32(seek_active_va))                  # edi = seek state block
        a.raw(b'\xB9\x16\x00\x00\x00')                         # ecx = 22 dwords (11 pairs)
        a.raw(b'\x31\xC0')                                     # eax = 0
        a.raw(b'\xF3\xAB')                                     # rep stosd
        a.raw(b'\xBF' + le32(bot_seek_va))                     # edi = bot_seek
        a.raw(b'\xB9' + le32(16))                              # ecx = MAX_BOT_SLOTS
        a.raw(b'\xF3\xAB')                                     # rep stosd
    # No graph? leave INF and bail.
    a.raw(b'\xA1' + le32(vcount_va))                           # eax = vertex_count
    a.raw(b'\x85\xC0'); a.jz('bfr_done')
    if seek:
        # Bind each live switch to its nearest graph node (the seek routing
        # target). wp_find_nearest reads wp_scratch, returns ebx; it may
        # clobber the loop register, so spill the index in bfs_u.
        a.raw(b'\xC7\x05' + le32(bfs_u_va) + le32(0))          # s = 0
        a.label('bfr_sbn_loop')
        a.raw(b'\xA1' + le32(bfs_u_va))                        # eax = s
        a.raw(b'\x3B\x05' + le32(switch_count_va))             # s >= switch_count?
        a.jae('bfr_sbn_done')
        a.raw(b'\x83\xF8' + bytes([cfg.SWITCH_TABLE_MAX]))     # s >= table max?
        a.jae('bfr_sbn_done')
        a.raw(b'\x8B\x0C\xC5' + le32(switch_table_va))         # ecx = switch.x
        a.raw(b'\x89\x0D' + le32(wp_scratch_va))               # wp_scratch.x
        a.raw(b'\x8B\x0C\xC5' + le32(switch_table_va + 4))     # ecx = switch.y
        a.raw(b'\x89\x0D' + le32(wp_scratch_va + 4))           # wp_scratch.y
        a.call_lbl('wp_find_nearest')                          # ebx = nearest or -1
        a.raw(b'\xA1' + le32(bfs_u_va))                        # eax = s
        a.raw(b'\x89\x1C\x85' + le32(switch_node_va))          # switch_node[s] = ebx
        a.raw(b'\xFF\x05' + le32(bfs_u_va))                    # ++s
        a.jmp('bfr_sbn_loop')
        a.label('bfr_sbn_done')
    a.raw(b'\xC7\x05' + le32(bfr_i_va) + le32(0))             # bfr_i = 0

    a.label('bfr_loop')
    # nbase = min(flag_count, RMAX); stop when bfr_i >= nbase.
    a.raw(b'\x8B\x0D' + le32(flag_count_va))                  # ecx = flag_count
    a.raw(b'\x83\xF9' + bytes([RMAX]))                        # cmp ecx, RMAX
    a.jbe('bfr_nb_ok')
    a.raw(b'\xB9' + le32(RMAX))                                # ecx = RMAX
    a.label('bfr_nb_ok')
    a.raw(b'\xA1' + le32(bfr_i_va))                           # eax = i
    a.raw(b'\x39\xC8')                                         # cmp eax, ecx (i - nbase)
    a.jae('bfr_done')
    # wp_scratch = flag_table[i]
    a.raw(b'\x8B\x0C\xC5' + le32(flag_table_va))             # ecx = [flag_table + eax*8] (x)
    a.raw(b'\x89\x0D' + le32(wp_scratch_va))                  # wp_scratch.x = ecx
    a.raw(b'\x8B\x0C\xC5' + le32(flag_table_va + 4))         # ecx = [flag_table + eax*8 + 4] (y)
    a.raw(b'\x89\x0D' + le32(wp_scratch_va + 4))             # wp_scratch.y = ecx
    a.call_lbl('wp_find_nearest')                             # ebx = nearest idx or -1
    a.raw(b'\x83\xFB\xFF')                                     # cmp ebx, -1
    a.jz('bfr_next')                                          # no node -> skip base
    a.raw(b'\xA1' + le32(bfr_i_va))                           # eax = i
    a.raw(b'\x89\x1C\x85' + le32(route_node_va))             # flag_route_node[i] = ebx
    if door_route:
        a.raw(b'\x89\x1D' + le32(bfs_start_va))               # bfs_start = nearest
        # FULL field: disti = flag_dist + i*ROW, no edge skipping.
        a.raw(b'\x69\xC0' + le32(ROW))                        # imul eax, eax, ROW
        a.raw(b'\x05' + le32(flag_dist_va))                   # add eax, flag_dist
        a.raw(b'\xA3' + le32(bfs_disti_va))                   # bfs_disti = eax
        a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(0))      # bfs_skip = 0
        a.call_lbl('bfs_run')
        # OPEN fields (one per team): closed-door edges pass only in
        # directions that team can open.
        a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(1))      # bfs_skip = 1
        a.raw(b'\xA1' + le32(bfr_i_va))                       # eax = i
        a.raw(b'\x69\xC0' + le32(ROW))                        # imul eax, eax, ROW
        a.raw(b'\x05' + le32(flag_dist_open_va))              # team 0 row
        a.raw(b'\xA3' + le32(bfs_disti_va))                   # bfs_disti = eax
        a.raw(b'\xC7\x05' + le32(door_mask_i_va) + le32(0x01))
        a.raw(b'\xC7\x05' + le32(door_mask_j_va) + le32(0x02))
        a.call_lbl('bfs_run')
        a.raw(b'\xA1' + le32(bfr_i_va))                       # eax = i
        a.raw(b'\x69\xC0' + le32(ROW))                        # imul eax, eax, ROW
        a.raw(b'\x05' + le32(flag_dist_open_va + RMAX * VMAX * 4))  # team 1 row
        a.raw(b'\xA3' + le32(bfs_disti_va))                   # bfs_disti = eax
        a.raw(b'\xC7\x05' + le32(door_mask_i_va) + le32(0x04))
        a.raw(b'\xC7\x05' + le32(door_mask_j_va) + le32(0x08))
        a.call_lbl('bfs_run')
        a.jmp('bfr_next')
    else:
        # disti = flag_dist + i*ROW
        a.raw(b'\x69\xC0' + le32(ROW))                        # imul eax, eax, ROW
        a.raw(b'\x05' + le32(flag_dist_va))                   # add eax, flag_dist
        a.raw(b'\xA3' + le32(bfs_disti_va))                   # bfs_disti = eax
        # BFS init: disti[nearest]=0; queue[0]=nearest; head=0; tail=1
        a.raw(b'\x8B\x0D' + le32(bfs_disti_va))              # ecx = disti
        a.raw(b'\xC7\x04\x99\x00\x00\x00\x00')               # mov [ecx + ebx*4], 0
        a.raw(b'\x89\x1D' + le32(bfs_queue_va))              # queue[0] = ebx
        a.raw(b'\xC7\x05' + le32(bfs_head_va) + le32(0))     # head = 0
        a.raw(b'\xC7\x05' + le32(bfs_tail_va) + le32(1))     # tail = 1

        a.label('bfr_bfs_loop')
        a.raw(b'\xA1' + le32(bfs_head_va))                    # eax = head
        a.raw(b'\x3B\x05' + le32(bfs_tail_va))               # cmp eax, tail
        a.jae('bfr_bfs_done')
        a.raw(b'\x8B\x0C\x85' + le32(bfs_queue_va))         # ecx = queue[head]
        a.raw(b'\x89\x0D' + le32(bfs_u_va))                  # bfs_u = u
        a.raw(b'\xFF\x05' + le32(bfs_head_va))              # head++
        a.raw(b'\x8B\x15' + le32(bfs_disti_va))             # edx = disti
        a.raw(b'\x8B\x04\x8A')                               # eax = [edx + ecx*4] (disti[u] = du)
        a.raw(b'\x89\x05' + le32(bfs_du_va))                # bfs_du = du
        a.raw(b'\x31\xF6')                                   # esi = 0 (edge idx)

        a.label('bfr_edge_loop')
        a.raw(b'\x3B\x35' + le32(ecount_va))                # cmp esi, edge_count
        a.jae('bfr_bfs_loop')                               # edges done -> next BFS node
        a.raw(b'\x8B\x04\xB5' + le32(edges_va))            # eax = edges[esi]
        a.raw(b'\x0F\xB7\xD8')                              # movzx ebx, ax (i)
        a.raw(b'\xC1\xE8\x10')                              # shr eax, 16   (j)
        a.raw(b'\x8B\x0D' + le32(bfs_u_va))                # ecx = u
        a.raw(b'\x39\xCB')                                  # cmp ebx, ecx (i == u?)
        a.jz('bfr_edge_v_j')                                # i==u -> v = j (eax)
        a.raw(b'\x39\xC8')                                  # cmp eax, ecx (j == u?)
        a.jz('bfr_edge_v_i')                                # j==u -> v = i (ebx)
        a.jmp('bfr_edge_next')
        a.label('bfr_edge_v_i')
        a.raw(b'\x89\xD8')                                  # eax = ebx (v = i)
        a.label('bfr_edge_v_j')                            # v in eax
        a.raw(b'\x3B\x05' + le32(vcount_va))               # cmp eax, vertex_count
        a.jae('bfr_edge_next')                             # out of range
        a.raw(b'\x8B\x15' + le32(bfs_disti_va))           # edx = disti
        a.raw(b'\x8B\x0C\x82')                             # ecx = [edx + eax*4] (disti[v])
        a.raw(b'\x83\xF9\xFF')                             # cmp ecx, -1 (visited?)
        a.jnz('bfr_edge_next')
        a.raw(b'\x8B\x0D' + le32(bfs_du_va))              # ecx = du
        a.raw(b'\x41')                                     # inc ecx (du+1)
        a.raw(b'\x89\x0C\x82')                            # [edx + eax*4] = ecx (disti[v] = du+1)
        a.raw(b'\x8B\x0D' + le32(bfs_tail_va))           # ecx = tail
        a.raw(b'\x89\x04\x8D' + le32(bfs_queue_va))      # queue[tail] = v (eax)
        a.raw(b'\xFF\x05' + le32(bfs_tail_va))           # tail++
        a.label('bfr_edge_next')
        a.raw(b'\x46')                                    # inc esi
        a.jmp('bfr_edge_loop')

        a.label('bfr_bfs_done')
    a.label('bfr_next')
    a.raw(b'\xFF\x05' + le32(bfr_i_va))                  # i++
    a.jmp('bfr_loop')

    a.label('bfr_done')
    a.raw(b'\x61')                                        # popad
    a.raw(b'\xC3')

    # =====================================================================
    # build_edge_lens: per-match quantized physical edge lengths — the
    # traversal cost bfs_run adds per edge (weighted SPFA). Hop counting was
    # live-refuted on Hydroplant Bouncefest: the through-door route and the
    # around-the-top route TIE at 9 hops, so routing and the seek benefit
    # gate saw zero gain from opening the switch-doors, yet the door route
    # is 1899 px vs 2580 px around. All slots default to cost 1 so strict
    # next-hop descent survives any degenerate edge. Called once per match
    # from detour_df90 (after wp_load). pushad/popad, no args.
    # =====================================================================
    if not weighted:
        a.label('build_edge_lens')
        a.raw(b'\xC3')
    else:
        a.label('build_edge_lens')
        a.raw(b'\x60')                                        # pushad
        a.raw(b'\xBF' + le32(edge_len_va))                    # edi = edge_len
        a.raw(b'\xB9' + le32(cfg.OVERLAY_EDGE_MAX))           # ecx = edge cap
        a.raw(b'\xB8\x01\x00\x00\x00')                        # eax = 1 (default cost)
        a.raw(b'\xFC\xF3\xAB')                                # cld; rep stosd
        a.raw(b'\x31\xF6')                                    # esi = 0 (e)
        a.label('bel_loop')
        a.raw(b'\x3B\x35' + le32(ecount_va))                  # e >= edge_count?
        a.jae('bel_done')
        a.raw(b'\x81\xFE' + le32(cfg.OVERLAY_EDGE_MAX))       # e >= cap?
        a.jae('bel_done')
        a.raw(b'\x8B\x04\xB5' + le32(edges_va))               # eax = edges[e]
        a.raw(b'\x0F\xB7\xD8')                                # ebx = i (low16)
        a.raw(b'\xC1\xE8\x10')                                # eax = j (high16)
        a.raw(b'\x3B\x1D' + le32(vcount_va))                  # i >= vcount?
        a.jae('bel_next')
        a.raw(b'\x3B\x05' + le32(vcount_va))                  # j >= vcount?
        a.jae('bel_next')
        a.raw(b'\xD9\x04\xC5' + le32(verts_va))               # fld vx[j]
        a.raw(b'\xD8\x24\xDD' + le32(verts_va))               # fsub vx[i]
        a.raw(b'\xD8\xC8')                                    # fmul st,st
        a.raw(b'\xD9\x04\xC5' + le32(verts_va + 4))           # fld vy[j]
        a.raw(b'\xD8\x24\xDD' + le32(verts_va + 4))           # fsub vy[i]
        a.raw(b'\xD8\xC8')                                    # fmul st,st
        a.raw(b'\xDE\xC1')                                    # faddp -> len^2
        a.raw(b'\xD9\xFA')                                    # fsqrt -> len px
        a.raw(b'\xD8\x35' + le32(elen_quantum_va))            # fdiv quantum px/unit
        a.raw(b'\xDB\x1D' + le32(bfs_u_va))                   # fistp (round-nearest)
        a.raw(b'\xA1' + le32(bfs_u_va))                       # eax = quantized len
        a.raw(b'\x83\xF8\x01')                                # cmp eax, 1
        a.jge('bel_store')                                    # >= 1 -> keep
        a.raw(b'\xB8\x01\x00\x00\x00')                        # min cost 1
        a.label('bel_store')
        a.raw(b'\x89\x04\xB5' + le32(edge_len_va))            # edge_len[e] = eax
        a.label('bel_next')
        a.raw(b'\x46')                                        # ++e
        a.jmp('bel_loop')
        a.label('bel_done')
        a.raw(b'\x61')                                        # popad
        a.raw(b'\xC3')

    if door_route:
        # =================================================================
        # bfs_run: one WEIGHTED shortest-path pass (SPFA — queue-based
        # Bellman-Ford; positive edge costs from edge_len[], so it
        # terminates and yields exact shortest paths). Inputs (scratch):
        # bfs_start (seed node), bfs_disti (distance-row base, pre-cleared
        # to -1 = INF), bfs_skip (1 = gate edges crossing currently-blocked
        # doors). The queue is a VMAX ring (free-running head/tail masked on
        # access) with per-node in-queue flags (bfs_inq) so a node is
        # enqueued at most once at a time — max in-flight = VMAX, the ring
        # never overflows. Distances are physical lengths in
        # WP_EDGE_LEN_QUANTUM px units (portal hops stay cost 1: teleports
        # are near-free and strongly preferred). Clobbers GPRs.
        # =================================================================
        assert weighted, 'door_route builds carry the weighted-routing fields'
        a.label('bfs_run')
        a.raw(b'\xBF' + le32(bfs_inq_va))                    # edi = bfs_inq
        a.raw(b'\xB9' + le32(VMAX // 4))                     # ecx = VMAX/4 dwords
        a.raw(b'\x31\xC0')                                   # eax = 0
        a.raw(b'\xFC\xF3\xAB')                               # cld; rep stosd (clear inq)
        a.raw(b'\x8B\x1D' + le32(bfs_start_va))              # ebx = start node
        a.raw(b'\x8B\x0D' + le32(bfs_disti_va))              # ecx = disti
        a.raw(b'\xC7\x04\x99\x00\x00\x00\x00')               # disti[start] = 0
        a.raw(b'\xC6\x83' + le32(bfs_inq_va) + b'\x01')      # inq[start] = 1
        a.raw(b'\x89\x1D' + le32(bfs_queue_va))              # queue[0] = start
        a.raw(b'\xC7\x05' + le32(bfs_head_va) + le32(0))     # head = 0
        a.raw(b'\xC7\x05' + le32(bfs_tail_va) + le32(1))     # tail = 1

        # MULTI-SOURCE entry (the SK mineral field): the caller pre-clears
        # the row + bfs_inq itself, seeds N nodes (disti[n]=0, inq[n]=1,
        # queue[tail&mask]=n, tail++) with head=0, then calls here to run
        # the relaxation loop over the pre-seeded ring. inq-dedup bounds the
        # seeds at VMAX distinct nodes, so the ring cannot overflow.
        a.label('bfs_run_seeded')
        a.label('bfsr_loop')
        a.raw(b'\xA1' + le32(bfs_head_va))                    # eax = head
        a.raw(b'\x3B\x05' + le32(bfs_tail_va))               # cmp eax, tail
        a.jae('bfsr_done')                                    # head==tail -> empty
        a.raw(b'\x25' + le32(VMAX - 1))                       # ring index (and eax, mask)
        a.raw(b'\x8B\x0C\x85' + le32(bfs_queue_va))         # ecx = queue[head & mask]
        a.raw(b'\x89\x0D' + le32(bfs_u_va))                  # bfs_u = u
        a.raw(b'\xFF\x05' + le32(bfs_head_va))              # head++
        a.raw(b'\xC6\x81' + le32(bfs_inq_va) + b'\x00')      # inq[u] = 0 (re-enqueueable)
        a.raw(b'\x8B\x15' + le32(bfs_disti_va))             # edx = disti
        a.raw(b'\x8B\x04\x8A')                               # eax = disti[u] (du)
        a.raw(b'\x89\x05' + le32(bfs_du_va))                # bfs_du = du
        a.raw(b'\x31\xF6')                                   # esi = 0 (edge idx)

        a.label('bfsr_edge_loop')
        a.raw(b'\x3B\x35' + le32(ecount_va))                # cmp esi, edge_count
        if portal_route:
            a.jae('bfsr_portals')                           # edges done -> portal relax
        else:
            a.jae('bfsr_loop')                              # edges done -> next BFS node
        # Blocked-door precheck -> cnh_blk spill (the directional pass test
        # needs the traversal side, which is only known after decoding the
        # edge). Open doors and doorless edges are never gated.
        a.raw(b'\xC7\x05' + le32(cnh_blk_va) + le32(0))     # cnh_blk = 0
        a.raw(b'\x83\x3D' + le32(bfs_skip_va) + b'\x00')    # skipping blocked edges?
        a.jz('bfsr_no_blk')
        a.raw(b'\x8B\x04\xB5' + le32(edge_door_va))         # eax = edge_door[e]
        a.raw(b'\x83\xF8\xFF')                              # no door on this edge?
        a.jz('bfsr_no_blk')
        a.raw(b'\x3B\x05' + le32(door_count_va))            # stale idx safety
        a.jae('bfsr_no_blk')
        a.raw(b'\x83\x3C\x85' + le32(door_blocked_va) + b'\x00')  # door closed?
        a.jz('bfsr_no_blk')
        a.raw(b'\xC7\x05' + le32(cnh_blk_va) + le32(1))     # closed door on this edge
        a.label('bfsr_no_blk')
        a.raw(b'\x8B\x04\xB5' + le32(edges_va))            # eax = edges[esi]
        a.raw(b'\x0F\xB7\xD8')                              # movzx ebx, ax (i)
        a.raw(b'\xC1\xE8\x10')                              # shr eax, 16   (j)
        a.raw(b'\x8B\x0D' + le32(bfs_u_va))                # ecx = u
        a.raw(b'\x39\xCB')                                  # cmp ebx, ecx (i == u?)
        a.jz('bfsr_edge_v_j')                               # i==u -> v = j (eax)
        a.raw(b'\x39\xC8')                                  # cmp eax, ecx (j == u?)
        a.jz('bfsr_edge_v_i')                               # j==u -> v = i (ebx)
        a.jmp('bfsr_edge_next')
        a.label('bfsr_edge_v_i')
        # v = i. The bot walks v -> u (decreasing dist), so it enters this
        # edge FROM node i. PHYSICAL-STATE (cfg.DOOR_ROUTE_PHYSICAL_STATE): any
        # closed door is impassable, route around it. Else directional: the
        # closed door must be openable from i by the field's team
        # (door_mask_i = 1 << team*2, set by the caller).
        a.raw(b'\x89\xD8')                                  # eax = ebx (v = i)
        a.raw(b'\x83\x3D' + le32(cnh_blk_va) + b'\x00')
        if cfg.DOOR_ROUTE_PHYSICAL_STATE:
            a.jnz('bfsr_edge_next')                         # closed door -> impassable
            a.jmp('bfsr_pass_ok')
            a.label('bfsr_edge_v_j')                        # v = j: bot enters from j
            a.raw(b'\x83\x3D' + le32(cnh_blk_va) + b'\x00')
            a.jnz('bfsr_edge_next')
        else:
            a.jz('bfsr_pass_ok')
            a.raw(b'\x0F\xB6\x14\x35' + le32(edge_pass_va)) # movzx edx, edge_pass[e]
            a.raw(b'\x85\x15' + le32(door_mask_i_va))       # test edx, [door_mask_i]
            a.jz('bfsr_edge_next')
            a.jmp('bfsr_pass_ok')
            a.label('bfsr_edge_v_j')                        # v = j: bot enters from j
            a.raw(b'\x83\x3D' + le32(cnh_blk_va) + b'\x00')
            a.jz('bfsr_pass_ok')
            a.raw(b'\x0F\xB6\x14\x35' + le32(edge_pass_va)) # movzx edx, edge_pass[e]
            a.raw(b'\x85\x15' + le32(door_mask_j_va))       # test edx, [door_mask_j]
            a.jz('bfsr_edge_next')
        a.label('bfsr_pass_ok')
        a.raw(b'\x3B\x05' + le32(vcount_va))               # cmp eax, vertex_count
        a.jae('bfsr_edge_next')                            # out of range
        # Weighted relax: cand = du + edge_len[e]; improve-or-skip (INF =
        # 0xFFFFFFFF loses every unsigned compare, so "unvisited" needs no
        # special case); enqueue v unless already in the ring.
        a.raw(b'\x8B\x0D' + le32(bfs_du_va))              # ecx = du
        a.raw(b'\x03\x0C\xB5' + le32(edge_len_va))        # ecx += edge_len[e]
        a.raw(b'\x8B\x15' + le32(bfs_disti_va))           # edx = disti
        a.raw(b'\x3B\x0C\x82')                             # cand vs disti[v]
        a.jae('bfsr_edge_next')                            # no improvement
        a.raw(b'\x89\x0C\x82')                            # disti[v] = cand
        a.raw(b'\x80\xB8' + le32(bfs_inq_va) + b'\x00')    # inq[v] set?
        a.jnz('bfsr_edge_next')                            # already queued
        a.raw(b'\xC6\x80' + le32(bfs_inq_va) + b'\x01')    # inq[v] = 1
        a.raw(b'\x8B\x0D' + le32(bfs_tail_va))           # ecx = tail
        a.raw(b'\x81\xE1' + le32(VMAX - 1))                # ring index (and ecx, mask)
        a.raw(b'\x89\x04\x8D' + le32(bfs_queue_va))      # queue[tail & mask] = v (eax)
        a.raw(b'\xFF\x05' + le32(bfs_tail_va))           # tail++
        a.label('bfsr_edge_next')
        a.raw(b'\x46')                                    # inc esi
        a.jmp('bfsr_edge_loop')

        if portal_route:
            # --- Portal relax pass for the dequeued node u. A pad whose
            # DESTINATION's nearest node is u gives its SOURCE node distance
            # du+1 (the bot walks source -> pad -> comes out at dest). No
            # door gating (pads are not doors) and no live-active gating
            # (fields are per-match; see the portal_route comment above).
            a.label('bfsr_portals')
            a.raw(b'\x31\xFF')                            # edi = 0 (p)
            a.label('bfsr_pp_loop')
            a.raw(b'\x3B\x3D' + le32(portal_count_va))    # p >= portal_count?
            a.jae('bfsr_loop')
            a.raw(b'\x83\xFF' + bytes([cfg.PORTAL_TABLE_MAX]))
            a.jae('bfsr_loop')
            a.raw(b'\x83\x3C\xBD' + le32(portal_has_dest_va) + b'\x00')
            a.jz('bfsr_pp_next')                          # no directed edge
            a.raw(b'\x8B\x04\xBD' + le32(portal_dest_node_va))  # eax = dest node
            a.raw(b'\x3B\x05' + le32(bfs_u_va))           # dest node == u?
            a.jnz('bfsr_pp_next')
            a.raw(b'\x8B\x04\xBD' + le32(portal_node_va)) # eax = source pad node
            a.raw(b'\x83\xF8\xFF')                        # unbound?
            a.jz('bfsr_pp_next')
            a.raw(b'\x3B\x05' + le32(vcount_va))          # defensive range
            a.jae('bfsr_pp_next')
            # Weighted relax, pad cost 1 (near-free — teleporting is
            # instant, so pads stay strongly preferred, matching their old
            # +1 hop semantics against px-quantum walk costs).
            a.raw(b'\x8B\x0D' + le32(bfs_du_va))          # ecx = du
            a.raw(b'\x41')                                # inc ecx (du+1)
            a.raw(b'\x8B\x15' + le32(bfs_disti_va))       # edx = disti
            a.raw(b'\x3B\x0C\x82')                        # cand vs disti[src]
            a.jae('bfsr_pp_next')                         # no improvement
            a.raw(b'\x89\x0C\x82')                        # disti[src] = cand
            a.raw(b'\x80\xB8' + le32(bfs_inq_va) + b'\x00')  # inq[src] set?
            a.jnz('bfsr_pp_next')
            a.raw(b'\xC6\x80' + le32(bfs_inq_va) + b'\x01')  # inq[src] = 1
            a.raw(b'\x8B\x0D' + le32(bfs_tail_va))        # ecx = tail
            a.raw(b'\x81\xE1' + le32(VMAX - 1))           # ring index
            a.raw(b'\x89\x04\x8D' + le32(bfs_queue_va))   # queue[tail & mask] = src
            a.raw(b'\xFF\x05' + le32(bfs_tail_va))        # tail++
            a.label('bfsr_pp_next')
            a.raw(b'\x47')                                # ++p
            a.jmp('bfsr_pp_loop')

        a.label('bfsr_done')
        a.raw(b'\xC3')

        # =================================================================
        # rebuild_open_routes: refresh ONLY flag_dist_open after door state
        # changed. The route nodes and the full field are static per match.
        # Called from the page-flip hook (door_dirty, debounced), so it must
        # be pushad/popad self-contained. Reuses bfr_i — single main thread,
        # never interleaved with the match-change build.
        # =================================================================
        a.label('rebuild_open_routes')
        a.raw(b'\x60')                                        # pushad
        a.raw(b'\xBF' + le32(flag_dist_open_va))              # edi = flag_dist_open
        a.raw(b'\xB9' + le32(2 * RMAX * VMAX))                # ecx = both team fields
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                        # eax = -1
        a.raw(b'\xFC\xF3\xAB')                                # cld; rep stosd
        a.raw(b'\xA1' + le32(vcount_va))                      # eax = vertex_count
        a.raw(b'\x85\xC0'); a.jz('ror_done')
        a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(1))      # skip blocked edges
        a.raw(b'\xC7\x05' + le32(bfr_i_va) + le32(0))         # i = 0
        a.label('ror_loop')
        a.raw(b'\x8B\x0D' + le32(flag_count_va))              # ecx = flag_count
        a.raw(b'\x83\xF9' + bytes([RMAX]))                    # cmp ecx, RMAX
        a.jbe('ror_nb_ok')
        a.raw(b'\xB9' + le32(RMAX))
        a.label('ror_nb_ok')
        a.raw(b'\xA1' + le32(bfr_i_va))                       # eax = i
        a.raw(b'\x39\xC8')                                    # cmp eax, ecx
        a.jae('ror_done')
        a.raw(b'\x8B\x1C\x85' + le32(route_node_va))          # ebx = flag_route_node[i]
        a.raw(b'\x83\xFB\xFF')                                # no node?
        a.jz('ror_next')
        a.raw(b'\x89\x1D' + le32(bfs_start_va))               # bfs_start = node
        # team 0 row
        a.raw(b'\x69\xC0' + le32(ROW))                        # imul eax, eax, ROW
        a.raw(b'\x05' + le32(flag_dist_open_va))              # + flag_dist_open
        a.raw(b'\xA3' + le32(bfs_disti_va))                   # bfs_disti = eax
        a.raw(b'\xC7\x05' + le32(door_mask_i_va) + le32(0x01))
        a.raw(b'\xC7\x05' + le32(door_mask_j_va) + le32(0x02))
        a.call_lbl('bfs_run')
        # team 1 row
        a.raw(b'\xA1' + le32(bfr_i_va))                       # eax = i
        a.raw(b'\x69\xC0' + le32(ROW))                        # imul eax, eax, ROW
        a.raw(b'\x05' + le32(flag_dist_open_va + RMAX * VMAX * 4))
        a.raw(b'\xA3' + le32(bfs_disti_va))                   # bfs_disti = eax
        a.raw(b'\xC7\x05' + le32(door_mask_i_va) + le32(0x04))
        a.raw(b'\xC7\x05' + le32(door_mask_j_va) + le32(0x08))
        a.call_lbl('bfs_run')
        a.label('ror_next')
        a.raw(b'\xFF\x05' + le32(bfr_i_va))                   # i++
        a.jmp('ror_loop')
        a.label('ror_done')
        if seek:
            # Door state changed: every seek premise (which doors are blocked,
            # which switches are viable, the seek field itself) is stale.
            # Clear the whole per-team block INCLUDING the timeout blacklist
            # (a blacklisted switch deserves a retry once the world changed);
            # bots re-request on their next arrival if still blocked. The
            # epoch bump below already forces every routed bot to re-acquire.
            a.raw(b'\xBF' + le32(seek_active_va))             # edi = seek state block
            a.raw(b'\xB9\x16\x00\x00\x00')                    # ecx = 22 dwords
            a.raw(b'\x31\xC0')                                # eax = 0
            a.raw(b'\xFC\xF3\xAB')                            # cld; rep stosd
        # Bump the route epoch so every routed bot re-acquires its node on the
        # next think and re-runs ctf_next_hop against the freshly rebuilt open
        # field. Without this, routing only re-evaluates on node arrival, so a
        # door that opens mid-edge is invisible until the bot dies and respawns
        # (a bot steering across a still-closed door never arrives at a node).
        if layout.has_field('route_epoch'):
            a.raw(b'\xFF\x05' + le32(layout.va('route_epoch')))  # ++route_epoch
        a.raw(b'\x61')                                        # popad
        a.raw(b'\xC3')
    else:
        a.label('rebuild_open_routes')
        a.raw(b'\xC3')

    # =====================================================================
    # ctf_pick_goal: set route_goal_flag = this bot's goal flag index (the HOME
    # base if carrying, else the ENEMY base; -1 when routing inactive / no goal).
    # Reads bot_slot_tmp / bot_char_tmp / bot_team. Called per-frame by the
    # follower (final-approach check) and by ctf_next_hop. Clobbers GPRs; carry
    # is spilled to route_carry so it survives the sub_4267E0/sub_425290 calls.
    # =====================================================================
    a.label('ctf_pick_goal')
    a.raw(b'\xC7\x05' + le32(route_goal_va) + le32(0xFFFFFFFF))  # route_goal_flag = -1
    a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00')       # routing active?
    a.jz('cpg_done')
    # Per-bot routing suspension: after a routed progress-timeout the follower
    # parks BFS routing for WP_ROUTE_SUSPEND_FRAMES (see bot_movement.py) so
    # the bot roams instead of being funnelled back into a blocked segment.
    # Reporting "no goal" here suspends the next-hop bias, the final approach
    # AND the far-base force-tick in one place. The counter is decremented
    # once per think by the follower; this is a pure read.
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                      # ecx = slot
    a.raw(b'\x83\x3C\x8D' + le32(route_suspend_va) + b'\x00')   # suspended?
    a.jz('cpg_not_suspended')
    a.raw(b'\xC7\x05' + le32(route_carry_va) + le32(0))         # keep global fresh
    a.jmp('cpg_done')
    a.label('cpg_not_suspended')
    # carrying? -> route_carry (live-verified inventory-group test)
    a.raw(b'\xC7\x05' + le32(route_carry_va) + le32(0))         # route_carry = 0
    a.raw(b'\x8B\x0D' + le32(bot_char_va))                      # ecx = bot char
    a.raw(b'\x85\xC9'); a.jz('cpg_carry_done')                  # NULL char
    a.call_va(ax.SUB_4267E0_VA)                                  # eax = inv (ret 0)
    a.raw(b'\x85\xC0'); a.jz('cpg_carry_done')                  # NULL inv
    a.raw(b'\x8B\x15' + le32(ax.MULTIPLAYER_FLAG_GID_VA))       # edx = FLAG_GID
    a.raw(b'\x85\xD2'); a.jz('cpg_carry_done')                  # gid unresolved
    a.raw(b'\x52')                                              # push gid
    a.raw(b'\x89\xC1')                                          # ecx = inv
    a.call_va(ax.SUB_425290_VA)                                  # eax = slot (ret 4)
    a.raw(b'\x83\xF8\xFF'); a.jz('cpg_carry_done')             # slot == -1 -> not carrying
    a.raw(b'\xC7\x05' + le32(route_carry_va) + le32(1))        # route_carry = 1
    a.label('cpg_carry_done')
    # team -> ebx (no engine calls after here)
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\x8B\x1C\x8D' + le32(bot_team_va))                 # ebx = bot_team[slot]
    a.raw(b'\x8B\x0D' + le32(flag_count_va))                   # ecx = flag_count
    a.raw(b'\x83\xF9' + bytes([RMAX]))                         # cmp ecx, RMAX
    a.jbe('cpg_nb_ok')
    a.raw(b'\xB9' + le32(RMAX))
    a.label('cpg_nb_ok')
    a.raw(b'\x85\xC9'); a.jz('cpg_done')                       # no bases
    a.raw(b'\x31\xF6')                                         # esi = 0 (i)
    a.raw(b'\xBF\xFF\xFF\xFF\xFF')                             # edi = -1 (home)
    a.raw(b'\xBA\xFF\xFF\xFF\xFF')                             # edx = -1 (enemy)
    a.label('cpg_goal_loop')
    a.raw(b'\x39\xCE'); a.jae('cpg_goal_done')                 # i >= nbase
    a.raw(b'\x8B\x04\xB5' + le32(flag_team_va))               # eax = flag_team[i]
    a.raw(b'\x39\xD8'); a.jnz('cpg_goal_enemy')               # != team -> enemy
    a.raw(b'\x83\xFF\xFF'); a.jnz('cpg_goal_next')            # home already set
    a.raw(b'\x89\xF7'); a.jmp('cpg_goal_next')                # home = i
    a.label('cpg_goal_enemy')
    a.raw(b'\x83\xFA\xFF'); a.jnz('cpg_goal_next')            # enemy already set
    a.raw(b'\x89\xF2')                                         # enemy = i
    a.label('cpg_goal_next')
    a.raw(b'\x46'); a.jmp('cpg_goal_loop')                     # ++i
    a.label('cpg_goal_done')
    a.raw(b'\x83\x3D' + le32(route_carry_va) + b'\x00')        # carrying?
    a.jz('cpg_pick_enemy')
    a.raw(b'\x89\xF8')                                         # eax = home (edi)
    a.jmp('cpg_store')
    a.label('cpg_pick_enemy')
    a.raw(b'\x89\xD0')                                         # eax = enemy (edx)
    a.label('cpg_store')
    a.raw(b'\x83\xF8\xFF'); a.jz('cpg_store_goal')             # no goal -> store -1
    a.raw(b'\x83\x3C\x85' + le32(flag_present_va) + b'\x00')  # cmp flag_present[goal], 0
    a.jz('cpg_goal_missing')
    # Goal flag is present at base: clear any missing-flag policy for this bot.
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\xC7\x04\x8D' + le32(missing_policy_va) + le32(0))
    a.raw(b'\xC7\x04\x8D' + le32(missing_goal_va) + le32(0xFFFFFFFF))
    a.jmp('cpg_store_goal')

    a.label('cpg_goal_missing')
    # If we are carrying the enemy flag, the missing goal is our OWN home flag.
    # Do not route/final-approach to an empty home base: normal CTF forbids a
    # capture while our flag is away, and the page-flip far-base tick can wake
    # capture entities that would otherwise stay camera-gated. Search instead.
    a.raw(b'\x83\x3D' + le32(route_carry_va) + b'\x00')        # carrying?
    a.jz('cpg_missing_attacker')
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\xC7\x04\x8D' + le32(missing_policy_va) + le32(1)) # policy = search
    a.raw(b'\x89\x04\x8D' + le32(missing_goal_va))             # missing_goal[slot] = goal
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                             # no goal -> random graph roam
    a.jmp('cpg_store_goal')

    a.label('cpg_missing_attacker')
    # The target flag is absent from its base. Keep the bot's policy stable
    # until this same target becomes present again or the bot switches goals.
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\x8B\x14\x8D' + le32(missing_goal_va))             # edx = missing_goal[slot]
    a.raw(b'\x39\xC2')                                         # cmp edx, eax
    a.jnz('cpg_missing_roll')                                  # new missing goal -> re-roll
    a.raw(b'\x83\x3C\x8D' + le32(missing_policy_va) + b'\x00') # cmp missing_policy[slot], 0
    a.jz('cpg_missing_roll')                                   # unset -> roll
    a.jmp('cpg_missing_have_policy')

    a.label('cpg_missing_roll')
    a.raw(b'\x89\x04\x8D' + le32(missing_goal_va))             # missing_goal[slot] = goal
    a.raw(b'\x6A\x01')                                         # push high=1
    a.raw(b'\x6A\x00')                                         # push low=0
    a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                       # ecx = RNG
    a.call_va(ax.RNG_SUB)                                      # eax = 0/1 (callee pops args)
    a.raw(b'\x40')                                             # policy = eax + 1 (1 search, 2 wait)
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\x89\x04\x8D' + le32(missing_policy_va))           # missing_policy[slot] = policy
    a.raw(b'\x8B\x04\x8D' + le32(missing_goal_va))             # eax = goal

    a.label('cpg_missing_have_policy')
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\x8B\x14\x8D' + le32(missing_policy_va))           # edx = policy
    a.raw(b'\x83\xFA\x01')                                     # policy == search?
    a.jnz('cpg_store_goal')                                    # wait -> keep eax=goal
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                             # search -> random graph roam

    a.label('cpg_store_goal')
    a.raw(b'\xA3' + le32(route_goal_va))                       # route_goal_flag = eax (or -1)
    a.label('cpg_done')
    a.raw(b'\xC3')

    # =====================================================================
    # ctf_next_hop(ECX = current node idx) -> EAX = goal-biased next node,
    # or 0xFFFFFFFF when routing can't apply (caller falls back to wp_advance).
    # Reads bot_slot_tmp / bot_char_tmp / bot_team from scratch. Called inside
    # the bot-movement pushad frame, so it may clobber any GPR.
    # =====================================================================
    a.label('ctf_next_hop')
    a.raw(b'\x89\x0D' + le32(route_cur_va))               # route_cur = ecx (cur)
    if portal_route:
        # Fresh per-arrival portal-hop output; the caller latches off it only
        # when this call reports a hop, so a stale value must never survive.
        a.raw(b'\xC7\x05' + le32(route_portal_hop_va) + le32(0))
    a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00') # cmp [flag_routing_active], 0
    a.jz('cnh_fail')
    if seek:
        # Participation is re-earned at every arrival: cleared here, set again
        # below only when this hop actually descends the seek field.
        a.raw(b'\x8B\x0D' + le32(bot_slot_va))            # ecx = slot
        a.raw(b'\xC7\x04\x8D' + le32(bot_seek_va) + le32(0))  # bot_seek[slot] = 0
    a.call_lbl('ctf_pick_goal')                           # sets route_goal_flag
    a.raw(b'\xA1' + le32(route_goal_va))                  # eax = goal flag idx
    a.raw(b'\x83\xF8\xFF'); a.jz('cnh_fail')             # no goal base

    a.raw(b'\x69\xC0' + le32(ROW))                       # imul eax, eax, ROW
    if door_route:
        # Prefer this bot's TEAM open field (closed-door edges pass only in
        # directions this team can open) so the bot routes AROUND doors it
        # cannot use. If the current node cannot reach the goal that way
        # (open dist == -1), fall back to the FULL field — the old behaviour:
        # walk at the door and let the wedge machinery / approach-open work.
        a.raw(b'\x89\xC7')                               # edi = goal row (fallback base)
        # team = bot_team[slot] & 1 -> masks + team field select
        a.raw(b'\x8B\x0D' + le32(bot_slot_va))           # ecx = slot
        a.raw(b'\x8B\x0C\x8D' + le32(bot_team_va))       # ecx = bot_team[slot]
        a.raw(b'\x83\xE1\x01')                           # and ecx, 1 (defensive)
        a.raw(b'\x01\xC9')                               # add ecx, ecx (cl = team*2)
        a.raw(b'\xBA\x01\x00\x00\x00')                   # edx = 1
        a.raw(b'\xD3\xE2')                               # shl edx, cl
        a.raw(b'\x89\x15' + le32(door_mask_i_va))        # door_mask_i = 1 << team*2
        a.raw(b'\x01\xD2')                               # edx += edx
        a.raw(b'\x89\x15' + le32(door_mask_j_va))        # door_mask_j = 2 << team*2
        a.raw(b'\x85\xC9')                               # team 1?
        a.jz('cnh_team_row_ok')
        a.raw(b'\x05' + le32(TEAM_ROW))                  # eax += team-1 field stride
        a.label('cnh_team_row_ok')
        a.raw(b'\xC7\x05' + le32(route_use_open_va) + le32(1))
        a.raw(b'\x8D\xA8' + le32(flag_dist_open_va))     # lea ebp, [eax + flag_dist_open]
        a.raw(b'\x8B\x1D' + le32(route_cur_va))          # ebx = cur
        a.raw(b'\x8B\x4C\x9D\x00')                        # ecx = open_dist[cur]
        if seek:
            # --- Switch-seek gate. Two triggers: the goal is open-field
            # UNREACHABLE from here (sealed base), or reachable only via a
            # detour SHORTCUT_GAIN+ hops worse than the full field's
            # closed-door path (the "inside switch" shortcut). While a team
            # seek is ACTIVE and this node reaches the switch, descend the
            # seek field instead; otherwise request an eval and keep the
            # normal open-or-full behaviour meanwhile.
            a.raw(b'\x83\xF9\xFF')                        # open unreachable?
            a.jz('cnh_seek_gate')
            a.raw(b'\x89\xFA')                            # edx = edi (goal row offset)
            a.raw(b'\x81\xC2' + le32(flag_dist_va))       # edx = full row base
            a.raw(b'\x8B\x14\x9A')                        # edx = full_dist[cur]
            a.raw(b'\x83\xFA\xFF')                        # full unreachable?
            a.jz('cnh_field_ok')                          # keep open field
            a.raw(b'\x83\xC2' + bytes([cfg.SWITCH_SEEK_SHORTCUT_GAIN]))
            a.raw(b'\x39\xCA')                            # cmp full+GAIN, open
            a.jb('cnh_seek_gate')                         # big detour -> try seek
            a.jmp('cnh_field_ok')

            a.label('cnh_seek_gate')
            a.raw(b'\x89\xC8')                            # eax = open dist (or -1) for req_open
            # team from door_mask_i (1 = team0, 4 = team1; set above)
            a.raw(b'\x31\xD2')                            # edx = 0
            a.raw(b'\x83\x3D' + le32(door_mask_i_va) + b'\x01')
            a.jz('cnh_seek_t0')
            a.raw(b'\x42')                                # edx = 1
            a.label('cnh_seek_t0')
            a.raw(b'\x8B\x0C\x95' + le32(seek_active_va)) # ecx = seek_active[team]
            a.raw(b'\x85\xC9')
            a.jnz('cnh_seek_have')
            # No active seek: request an eval (once) and route normally.
            a.raw(b'\x83\x3C\x95' + le32(seek_pending_va) + b'\x00')
            a.jnz('cnh_seek_fb')
            a.raw(b'\xC7\x04\x95' + le32(seek_pending_va) + le32(1))
            a.raw(b'\x89\x1C\x95' + le32(seek_req_node_va))   # req_node = cur
            a.raw(b'\x89\x04\x95' + le32(seek_req_open_va))   # req_open = open dist (benefit bar)
            a.raw(b'\xA1' + le32(route_goal_va))              # eax = goal idx
            a.raw(b'\x89\x04\x95' + le32(seek_req_goal_va))   # req_goal = goal
            a.raw(b'\xC7\x04\x95' + le32(seek_best_va) + le32(0))  # fresh eval round
            a.jmp('cnh_seek_fb')

            a.label('cnh_seek_have')
            a.raw(b'\x89\xD0')                            # eax = team
            a.raw(b'\x69\xC0' + le32(SEEK_ROW))           # eax *= SEEK_ROW
            a.raw(b'\x05' + le32(seek_dist_va))           # eax = seek row base
            a.raw(b'\x8B\x0C\x98')                        # ecx = seek_dist[cur]
            a.raw(b'\x83\xF9\xFF')                        # switch reachable from here?
            a.jz('cnh_seek_fb')
            a.raw(b'\x89\xC5')                            # ebp = seek row
            a.raw(b'\x8B\x15' + le32(bot_slot_va))        # edx = slot
            a.raw(b'\xC7\x04\x95' + le32(bot_seek_va) + le32(1))  # bot_seek[slot] = 1
            a.jmp('cnh_field_ok')                         # ecx = cur seek dist

            a.label('cnh_seek_fb')
            # Fallback = the original open-or-full selection (ebp = open row).
            a.raw(b'\x8B\x4C\x9D\x00')                    # ecx = open_dist[cur]
            a.raw(b'\x83\xF9\xFF')
            a.jnz('cnh_field_ok')                         # shortcut case: open field
        else:
            a.raw(b'\x83\xF9\xFF')                        # reachable for this team?
            a.jnz('cnh_field_ok')
        a.raw(b'\xC7\x05' + le32(route_use_open_va) + le32(0))
        a.raw(b'\x8D\xAF' + le32(flag_dist_va))          # lea ebp, [edi + flag_dist]
        a.raw(b'\x8B\x4C\x9D\x00')                        # ecx = full_dist[cur]
        a.raw(b'\x83\xF9\xFF')                            # cmp ecx, -1 (cur unreachable?)
        a.jz('cnh_fail')
        a.label('cnh_field_ok')
    else:
        # disti = flag_dist + goal*ROW -> EBP
        a.raw(b'\x05' + le32(flag_dist_va))             # add eax, flag_dist
        a.raw(b'\x89\xC5')                               # ebp = disti
        # cur -> EBX; cur_d = disti[cur] -> ECX (best-distance threshold)
        a.raw(b'\x8B\x1D' + le32(route_cur_va))         # ebx = cur
        a.raw(b'\x8B\x4C\x9D\x00')                       # ecx = [ebp + ebx*4] (cur_d)
        a.raw(b'\x83\xF9\xFF')                           # cmp ecx, -1 (cur unreachable?)
        a.jz('cnh_fail')
    a.raw(b'\xBA\xFF\xFF\xFF\xFF')                       # edx = -1 (best)
    a.raw(b'\x31\xF6')                                   # esi = 0 (edge idx)

    a.label('cnh_scan_loop')
    a.raw(b'\x3B\x35' + le32(ecount_va))                # cmp esi, edge_count
    a.jae('cnh_scan_done')
    if door_route:
        # While scanning the OPEN field, a closed-door edge is usable only in
        # a direction the bot could OPEN the door from (a neighbour can carry
        # a smaller open-distance via ANOTHER path while the direct cur->nb
        # edge is the closed door itself). Precheck spills to cnh_blk; the
        # directional bit is tested after the edge is decoded (from = cur).
        a.raw(b'\xC7\x05' + le32(cnh_blk_va) + le32(0))  # cnh_blk = 0
        a.raw(b'\x83\x3D' + le32(route_use_open_va) + b'\x00')
        a.jz('cnh_no_door_skip')
        a.raw(b'\x8B\x04\xB5' + le32(edge_door_va))      # eax = edge_door[e]
        a.raw(b'\x83\xF8\xFF')                           # door on this edge?
        a.jz('cnh_no_door_skip')
        a.raw(b'\x3B\x05' + le32(door_count_va))         # stale idx safety
        a.jae('cnh_no_door_skip')
        a.raw(b'\x83\x3C\x85' + le32(door_blocked_va) + b'\x00')
        a.jz('cnh_no_door_skip')
        a.raw(b'\xC7\x05' + le32(cnh_blk_va) + le32(1))  # closed door on this edge
        a.label('cnh_no_door_skip')
    a.raw(b'\x8B\x04\xB5' + le32(edges_va))            # eax = edges[esi]
    a.raw(b'\x0F\xB7\xF8')                              # movzx edi, ax (i)
    a.raw(b'\xC1\xE8\x10')                              # shr eax, 16   (j)
    a.raw(b'\x39\xDF')                                  # cmp edi, ebx (i == cur?)
    a.jz('cnh_nb_j')                                    # nb = j (eax); bot crosses FROM i
    a.raw(b'\x39\xD8')                                  # cmp eax, ebx (j == cur?)
    a.jz('cnh_nb_i')                                    # nb = i (edi); bot crosses FROM j
    a.jmp('cnh_scan_next')
    a.label('cnh_nb_i')
    a.raw(b'\x89\xF8')                                  # eax = edi (nb = i)
    if door_route:
        a.raw(b'\x83\x3D' + le32(cnh_blk_va) + b'\x00')
        if cfg.DOOR_ROUTE_PHYSICAL_STATE:
            a.jnz('cnh_scan_next')                      # closed door -> impassable
            a.jmp('cnh_pass_ok')
        else:
            a.jz('cnh_pass_ok')
            a.raw(b'\x0F\xB6\x3C\x35' + le32(edge_pass_va)) # movzx edi, edge_pass[e]
            a.raw(b'\x85\x3D' + le32(door_mask_j_va))   # openable from j (cur side), this team?
            a.jz('cnh_scan_next')
            a.jmp('cnh_pass_ok')
    a.label('cnh_nb_j')                                # nb in eax
    if door_route:
        a.raw(b'\x83\x3D' + le32(cnh_blk_va) + b'\x00')
        if cfg.DOOR_ROUTE_PHYSICAL_STATE:
            a.jnz('cnh_scan_next')                      # closed door -> impassable
        else:
            a.jz('cnh_pass_ok')
            a.raw(b'\x0F\xB6\x3C\x35' + le32(edge_pass_va)) # movzx edi, edge_pass[e]
            a.raw(b'\x85\x3D' + le32(door_mask_i_va))   # openable from i (cur side), this team?
            a.jz('cnh_scan_next')
        a.label('cnh_pass_ok')
    a.raw(b'\x3B\x05' + le32(vcount_va))               # cmp eax, vertex_count
    a.jae('cnh_scan_next')                             # out of range
    a.raw(b'\x8B\x7C\x85\x00')                          # edi = [ebp + eax*4] (nd = disti[nb])
    a.raw(b'\x39\xCF')                                  # cmp edi, ecx (nd - best_d)
    a.jae('cnh_scan_next')                             # nd >= best_d -> not closer
    a.raw(b'\x89\xF9')                                  # ecx = edi (best_d = nd)
    a.raw(b'\x89\xC2')                                  # edx = eax (best = nb)
    a.label('cnh_scan_next')
    a.raw(b'\x46')                                      # inc esi
    a.jmp('cnh_scan_loop')

    a.label('cnh_scan_done')
    if portal_route:
        # --- Portal hop. A pad bound to the CURRENT node whose destination
        # node carries a strictly smaller distance in the ACTIVE field (ebp
        # row — full/open/seek all relax portals identically) beats the best
        # neighbour found above. Winner goes to route_portal_hop (pad idx+1)
        # and the call returns CUR itself — the caller latches the pad
        # final-approach instead of steering to a node. Gated on the LIVE
        # pad usability flag so a deactivated teleporter is never entered.
        # EBX = cur, EBP = field row, ECX = best_d, EDX = best node here.
        a.raw(b'\x31\xF6')                              # esi = 0 (p)
        a.label('cnh_pp_loop')
        a.raw(b'\x3B\x35' + le32(portal_count_va))      # p >= portal_count?
        a.jae('cnh_pp_done')
        a.raw(b'\x83\xFE' + bytes([cfg.PORTAL_TABLE_MAX]))
        a.jae('cnh_pp_done')
        a.raw(b'\x83\x3C\xB5' + le32(portal_has_dest_va) + b'\x00')
        a.jz('cnh_pp_next')
        if portal_active_va:
            a.raw(b'\x83\x3C\xB5' + le32(portal_active_va) + b'\x00')
            a.jz('cnh_pp_next')                         # pad currently unusable
        a.raw(b'\x8B\x04\xB5' + le32(portal_node_va))   # eax = pad node
        a.raw(b'\x39\xD8')                              # pad at cur?
        a.jnz('cnh_pp_next')
        a.raw(b'\x8B\x04\xB5' + le32(portal_dest_node_va))  # eax = dest node
        a.raw(b'\x83\xF8\xFF')                          # unbound?
        a.jz('cnh_pp_next')
        a.raw(b'\x3B\x05' + le32(vcount_va))            # defensive range
        a.jae('cnh_pp_next')
        a.raw(b'\x8B\x7C\x85\x00')                      # edi = dist[dest node]
        a.raw(b'\x39\xCF')                              # cmp edi, best_d
        a.jae('cnh_pp_next')                            # not strictly closer
        a.raw(b'\x89\xF9')                              # best_d = edi
        a.raw(b'\x8D\x46\x01')                          # lea eax, [esi+1]
        a.raw(b'\xA3' + le32(route_portal_hop_va))      # route_portal_hop = p+1
        a.label('cnh_pp_next')
        a.raw(b'\x46')                                  # ++p
        a.jmp('cnh_pp_loop')
        a.label('cnh_pp_done')
        a.raw(b'\x83\x3D' + le32(route_portal_hop_va) + b'\x00')
        a.jz('cnh_node_ret')
        a.raw(b'\x89\xD8')                              # eax = cur (latch drives movement)
        a.raw(b'\xC3')
        a.label('cnh_node_ret')
    a.raw(b'\x89\xD0')                                  # eax = edx (best, or -1)
    a.raw(b'\xC3')

    a.label('cnh_fail')
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                      # eax = -1
    a.raw(b'\xC3')

    # =====================================================================
    # switch_seek_eval: per-frame (page flip) seek servicing. For each team:
    # an ACTIVE seek ticks its timeout (expiry blacklists the switch until
    # the next door-state change); a PENDING request evaluates AT MOST ONE
    # candidate per frame — the untried door-opening switch with a currently
    # BLOCKED paired door, a bound node, and the smallest full-field distance
    # to the requester's goal — by running one team-door-gated bfs_run rooted
    # at the switch node into this team's seek_dist row. Requester reachable
    # => activate; unreachable => mark tried, next frame tries the next
    # candidate; none left => drop the request (bots keep today's fallback).
    # One BFS per frame keeps the page flip smooth. pushad/popad.
    # =====================================================================
    if not seek:
        a.label('switch_seek_eval')
        a.raw(b'\xC3')
    else:
        a.label('switch_seek_eval')
        a.raw(b'\x60')                                          # pushad
        a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00')  # CTF routing live?
        a.jz('sse_out')
        a.raw(b'\x31\xED')                                      # ebp = 0 (team)

        a.label('sse_team_loop')
        a.raw(b'\x8B\x04\xAD' + le32(seek_active_va))           # eax = seek_active[team]
        a.raw(b'\x85\xC0')
        a.jz('sse_check_pending')
        # Active: tick the timeout.
        a.raw(b'\x8B\x0C\xAD' + le32(seek_timer_va))            # ecx = timer
        a.raw(b'\x85\xC9'); a.jz('sse_expire')                  # defensive
        a.raw(b'\x49')                                          # dec ecx
        a.raw(b'\x89\x0C\xAD' + le32(seek_timer_va))            # store back
        a.jnz('sse_next_team')                                  # still ticking
        a.label('sse_expire')
        a.raw(b'\x48')                                          # eax = switch idx
        a.raw(b'\x89\xC1')                                      # ecx = idx
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl
        a.raw(b'\x09\x14\xAD' + le32(seek_fail_va))             # fail |= 1 << idx
        a.raw(b'\xC7\x04\xAD' + le32(seek_active_va) + le32(0))
        a.jmp('sse_next_team')

        a.label('sse_check_pending')
        a.raw(b'\x83\x3C\xAD' + le32(seek_pending_va) + b'\x00')
        a.jz('sse_next_team')
        # ---- find the FIRST untried viable candidate (cheap filters run in
        # one frame; the BFS below is at most ONE per frame). Every candidate
        # is scored by the DETOUR metric seek_walk(requester -> switch) +
        # full_dist(switch -> goal); the best is activated once all have been
        # evaluated. Cheap-rejects are marked tried immediately so the scan
        # never revisits them.
        a.raw(b'\x31\xF6')                                      # esi = 0 (s)
        a.label('sse_cand_loop')
        a.raw(b'\x3B\x35' + le32(switch_count_va))              # s >= switch_count?
        a.jae('sse_no_more')
        a.raw(b'\x83\xFE' + bytes([cfg.SWITCH_TABLE_MAX]))      # s >= table max?
        a.jae('sse_no_more')
        # tried/blacklisted?
        a.raw(b'\x89\xF1')                                      # ecx = s
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl (1<<s)
        a.raw(b'\x8B\x04\xAD' + le32(seek_tried_va))            # eax = tried[team]
        a.raw(b'\x0B\x04\xAD' + le32(seek_fail_va))             # eax |= fail[team]
        a.raw(b'\x85\xD0')                                      # test eax, edx
        a.jnz('sse_cand_next')
        # door-opening class?
        a.raw(b'\xF6\x04\x35' + le32(switch_flags_va) + b'\x01')  # flags[s] & OPENS?
        a.jz('sse_cand_tried')
        # node bound?
        a.raw(b'\x8B\x04\xB5' + le32(switch_node_va))           # eax = switch_node[s]
        a.raw(b'\x83\xF8\xFF')
        a.jz('sse_cand_tried')
        # any paired door currently blocked? (also the toggle-safety gate:
        # a toggler whose doors are all open must never be bumped shut)
        a.raw(b'\x31\xC9')                                      # ecx = 0 (pair idx)
        a.label('sse_pair_loop')
        a.raw(b'\x3B\x0D' + le32(switch_pair_count_va))         # pairs done?
        a.jae('sse_cand_tried')                                 # none blocked -> reject
        a.raw(b'\x8B\x14\x8D' + le32(switch_pairs_va))          # edx = pair record
        a.raw(b'\x0F\xB7\xC2')                                  # movzx eax, dx (switch idx)
        a.raw(b'\x39\xF0')                                      # cmp eax, esi
        a.jnz('sse_pair_next')
        a.raw(b'\xC1\xEA\x10')                                  # edx >>= 16 (door idx)
        a.raw(b'\x3B\x15' + le32(door_count_va))                # stale idx?
        a.jae('sse_pair_next')
        a.raw(b'\x83\x3C\x95' + le32(door_blocked_va) + b'\x00')
        a.jnz('sse_have_cand')
        a.label('sse_pair_next')
        a.raw(b'\x41')                                          # ++pair idx
        a.jmp('sse_pair_loop')
        a.label('sse_cand_tried')
        # Cheap reject: mark tried so tomorrow's scan skips it, keep scanning.
        a.raw(b'\x89\xF1')                                      # ecx = s
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl
        a.raw(b'\x09\x14\xAD' + le32(seek_tried_va))            # tried |= 1<<s
        a.label('sse_cand_next')
        a.raw(b'\x46')                                          # ++s
        a.jmp('sse_cand_loop')

        a.label('sse_have_cand')
        # One team-gated BFS rooted at candidate esi's node into seek_dist[team].
        a.raw(b'\x89\x35' + le32(seek_eval_s_va))               # spill candidate s (NOT bfs_u: bfs_run clobbers it)
        a.raw(b'\x89\xE8')                                      # eax = team
        a.raw(b'\x69\xC0' + le32(SEEK_ROW))                     # eax *= SEEK_ROW
        a.raw(b'\x05' + le32(seek_dist_va))                     # eax = seek row
        a.raw(b'\xA3' + le32(bfs_disti_va))                     # bfs_disti = row
        a.raw(b'\x89\xC7')                                      # edi = row
        a.raw(b'\xB9' + le32(VMAX))                             # ecx = VMAX
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
        a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd (clear row)
        a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(1))        # skip blocked edges
        a.raw(b'\x8D\x4C\x2D\x00')                              # lea ecx, [ebp+ebp] (team*2)
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl
        a.raw(b'\x89\x15' + le32(door_mask_i_va))               # door_mask_i = 1<<team*2
        a.raw(b'\x01\xD2')                                      # edx += edx
        a.raw(b'\x89\x15' + le32(door_mask_j_va))               # door_mask_j = 2<<team*2
        a.raw(b'\xA1' + le32(seek_eval_s_va))                   # eax = candidate s
        a.raw(b'\x8B\x04\x85' + le32(switch_node_va))           # eax = switch_node[s]
        a.raw(b'\xA3' + le32(bfs_start_va))                     # bfs_start = node
        a.raw(b'\x89\x2D' + le32(bfr_i_va))                     # spill team (bfs clobbers GPRs)
        a.call_lbl('bfs_run')
        a.raw(b'\x8B\x2D' + le32(bfr_i_va))                     # ebp = team
        # Mark tried regardless of the outcome.
        a.raw(b'\x8B\x0D' + le32(seek_eval_s_va))               # ecx = candidate s
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl
        a.raw(b'\x09\x14\xAD' + le32(seek_tried_va))            # tried |= 1<<s
        # Requester reachable?
        a.raw(b'\x8B\x04\xAD' + le32(seek_req_node_va))         # eax = req node
        a.raw(b'\x3B\x05' + le32(vcount_va))                    # defensive range
        a.jae('sse_next_team')
        a.raw(b'\x89\xE9')                                      # ecx = team
        a.raw(b'\x69\xC9' + le32(SEEK_ROW))                     # ecx *= SEEK_ROW
        a.raw(b'\x81\xC1' + le32(seek_dist_va))                 # ecx = seek row
        a.raw(b'\x8B\x0C\x81')                                  # ecx = seek_dist[req node]
        a.raw(b'\x83\xF9\xFF')
        a.jz('sse_next_team')                                   # unreachable -> next frame
        # score2 = seek walk + full_dist(switch_node -> req_goal)
        a.raw(b'\x8B\x04\xAD' + le32(seek_req_goal_va))         # eax = req_goal
        a.raw(b'\x83\xF8' + bytes([RMAX]))                      # defensive range
        a.jae('sse_next_team')
        a.raw(b'\x69\xC0' + le32(ROW))                          # eax *= ROW
        a.raw(b'\x05' + le32(flag_dist_va))                     # eax = full row base
        a.raw(b'\x8B\x15' + le32(seek_eval_s_va))               # edx = candidate s
        a.raw(b'\x8B\x14\x95' + le32(switch_node_va))           # edx = switch_node[s]
        a.raw(b'\x8B\x04\x90')                                  # eax = full[switch node]
        a.raw(b'\x83\xF8\xFF')                                  # goal unreachable from switch?
        a.jz('sse_next_team')
        a.raw(b'\x01\xC1')                                      # ecx += eax (score2)
        # BENEFIT bar: the switch route (walk + ideal post-open path) must
        # BEAT the requester's current open route, else the detour is a net
        # loss and the candidate is skipped. req_open == -1 (goal open-field
        # unreachable, e.g. sealed Torture bases) accepts any viable switch.
        a.raw(b'\x8B\x04\xAD' + le32(seek_req_open_va))         # eax = req_open[team]
        a.raw(b'\x83\xF8\xFF')
        a.jz('sse_benefit_ok')
        a.raw(b'\x39\xC1')                                      # cmp score2, req_open
        a.jae('sse_next_team')                                  # no gain -> skip candidate
        a.label('sse_benefit_ok')
        # Better than the round's best so far? (best==0 = none yet)
        a.raw(b'\x8B\x04\xAD' + le32(seek_best_va))             # eax = best idx+1
        a.raw(b'\x85\xC0'); a.jz('sse_take')
        a.raw(b'\x3B\x0C\xAD' + le32(seek_best_score_va))       # cmp score2, best score
        a.jae('sse_next_team')
        a.label('sse_take')
        a.raw(b'\x8B\x15' + le32(seek_eval_s_va))               # edx = candidate s
        a.raw(b'\x42')                                          # edx = s+1
        a.raw(b'\x89\x14\xAD' + le32(seek_best_va))             # best = s+1
        a.raw(b'\x89\x0C\xAD' + le32(seek_best_score_va))       # best score = score2
        a.jmp('sse_next_team')

        a.label('sse_no_more')
        # Every candidate evaluated: activate the round's best, or drop.
        a.raw(b'\x8B\x04\xAD' + le32(seek_best_va))             # eax = best idx+1
        a.raw(b'\x85\xC0'); a.jz('sse_drop')
        a.raw(b'\x48')                                          # eax = best idx
        a.raw(b'\xA3' + le32(seek_eval_s_va))                   # spill (survives bfs_run)
        # Re-run the winner's BFS (the row currently holds the LAST candidate).
        a.raw(b'\x89\xE8')                                      # eax = team
        a.raw(b'\x69\xC0' + le32(SEEK_ROW))                     # eax *= SEEK_ROW
        a.raw(b'\x05' + le32(seek_dist_va))                     # eax = seek row
        a.raw(b'\xA3' + le32(bfs_disti_va))                     # bfs_disti = row
        a.raw(b'\x89\xC7')                                      # edi = row
        a.raw(b'\xB9' + le32(VMAX))                             # ecx = VMAX
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
        a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
        a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(1))
        a.raw(b'\x8D\x4C\x2D\x00')                              # lea ecx, [ebp+ebp]
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl
        a.raw(b'\x89\x15' + le32(door_mask_i_va))
        a.raw(b'\x01\xD2')
        a.raw(b'\x89\x15' + le32(door_mask_j_va))
        a.raw(b'\xA1' + le32(seek_eval_s_va))                   # eax = winner s
        a.raw(b'\x8B\x04\x85' + le32(switch_node_va))           # eax = its node
        a.raw(b'\xA3' + le32(bfs_start_va))
        a.raw(b'\x89\x2D' + le32(bfr_i_va))                     # spill team
        a.call_lbl('bfs_run')
        a.raw(b'\x8B\x2D' + le32(bfr_i_va))                     # ebp = team
        # ACTIVATE.
        a.raw(b'\xA1' + le32(seek_eval_s_va))                   # eax = winner s
        a.raw(b'\x8B\x0C\x85' + le32(switch_node_va))           # ecx = its node
        a.raw(b'\x89\x0C\xAD' + le32(seek_node_va))             # seek_node[team] = node
        a.raw(b'\x40')                                          # eax = s+1
        a.raw(b'\x89\x04\xAD' + le32(seek_active_va))           # seek_active[team] = s+1
        a.raw(b'\xC7\x04\xAD' + le32(seek_timer_va)
              + le32(cfg.SWITCH_SEEK_TIMEOUT_FRAMES))
        a.label('sse_drop')
        a.raw(b'\xC7\x04\xAD' + le32(seek_pending_va) + le32(0))
        a.raw(b'\xC7\x04\xAD' + le32(seek_tried_va) + le32(0))
        a.raw(b'\xC7\x04\xAD' + le32(seek_best_va) + le32(0))
        a.raw(b'\xC7\x04\xAD' + le32(seek_best_score_va) + le32(0))

        a.label('sse_next_team')
        a.raw(b'\x45')                                          # ++team
        a.raw(b'\x83\xFD\x02')                                  # team < 2?
        a.jb('sse_team_loop')
        a.label('sse_out')
        a.raw(b'\x61')                                          # popad
        a.raw(b'\xC3')

    # =====================================================================
    # Dropped-flag routing. drop_route_refresh (page flip, after the periodic
    # scan) rebuilds a per-drop BFS hop field (drop_dist row 0/1) whenever the
    # drop's bound node changes; drop_next_hop (arrival-time, called INSTEAD
    # of ctf_next_hop while a bot's pursuit latch is set and routing is not
    # suspended) descends that field one neighbour per arrival — real graph
    # pathing to the dropped copy instead of the v1 straight-line steer.
    # Full-field semantics (bfs_skip = 0): closed doors are walked at exactly
    # like pre-door-aware routing; the wedge machinery covers them. Portal
    # relax happens inside bfs_run so dist values cross pads, but the pad
    # next-hop emission is deliberately omitted (a latch needs 350px Euclid
    # proximity or a same-side objective, so cross-arena descents don't
    # arise in practice; the fallback is plain roaming).
    # =====================================================================
    drop_route = (
        door_route
        and cfg.CTF_DROPPED_FLAG_ENABLED
        and layout.has_field('drop_dist')
        and layout.has_field('flag_drop_node')
        and layout.has_field('drop_route_root')
        and layout.has_field('flag_drop_valid')
        and layout.has_field('bot_drop_target')
    )
    if not drop_route:
        a.label('drop_next_hop')
        a.raw(b'\xB8\xFF\xFF\xFF\xFF\xC3')                      # mov eax,-1; ret
        a.label('drop_route_refresh')
        a.raw(b'\xC3')
    else:
        flag_drop_valid_dr_va = layout.va('flag_drop_valid')
        flag_drop_node_dr_va  = layout.va('flag_drop_node')
        drop_route_root_va    = layout.va('drop_route_root')
        drop_dist_va          = layout.va('drop_dist')
        bot_drop_target_dr_va = layout.va('bot_drop_target')

        # -----------------------------------------------------------------
        # drop_next_hop(ECX = current node idx) -> EAX = neighbour descending
        # this bot's latched drop_dist row, or -1 (caller falls back to
        # ctf_next_hop / wp_advance). Inside the movement pushad frame; may
        # clobber any GPR. Clears route_portal_hop (the caller's pad-latch
        # check below the call site must never see a stale ctf value).
        # -----------------------------------------------------------------
        a.label('drop_next_hop')
        a.raw(b'\x89\x0D' + le32(route_cur_va))                 # route_cur = cur
        if portal_route:
            a.raw(b'\xC7\x05' + le32(route_portal_hop_va) + le32(0))
        a.raw(b'\x8B\x15' + le32(bot_slot_va))                  # edx = slot
        a.raw(b'\x8B\x04\x95' + le32(bot_drop_target_dr_va))    # eax = latch (idx+1)
        a.raw(b'\x48')                                          # eax = flag idx
        a.raw(b'\x83\xF8\x02'); a.jae('dnh_fail')               # rows 0/1 only
        a.raw(b'\x3B\x05' + le32(flag_count_va)); a.jae('dnh_fail')
        a.raw(b'\x83\x3C\x85' + le32(flag_drop_valid_dr_va) + b'\x00')
        a.jz('dnh_fail')                                        # drop gone
        a.raw(b'\x8B\x0C\x85' + le32(flag_drop_node_dr_va))     # ecx = drop node
        a.raw(b'\x83\xF9\xFF'); a.jz('dnh_fail')                # unbound
        a.raw(b'\x3B\x0C\x85' + le32(drop_route_root_va))       # row built from it?
        a.jnz('dnh_fail')                                       # stale row
        a.raw(b'\x69\xC0' + le32(VMAX * 4))                     # eax = idx * row stride
        a.raw(b'\x05' + le32(drop_dist_va))                     # + drop_dist base
        a.raw(b'\x89\xC5')                                      # ebp = row
        a.raw(b'\x8B\x1D' + le32(route_cur_va))                 # ebx = cur
        a.raw(b'\x3B\x1D' + le32(vcount_va)); a.jae('dnh_fail') # defensive range
        a.raw(b'\x8B\x4C\x9D\x00')                              # ecx = row[cur]
        a.raw(b'\x83\xF9\xFF'); a.jz('dnh_fail')                # cur unreachable
        a.raw(b'\xBA\xFF\xFF\xFF\xFF')                          # edx = best (-1)
        a.raw(b'\x31\xF6')                                      # esi = 0 (edge idx)
        a.label('dnh_scan')
        a.raw(b'\x3B\x35' + le32(ecount_va))                    # edges done?
        a.jae('dnh_done')
        a.raw(b'\x8B\x04\xB5' + le32(edges_va))                 # eax = edges[esi]
        a.raw(b'\x0F\xB7\xF8')                                  # movzx edi, ax (i)
        a.raw(b'\xC1\xE8\x10')                                  # shr eax, 16   (j)
        a.raw(b'\x39\xDF')                                      # i == cur?
        a.jz('dnh_nb_j')                                        # nb = j (eax)
        a.raw(b'\x39\xD8')                                      # j == cur?
        a.jz('dnh_nb_i')                                        # nb = i (edi)
        a.jmp('dnh_next')
        a.label('dnh_nb_i')
        a.raw(b'\x89\xF8')                                      # eax = edi
        a.label('dnh_nb_j')
        a.raw(b'\x3B\x05' + le32(vcount_va))                    # out of range?
        a.jae('dnh_next')
        a.raw(b'\x8B\x7C\x85\x00')                              # edi = row[nb]
        a.raw(b'\x39\xCF')                                      # nb dist < best?
        a.jae('dnh_next')
        a.raw(b'\x89\xF9')                                      # best_d = edi
        a.raw(b'\x89\xC2')                                      # best = nb
        a.label('dnh_next')
        a.raw(b'\x46')                                          # ++esi
        a.jmp('dnh_scan')
        a.label('dnh_done')
        if portal_route:
            # --- Pad hop on the drop row (mirror of cnh_pp). On Hydro a
            # cross-arena drop descent funnels INTO the pad-entry node: the
            # pad's exit carries row dist - 1, but no WALKABLE neighbour
            # descends from there, so without this pass drop_next_hop
            # returned -1 at the pad node, the random fallback bounced the
            # bot off it, and the next arrival's descent snapped it back —
            # the live-reported "moves between two waypoints only" shuttle
            # (dpursuit snapshot: 0<->25 orbit with failed-edge marker
            # (0,25); offline sim pinned in tests). EBX = cur, EBP = row,
            # ECX = best_d, EDX = best node here — identical shape to cnh.
            a.raw(b'\x31\xF6')                                  # esi = 0 (p)
            a.label('dnh_pp_loop')
            a.raw(b'\x3B\x35' + le32(portal_count_va))          # p >= portal_count?
            a.jae('dnh_pp_done')
            a.raw(b'\x83\xFE' + bytes([cfg.PORTAL_TABLE_MAX]))
            a.jae('dnh_pp_done')
            a.raw(b'\x83\x3C\xB5' + le32(portal_has_dest_va) + b'\x00')
            a.jz('dnh_pp_next')                                 # no directed edge
            if portal_active_va:
                a.raw(b'\x83\x3C\xB5' + le32(portal_active_va) + b'\x00')
                a.jz('dnh_pp_next')                             # pad currently unusable
            a.raw(b'\x8B\x04\xB5' + le32(portal_node_va))       # eax = pad node
            a.raw(b'\x39\xD8')                                  # pad at cur?
            a.jnz('dnh_pp_next')
            a.raw(b'\x8B\x04\xB5' + le32(portal_dest_node_va))  # eax = dest node
            a.raw(b'\x83\xF8\xFF')                              # unbound?
            a.jz('dnh_pp_next')
            a.raw(b'\x3B\x05' + le32(vcount_va))                # defensive range
            a.jae('dnh_pp_next')
            a.raw(b'\x8B\x7C\x85\x00')                          # edi = row[dest node]
            a.raw(b'\x39\xCF')                                  # cmp edi, best_d
            a.jae('dnh_pp_next')                                # not strictly closer
            a.raw(b'\x89\xF9')                                  # best_d = edi
            a.raw(b'\x8D\x46\x01')                              # lea eax, [esi+1]
            a.raw(b'\xA3' + le32(route_portal_hop_va))          # route_portal_hop = p+1
            a.label('dnh_pp_next')
            a.raw(b'\x46')                                      # ++p
            a.jmp('dnh_pp_loop')
            a.label('dnh_pp_done')
            a.raw(b'\x83\x3D' + le32(route_portal_hop_va) + b'\x00')
            a.jz('dnh_node_ret')
            a.raw(b'\x89\xD8')                                  # eax = cur (latch drives movement)
            a.raw(b'\xC3')
            a.label('dnh_node_ret')
        a.raw(b'\x89\xD0')                                      # eax = best (or -1)
        a.raw(b'\xC3')
        a.label('dnh_fail')
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
        a.raw(b'\xC3')

        # -----------------------------------------------------------------
        # drop_route_refresh: rebuild drop_dist rows whose drop node changed.
        # Called from the page flip right after scan_portal_active (the only
        # place flag_drop_node changes mid-match); a no-op when nothing
        # changed. One bfs_run per changed drop — drops move only when a
        # carrier dies, so this almost never runs. pushad/popad.
        # -----------------------------------------------------------------
        a.label('drop_route_refresh')
        a.raw(b'\x60')                                          # pushad
        a.raw(b'\x83\x3D' + le32(vcount_va) + b'\x00')          # graph loaded?
        a.jz('drr_out')
        a.raw(b'\xC7\x05' + le32(bfr_i_va) + le32(0))           # i = 0
        a.label('drr_loop')
        a.raw(b'\xA1' + le32(bfr_i_va))                         # eax = i
        a.raw(b'\x83\xF8\x02'); a.jae('drr_out')                # rows 0/1 only
        a.raw(b'\x3B\x05' + le32(flag_count_va)); a.jae('drr_out')
        a.raw(b'\x83\x3C\x85' + le32(flag_drop_valid_dr_va) + b'\x00')
        a.jnz('drr_has')
        a.raw(b'\xC7\x04\x85' + le32(drop_route_root_va)
              + b'\xFF\xFF\xFF\xFF')                            # no drop -> row invalid
        a.jmp('drr_next')
        a.label('drr_has')
        a.raw(b'\x8B\x0C\x85' + le32(flag_drop_node_dr_va))     # ecx = drop node
        a.raw(b'\x83\xF9\xFF')
        a.jnz('drr_node_ok')
        a.raw(b'\xC7\x04\x85' + le32(drop_route_root_va)
              + b'\xFF\xFF\xFF\xFF')                            # unbound -> row invalid
        a.jmp('drr_next')
        a.label('drr_node_ok')
        a.raw(b'\x3B\x0C\x85' + le32(drop_route_root_va))       # node == root?
        a.jz('drr_next')                                        # row up to date
        # Rebuild row i rooted at the new node (full-field semantics).
        a.raw(b'\x89\x0D' + le32(bfs_start_va))                 # bfs_start = node
        a.raw(b'\x69\xC0' + le32(VMAX * 4))                     # eax = i * row stride
        a.raw(b'\x05' + le32(drop_dist_va))                     # + base
        a.raw(b'\xA3' + le32(bfs_disti_va))                     # bfs_disti = row
        a.raw(b'\x89\xC7')                                      # edi = row
        a.raw(b'\xB9' + le32(VMAX))                             # ecx = VMAX
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
        a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
        a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(0))        # doors NOT gated
        a.call_lbl('bfs_run')
        a.raw(b'\xA1' + le32(bfr_i_va))                         # eax = i
        a.raw(b'\x8B\x0C\x85' + le32(flag_drop_node_dr_va))     # ecx = node (reload)
        a.raw(b'\x89\x0C\x85' + le32(drop_route_root_va))       # root[i] = node
        a.label('drr_next')
        a.raw(b'\xFF\x05' + le32(bfr_i_va))                     # ++i
        a.jmp('drr_loop')
        a.label('drr_out')
        a.raw(b'\x61')                                          # popad
        a.raw(b'\xC3')

    # =====================================================================
    # Salvage King routing. Two per-match fields (built once from
    # detour_df90 when detect_mode()==SK — minerals/bins/graph are all
    # static, so unlike doors there are no mid-match rebuilds):
    #   * sk_ore_dist — MULTI-SOURCE mineral field: every mineral-bearing
    #     node is seeded at distance 0 (bfs_run_seeded), so descending it
    #     always leads to the NEAREST mineral zone; a node at distance 0 IS
    #     a mineral zone and the follower falls back to the random roam
    #     there (the dense clusters are swept by walk-over collection).
    #   * sk_bin_dist — one bfs_run row per authored bin, TEAM-major (the
    #     authored bin Team Number == the SK bot team id), for the RETURN
    #     phase descent to the bot's own deposit bin.
    # sk_next_hop replaces ctf_next_hop at node arrivals in SK matches;
    # sk_update_phase maintains the per-bot COLLECT/RETURN latch from the
    # engine's own carried-mineral count getter (sub_426860 with the keys
    # load_sk resolved — the exact calls the SK stats sync makes).
    # Both fields use full-field door semantics (bfs_skip=0): SK maps are
    # mostly doorless and the wedge/suspension machinery covers the rest.
    # =====================================================================
    sk_route = (
        door_route
        and cfg.SK_ENABLED
        and layout.has_field('sk_routing_active')
        and layout.has_field('sk_ore_dist')
        and layout.has_field('sk_bin_dist')
    )
    if not sk_route:
        a.label('build_sk_routes'); a.raw(b'\xC3')
        a.label('sk_update_phase'); a.raw(b'\xC3')
        a.label('sk_next_hop'); a.raw(b'\xB8\xFF\xFF\xFF\xFF\xC3')
    else:
        sk_active_va       = layout.va('sk_routing_active')
        sk_min_count_va    = layout.va('sk_mineral_count')
        sk_min_node_va     = layout.va('sk_mineral_node')
        sk_ore_dist_va     = layout.va('sk_ore_dist')
        sk_bin_valid_va    = layout.va('sk_bin_valid')
        sk_bin_node_va     = layout.va('sk_bin_node')
        sk_bin_dist_va     = layout.va('sk_bin_dist')
        sk_def_ore_va      = layout.va('sk_def_ore')
        sk_def_crystal_va  = layout.va('sk_def_crystal')
        sk_carry_tmp_va    = layout.va('sk_carry_tmp')
        sk_return_min_va   = layout.va('sk_return_min')
        bot_sk_return_va   = layout.va('bot_sk_return')
        bot_sk_carry_va    = layout.va('bot_sk_carry')

        # -----------------------------------------------------------------
        # build_sk_routes: fill both fields and arm sk_routing_active when
        # at least one mineral node seeded. pushad/popad, no args. Caller
        # (detour_df90) has already verified detect_mode()==SK; load_sk
        # cleared sk_routing_active and bound the nodes.
        # -----------------------------------------------------------------
        a.label('build_sk_routes')
        a.raw(b'\x60')                                          # pushad
        a.raw(b'\x83\x3D' + le32(vcount_va) + b'\x00')          # graph loaded?
        a.jz('bsr_out')
        a.raw(b'\x83\x3D' + le32(sk_min_count_va) + b'\x00')    # any minerals?
        a.jz('bsr_bins')
        # Ore field: clear row + inq, seed every bound mineral node.
        a.raw(b'\xBF' + le32(sk_ore_dist_va))                   # edi = row
        a.raw(b'\xB9' + le32(VMAX))                             # ecx = VMAX
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
        a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
        a.raw(b'\xBF' + le32(bfs_inq_va))                       # edi = bfs_inq
        a.raw(b'\xB9' + le32(VMAX // 4))                        # ecx = VMAX/4 dwords
        a.raw(b'\x31\xC0')                                      # eax = 0
        a.raw(b'\xF3\xAB')                                      # rep stosd
        a.raw(b'\xC7\x05' + le32(bfs_head_va) + le32(0))        # head = 0
        a.raw(b'\xC7\x05' + le32(bfs_tail_va) + le32(0))        # tail = 0
        a.raw(b'\x31\xF6')                                      # esi = 0 (i)
        a.label('bsr_seed_loop')
        a.raw(b'\x3B\x35' + le32(sk_min_count_va))              # i >= mineral count?
        a.jae('bsr_seed_done')
        a.raw(b'\x81\xFE' + le32(cfg.SK_MINERAL_TABLE_MAX))     # i >= cap?
        a.jae('bsr_seed_done')
        a.raw(b'\x8B\x04\xB5' + le32(sk_min_node_va))           # eax = node[i]
        a.raw(b'\x83\xF8\xFF'); a.jz('bsr_seed_next')           # unbound
        a.raw(b'\x3B\x05' + le32(vcount_va)); a.jae('bsr_seed_next')
        a.raw(b'\x80\xB8' + le32(bfs_inq_va) + b'\x00')         # inq[node] set?
        a.jnz('bsr_seed_next')                                  # already seeded
        a.raw(b'\xC6\x80' + le32(bfs_inq_va) + b'\x01')         # inq[node] = 1
        a.raw(b'\xC7\x04\x85' + le32(sk_ore_dist_va) + le32(0)) # row[node] = 0
        a.raw(b'\x8B\x0D' + le32(bfs_tail_va))                  # ecx = tail
        a.raw(b'\x81\xE1' + le32(VMAX - 1))                     # ring index
        a.raw(b'\x89\x04\x8D' + le32(bfs_queue_va))             # queue[tail & mask] = node
        a.raw(b'\xFF\x05' + le32(bfs_tail_va))                  # tail++
        a.label('bsr_seed_next')
        a.raw(b'\x46')                                          # ++i
        a.jmp('bsr_seed_loop')
        a.label('bsr_seed_done')
        a.raw(b'\x83\x3D' + le32(bfs_tail_va) + b'\x00')        # any seeds?
        a.jz('bsr_bins')
        a.raw(b'\xC7\x05' + le32(bfs_disti_va) + le32(sk_ore_dist_va))
        a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(0))        # full-field semantics
        a.call_lbl('bfs_run_seeded')
        a.raw(b'\xC7\x05' + le32(sk_active_va) + le32(1))       # arm SK routing
        a.label('bsr_bins')
        # Bin rows: one single-source bfs_run per valid team slot.
        a.raw(b'\xC7\x05' + le32(bfr_i_va) + le32(0))           # t = 0
        a.label('bsr_bin_loop')
        a.raw(b'\xA1' + le32(bfr_i_va))                         # eax = t
        a.raw(b'\x83\xF8' + bytes([cfg.SK_BIN_TABLE_MAX]))      # t >= 16?
        a.jae('bsr_out')
        a.raw(b'\x83\x3C\x85' + le32(sk_bin_valid_va) + b'\x00')
        a.jz('bsr_bin_next')
        a.raw(b'\x8B\x0C\x85' + le32(sk_bin_node_va))           # ecx = bin node
        a.raw(b'\x83\xF9\xFF'); a.jz('bsr_bin_next')            # unbound
        a.raw(b'\x3B\x0D' + le32(vcount_va)); a.jae('bsr_bin_next')
        a.raw(b'\x89\x0D' + le32(bfs_start_va))                 # bfs_start = node
        a.raw(b'\x69\xC0' + le32(VMAX * 4))                     # eax = t * row stride
        a.raw(b'\x05' + le32(sk_bin_dist_va))                   # + base
        a.raw(b'\xA3' + le32(bfs_disti_va))                     # bfs_disti = row
        a.raw(b'\x89\xC7')                                      # edi = row
        a.raw(b'\xB9' + le32(VMAX))                             # ecx = VMAX
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
        a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
        a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(0))        # full-field semantics
        a.call_lbl('bfs_run')
        a.label('bsr_bin_next')
        a.raw(b'\xFF\x05' + le32(bfr_i_va))                     # ++t
        a.jmp('bsr_bin_loop')
        a.label('bsr_out')
        a.raw(b'\x61')                                          # popad
        a.raw(b'\xC3')

        # -----------------------------------------------------------------
        # sk_update_phase: recompute this bot's carried-mineral count and
        # maintain the COLLECT/RETURN hysteresis latch: count == 0 clears
        # it (deposit done / death), count >= sk_return_min sets it,
        # anything between keeps the current phase. Reads bot_slot_tmp /
        # bot_char_tmp; clobbers GPRs (called inside the movement pushad
        # frame). sub_426860 is __usercall (ECX=char, EDX=def key -> EAX
        # count) and preserves ebx/esi/edi/ebp.
        # -----------------------------------------------------------------
        a.label('sk_update_phase')
        a.raw(b'\xC7\x05' + le32(sk_carry_tmp_va) + le32(0))    # carry = 0
        a.raw(b'\x8B\x0D' + le32(bot_char_va))                  # ecx = bot char
        a.raw(b'\x85\xC9'); a.jz('sup_store')                   # NULL char
        a.raw(b'\x8B\x15' + le32(sk_def_ore_va))                # edx = ore key
        a.raw(b'\x85\xD2'); a.jz('sup_crystal')                 # unresolved
        a.call_va(ax.SUB_426860_VA)                             # eax = ore count
        a.raw(b'\x01\x05' + le32(sk_carry_tmp_va))              # carry += eax
        a.label('sup_crystal')
        a.raw(b'\x8B\x0D' + le32(bot_char_va))                  # ecx = bot char
        a.raw(b'\x8B\x15' + le32(sk_def_crystal_va))            # edx = crystal key
        a.raw(b'\x85\xD2'); a.jz('sup_store')                   # unresolved
        a.call_va(ax.SUB_426860_VA)                             # eax = crystal count
        a.raw(b'\x01\x05' + le32(sk_carry_tmp_va))              # carry += eax
        a.label('sup_store')
        a.raw(b'\x8B\x15' + le32(bot_slot_va))                  # edx = slot
        a.raw(b'\xA1' + le32(sk_carry_tmp_va))                  # eax = carry
        a.raw(b'\x89\x04\x95' + le32(bot_sk_carry_va))          # bot_sk_carry[slot] = carry
        a.raw(b'\x85\xC0'); a.jnz('sup_nonzero')
        a.raw(b'\xC7\x04\x95' + le32(bot_sk_return_va) + le32(0))  # empty -> collect
        a.raw(b'\xC3')
        a.label('sup_nonzero')
        a.raw(b'\x3B\x05' + le32(sk_return_min_va))             # carry >= threshold?
        a.jb('sup_keep')
        a.raw(b'\xC7\x04\x95' + le32(bot_sk_return_va) + le32(1))  # -> return phase
        a.label('sup_keep')
        a.raw(b'\xC3')

        # -----------------------------------------------------------------
        # sk_next_hop(ECX = current node idx) -> EAX = neighbour descending
        # the phase field (mineral field / own-bin row), CUR itself when a
        # portal hop was emitted (route_portal_hop = pad idx+1, exactly the
        # ctf/drop convention), or -1 (caller falls back to the random
        # wp_advance — deliberately reached at mineral zones, dist == 0).
        # Inside the movement pushad frame; may clobber any GPR.
        # -----------------------------------------------------------------
        a.label('sk_next_hop')
        a.raw(b'\x89\x0D' + le32(route_cur_va))                 # route_cur = cur
        if portal_route:
            a.raw(b'\xC7\x05' + le32(route_portal_hop_va) + le32(0))
        a.raw(b'\x83\x3D' + le32(sk_active_va) + b'\x00')       # SK routing armed?
        a.jz('snh_fail')
        a.raw(b'\x8B\x15' + le32(bot_slot_va))                  # edx = slot
        a.raw(b'\x83\x3C\x95' + le32(route_suspend_va) + b'\x00')
        a.jnz('snh_fail')                                       # suspended -> roam
        a.call_lbl('sk_update_phase')                           # refresh phase latch
        a.raw(b'\x8B\x15' + le32(bot_slot_va))                  # edx = slot (reload)
        a.raw(b'\x83\x3C\x95' + le32(bot_sk_return_va) + b'\x00')
        a.jz('snh_collect')
        # RETURN phase: this bot's own-bin row (team-major).
        a.raw(b'\x8B\x0C\x95' + le32(layout.va('bot_team')))    # ecx = bot_team[slot]
        a.raw(b'\x83\xE1' + bytes([cfg.SK_BIN_TABLE_MAX - 1]))  # and ecx, 15 (defensive)
        a.raw(b'\x83\x3C\x8D' + le32(sk_bin_valid_va) + b'\x00')
        a.jz('snh_collect')                                     # no authored bin -> collect
        a.raw(b'\x83\x3C\x8D' + le32(sk_bin_node_va) + b'\xFF')
        a.jz('snh_collect')                                     # unbound -> collect
        a.raw(b'\x69\xC1' + le32(VMAX * 4))                     # eax = team * row stride
        a.raw(b'\x05' + le32(sk_bin_dist_va))                   # + base
        a.jmp('snh_have_row')
        a.label('snh_collect')
        a.raw(b'\xB8' + le32(sk_ore_dist_va))                   # eax = mineral field
        a.label('snh_have_row')
        a.raw(b'\x89\xC5')                                      # ebp = row
        a.raw(b'\x8B\x1D' + le32(route_cur_va))                 # ebx = cur
        a.raw(b'\x3B\x1D' + le32(vcount_va)); a.jae('snh_fail') # defensive range
        a.raw(b'\x8B\x4C\x9D\x00')                              # ecx = row[cur]
        a.raw(b'\x83\xF9\xFF'); a.jz('snh_fail')                # unreachable
        a.raw(b'\x85\xC9'); a.jz('snh_fail')                    # dist 0: at the target zone
        a.raw(b'\xBA\xFF\xFF\xFF\xFF')                          # edx = best (-1)
        a.raw(b'\x31\xF6')                                      # esi = 0 (edge idx)
        a.label('snh_scan')
        a.raw(b'\x3B\x35' + le32(ecount_va))                    # edges done?
        a.jae('snh_done')
        a.raw(b'\x8B\x04\xB5' + le32(edges_va))                 # eax = edges[esi]
        a.raw(b'\x0F\xB7\xF8')                                  # movzx edi, ax (i)
        a.raw(b'\xC1\xE8\x10')                                  # shr eax, 16   (j)
        a.raw(b'\x39\xDF')                                      # i == cur?
        a.jz('snh_nb_j')                                        # nb = j (eax)
        a.raw(b'\x39\xD8')                                      # j == cur?
        a.jz('snh_nb_i')                                        # nb = i (edi)
        a.jmp('snh_next')
        a.label('snh_nb_i')
        a.raw(b'\x89\xF8')                                      # eax = edi
        a.label('snh_nb_j')
        a.raw(b'\x3B\x05' + le32(vcount_va))                    # out of range?
        a.jae('snh_next')
        a.raw(b'\x8B\x7C\x85\x00')                              # edi = row[nb]
        a.raw(b'\x39\xCF')                                      # nb dist < best?
        a.jae('snh_next')
        a.raw(b'\x89\xF9')                                      # best_d = edi
        a.raw(b'\x89\xC2')                                      # best = nb
        a.label('snh_next')
        a.raw(b'\x46')                                          # ++esi
        a.jmp('snh_scan')
        a.label('snh_done')
        if portal_route:
            # Pad hop on the SK row (exact mirror of the ctf/drop passes —
            # Jungle Ruins is an SK map with pads; harmless when no pad
            # carries a destination).
            a.raw(b'\x31\xF6')                                  # esi = 0 (p)
            a.label('snh_pp_loop')
            a.raw(b'\x3B\x35' + le32(portal_count_va))          # p >= portal_count?
            a.jae('snh_pp_done')
            a.raw(b'\x83\xFE' + bytes([cfg.PORTAL_TABLE_MAX]))
            a.jae('snh_pp_done')
            a.raw(b'\x83\x3C\xB5' + le32(portal_has_dest_va) + b'\x00')
            a.jz('snh_pp_next')
            if portal_active_va:
                a.raw(b'\x83\x3C\xB5' + le32(portal_active_va) + b'\x00')
                a.jz('snh_pp_next')
            a.raw(b'\x8B\x04\xB5' + le32(portal_node_va))       # eax = pad node
            a.raw(b'\x39\xD8')                                  # pad at cur?
            a.jnz('snh_pp_next')
            a.raw(b'\x8B\x04\xB5' + le32(portal_dest_node_va))  # eax = dest node
            a.raw(b'\x83\xF8\xFF')                              # unbound?
            a.jz('snh_pp_next')
            a.raw(b'\x3B\x05' + le32(vcount_va))                # defensive range
            a.jae('snh_pp_next')
            a.raw(b'\x8B\x7C\x85\x00')                          # edi = row[dest node]
            a.raw(b'\x39\xCF')                                  # cmp edi, best_d
            a.jae('snh_pp_next')                                # not strictly closer
            a.raw(b'\x89\xF9')                                  # best_d = edi
            a.raw(b'\x8D\x46\x01')                              # lea eax, [esi+1]
            a.raw(b'\xA3' + le32(route_portal_hop_va))          # route_portal_hop = p+1
            a.label('snh_pp_next')
            a.raw(b'\x46')                                      # ++p
            a.jmp('snh_pp_loop')
            a.label('snh_pp_done')
            a.raw(b'\x83\x3D' + le32(route_portal_hop_va) + b'\x00')
            a.jz('snh_node_ret')
            a.raw(b'\x89\xD8')                                  # eax = cur (latch drives movement)
            a.raw(b'\xC3')
            a.label('snh_node_ret')
        a.raw(b'\x89\xD0')                                      # eax = best (or -1)
        a.raw(b'\xC3')
        a.label('snh_fail')
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
        a.raw(b'\xC3')
