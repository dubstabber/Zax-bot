"""Per-match route-field builders: ``build_flag_routes`` (per-base BFS
roots + full/open fields), ``build_edge_lens`` (quantized physical
edge lengths), the shared ``bfs_run`` / ``bfs_run_seeded`` SPFA body
and ``rebuild_open_routes`` (door-state rebuild + route epoch bump)."""

from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout, c) -> None:
    route_node_va = c.route_node_va
    flag_dist_va = c.flag_dist_va
    bfs_queue_va = c.bfs_queue_va
    bfs_head_va = c.bfs_head_va
    bfs_tail_va = c.bfs_tail_va
    bfs_u_va = c.bfs_u_va
    bfs_du_va = c.bfs_du_va
    bfs_disti_va = c.bfs_disti_va
    bfr_i_va = c.bfr_i_va
    flag_table_va = c.flag_table_va
    flag_count_va = c.flag_count_va
    verts_va = c.verts_va
    vcount_va = c.vcount_va
    edges_va = c.edges_va
    ecount_va = c.ecount_va
    wp_scratch_va = c.wp_scratch_va
    VMAX = c.VMAX
    RMAX = c.RMAX
    ROW = c.ROW
    weighted = c.weighted
    edge_len_va = c.edge_len_va
    bfs_inq_va = c.bfs_inq_va
    elen_quantum_va = c.elen_quantum_va
    door_route = c.door_route
    flag_dist_open_va = c.flag_dist_open_va
    edge_door_va = c.edge_door_va
    edge_pass_va = c.edge_pass_va
    cnh_blk_va = c.cnh_blk_va
    door_mask_i_va = c.door_mask_i_va
    door_mask_j_va = c.door_mask_j_va
    door_blocked_va = c.door_blocked_va
    door_count_va = c.door_count_va
    bfs_start_va = c.bfs_start_va
    bfs_skip_va = c.bfs_skip_va
    seek = c.seek
    portal_route = c.portal_route
    portal_node_va = c.portal_node_va
    portal_dest_node_va = c.portal_dest_node_va
    portal_has_dest_va = c.portal_has_dest_va
    portal_count_va = c.portal_count_va
    switch_node_va = c.switch_node_va
    switch_table_va = c.switch_table_va
    switch_count_va = c.switch_count_va
    seek_active_va = c.seek_active_va
    seek_dist_va = c.seek_dist_va
    bot_seek_va = c.bot_seek_va

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
    if c.defender:
        # defend_radius[*] = 0 (recomputed per routed base at bfr_next).
        a.raw(b'\xBF' + le32(c.defend_radius_va))              # edi = defend_radius
        a.raw(b'\xB9' + le32(RMAX))                            # ecx = RMAX
        a.raw(b'\x31\xC0')                                     # eax = 0
        a.raw(b'\xF3\xAB')                                     # rep stosd
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
    if c.defender:
        # --- Per-base DEFENDER radius from the map's span. The full field
        # for base i was just built; its max FINITE distance is how far the
        # map extends from this base (quanta units), so the patrol radius
        # scales with map size: defend_radius[i] = max(MIN,
        # max_finite * PCT / 100). A skipped base (no node) leaves an
        # all-INF row -> max 0 -> the MIN clamp, harmlessly unused (its
        # routing is inert without a route node).
        a.raw(b'\xA1' + le32(bfr_i_va))                  # eax = i
        a.raw(b'\x69\xC0' + le32(ROW))                   # imul eax, eax, ROW
        a.raw(b'\x05' + le32(flag_dist_va))              # eax = row base
        a.raw(b'\x31\xD2')                               # edx = 0 (max finite)
        a.raw(b'\x31\xF6')                               # esi = 0 (n)
        a.label('bfr_dr_scan')
        a.raw(b'\x3B\x35' + le32(vcount_va))             # n >= vertex_count?
        a.jae('bfr_dr_scanned')
        a.raw(b'\x8B\x0C\xB0')                           # ecx = [eax + esi*4]
        a.raw(b'\x83\xF9\xFF')                           # unreachable?
        a.jz('bfr_dr_next')
        a.raw(b'\x39\xD1')                               # cmp ecx, edx
        a.jbe('bfr_dr_next')
        a.raw(b'\x89\xCA')                               # edx = ecx (new max)
        a.label('bfr_dr_next')
        a.raw(b'\x46')                                   # ++n
        a.jmp('bfr_dr_scan')
        a.label('bfr_dr_scanned')
        a.raw(b'\x69\xC2' + le32(cfg.CTF_DEFEND_RADIUS_PCT))  # eax = max * PCT
        a.raw(b'\xB9\x64\x00\x00\x00')                   # ecx = 100
        a.raw(b'\x31\xD2')                               # edx = 0
        a.raw(b'\xF7\xF1')                               # div ecx (eax /= 100)
        a.raw(b'\x83\xF8' + bytes([cfg.CTF_DEFEND_RADIUS_MIN]))
        a.jae('bfr_dr_min_ok')
        a.raw(b'\xB8' + le32(cfg.CTF_DEFEND_RADIUS_MIN)) # clamp up to MIN
        a.label('bfr_dr_min_ok')
        a.raw(b'\x8B\x0D' + le32(bfr_i_va))              # ecx = i
        a.raw(b'\x89\x04\x8D' + le32(c.defend_radius_va))  # defend_radius[i] = eax
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

