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
        a.label('rebuild_open_routes'); a.raw(b'\xC3')
        a.label('ctf_pick_goal'); a.raw(b'\xC3')
        a.label('ctf_next_hop'); a.raw(b'\xB8\xFF\xFF\xFF\xFF\xC3')  # mov eax,-1; ret
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
    # No graph? leave INF and bail.
    a.raw(b'\xA1' + le32(vcount_va))                           # eax = vertex_count
    a.raw(b'\x85\xC0'); a.jz('bfr_done')
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

    if door_route:
        # =================================================================
        # bfs_run: one BFS pass. Inputs (scratch): bfs_start (seed node),
        # bfs_disti (distance-row base, pre-cleared to -1), bfs_skip (1 =
        # skip edges crossing currently-blocked doors). Clobbers GPRs.
        # =================================================================
        a.label('bfs_run')
        a.raw(b'\x8B\x1D' + le32(bfs_start_va))              # ebx = start node
        a.raw(b'\x8B\x0D' + le32(bfs_disti_va))              # ecx = disti
        a.raw(b'\xC7\x04\x99\x00\x00\x00\x00')               # disti[start] = 0
        a.raw(b'\x89\x1D' + le32(bfs_queue_va))              # queue[0] = start
        a.raw(b'\xC7\x05' + le32(bfs_head_va) + le32(0))     # head = 0
        a.raw(b'\xC7\x05' + le32(bfs_tail_va) + le32(1))     # tail = 1

        a.label('bfsr_loop')
        a.raw(b'\xA1' + le32(bfs_head_va))                    # eax = head
        a.raw(b'\x3B\x05' + le32(bfs_tail_va))               # cmp eax, tail
        a.jae('bfsr_done')
        a.raw(b'\x8B\x0C\x85' + le32(bfs_queue_va))         # ecx = queue[head]
        a.raw(b'\x89\x0D' + le32(bfs_u_va))                  # bfs_u = u
        a.raw(b'\xFF\x05' + le32(bfs_head_va))              # head++
        a.raw(b'\x8B\x15' + le32(bfs_disti_va))             # edx = disti
        a.raw(b'\x8B\x04\x8A')                               # eax = disti[u] (du)
        a.raw(b'\x89\x05' + le32(bfs_du_va))                # bfs_du = du
        a.raw(b'\x31\xF6')                                   # esi = 0 (edge idx)

        a.label('bfsr_edge_loop')
        a.raw(b'\x3B\x35' + le32(ecount_va))                # cmp esi, edge_count
        a.jae('bfsr_loop')                                  # edges done -> next BFS node
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
        # edge FROM node i: the closed door must be openable from i by the
        # field's team (door_mask_i = 1 << team*2, set by the caller).
        a.raw(b'\x89\xD8')                                  # eax = ebx (v = i)
        a.raw(b'\x83\x3D' + le32(cnh_blk_va) + b'\x00')
        a.jz('bfsr_pass_ok')
        a.raw(b'\x0F\xB6\x14\x35' + le32(edge_pass_va))     # movzx edx, edge_pass[e]
        a.raw(b'\x85\x15' + le32(door_mask_i_va))           # test edx, [door_mask_i]
        a.jz('bfsr_edge_next')
        a.jmp('bfsr_pass_ok')
        a.label('bfsr_edge_v_j')                           # v = j: bot enters from j
        a.raw(b'\x83\x3D' + le32(cnh_blk_va) + b'\x00')
        a.jz('bfsr_pass_ok')
        a.raw(b'\x0F\xB6\x14\x35' + le32(edge_pass_va))     # movzx edx, edge_pass[e]
        a.raw(b'\x85\x15' + le32(door_mask_j_va))           # test edx, [door_mask_j]
        a.jz('bfsr_edge_next')
        a.label('bfsr_pass_ok')
        a.raw(b'\x3B\x05' + le32(vcount_va))               # cmp eax, vertex_count
        a.jae('bfsr_edge_next')                            # out of range
        a.raw(b'\x8B\x15' + le32(bfs_disti_va))           # edx = disti
        a.raw(b'\x8B\x0C\x82')                             # ecx = disti[v]
        a.raw(b'\x83\xF9\xFF')                             # visited?
        a.jnz('bfsr_edge_next')
        a.raw(b'\x8B\x0D' + le32(bfs_du_va))              # ecx = du
        a.raw(b'\x41')                                     # inc ecx (du+1)
        a.raw(b'\x89\x0C\x82')                            # disti[v] = du+1
        a.raw(b'\x8B\x0D' + le32(bfs_tail_va))           # ecx = tail
        a.raw(b'\x89\x04\x8D' + le32(bfs_queue_va))      # queue[tail] = v (eax)
        a.raw(b'\xFF\x05' + le32(bfs_tail_va))           # tail++
        a.label('bfsr_edge_next')
        a.raw(b'\x46')                                    # inc esi
        a.jmp('bfsr_edge_loop')

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
    a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00') # cmp [flag_routing_active], 0
    a.jz('cnh_fail')
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
        a.raw(b'\x83\xF9\xFF')                            # reachable for this team?
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
        a.jz('cnh_pass_ok')
        a.raw(b'\x0F\xB6\x3C\x35' + le32(edge_pass_va)) # movzx edi, edge_pass[e]
        a.raw(b'\x85\x3D' + le32(door_mask_j_va))       # openable from j (cur side), this team?
        a.jz('cnh_scan_next')
        a.jmp('cnh_pass_ok')
    a.label('cnh_nb_j')                                # nb in eax
    if door_route:
        a.raw(b'\x83\x3D' + le32(cnh_blk_va) + b'\x00')
        a.jz('cnh_pass_ok')
        a.raw(b'\x0F\xB6\x3C\x35' + le32(edge_pass_va)) # movzx edi, edge_pass[e]
        a.raw(b'\x85\x3D' + le32(door_mask_i_va))       # openable from i (cur side), this team?
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
    a.raw(b'\x89\xD0')                                  # eax = edx (best, or -1)
    a.raw(b'\xC3')

    a.label('cnh_fail')
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                      # eax = -1
    a.raw(b'\xC3')
