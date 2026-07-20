"""Salvage King + goody routing fields: ``build_sk_routes`` (multi-
source mineral field + per-bin rows), ``sk_update_phase``
(COLLECT/RETURN hysteresis), ``sk_next_hop`` (phase/kind row
descent), ``build_item_routes`` (per-category filler fields) and
``sk_pile_route_refresh`` (pile-row rebuild on ring changes)."""

from ... import addresses as ax
from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout, c) -> None:
    route_cur_va = c.route_cur_va
    bfs_queue_va = c.bfs_queue_va
    bfs_head_va = c.bfs_head_va
    bfs_tail_va = c.bfs_tail_va
    bfs_disti_va = c.bfs_disti_va
    bfr_i_va = c.bfr_i_va
    route_suspend_va = c.route_suspend_va
    vcount_va = c.vcount_va
    edges_va = c.edges_va
    ecount_va = c.ecount_va
    bot_slot_va = c.bot_slot_va
    bot_char_va = c.bot_char_va
    VMAX = c.VMAX
    bfs_inq_va = c.bfs_inq_va
    door_route = c.door_route
    bfs_start_va = c.bfs_start_va
    bfs_skip_va = c.bfs_skip_va
    portal_route = c.portal_route
    portal_node_va = c.portal_node_va
    portal_dest_node_va = c.portal_dest_node_va
    portal_has_dest_va = c.portal_has_dest_va
    portal_count_va = c.portal_count_va
    route_portal_hop_va = c.route_portal_hop_va
    portal_active_va = c.portal_active_va

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
        a.label('build_item_routes'); a.raw(b'\xC3')
        a.label('sk_pile_route_refresh'); a.raw(b'\xC3')
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
        sk_return_lo_va    = layout.va('sk_return_lo')
        sk_return_hi_va    = layout.va('sk_return_hi')
        bot_sk_return_va   = layout.va('bot_sk_return')
        bot_sk_carry_va    = layout.va('bot_sk_carry')
        bot_sk_thresh_va   = layout.va('bot_sk_thresh')
        bot_goody_va       = layout.va('bot_pile_target')  # pursuit kind latch
        # Goody-pursuit routing fields (graph-routed piles + filler items).
        goody_fields = (layout.has_field('item_dist')
                        and layout.has_field('item_routing_active'))
        pile_field   = layout.has_field('sk_pile_dist')

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
        # sk_roll_thresh: roll a fresh per-bot RETURN threshold in
        # [sk_return_lo, sk_return_hi] via the engine RNG and store it in
        # bot_sk_thresh[slot]. Floor 1 defensively (0 is the "unrolled"
        # sentinel — a CE-tuned lo of 0 must not wedge the lazy init in a
        # per-think re-roll loop). Reads bot_slot_tmp; clobbers GPRs.
        # -----------------------------------------------------------------
        a.label('sk_roll_thresh')
        a.raw(b'\xFF\x35' + le32(sk_return_hi_va))              # push high
        a.raw(b'\xFF\x35' + le32(sk_return_lo_va))              # push low
        a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                    # ecx = RNG instance
        a.call_va(ax.RNG_SUB)                                   # eax = [lo, hi] (callee pops)
        a.raw(b'\x85\xC0'); a.jnz('srt_ok')
        a.raw(b'\x40')                                          # floor 1
        a.label('srt_ok')
        a.raw(b'\x8B\x15' + le32(bot_slot_va))                  # edx = slot
        a.raw(b'\x89\x04\x95' + le32(bot_sk_thresh_va))         # bot_sk_thresh[slot] = eax
        a.raw(b'\xC3')

        # -----------------------------------------------------------------
        # sk_update_phase: recompute this bot's carried-mineral count and
        # maintain the COLLECT/RETURN hysteresis latch: count == 0 clears
        # it, count >= bot_sk_thresh[slot] sets it, anything between keeps
        # the current phase. The threshold is RANDOMIZED per run: rolled
        # lazily on first use (load_sk zeroes it per match) and RE-ROLLED on
        # the RETURN->empty transition — the frame the deposit banks the
        # load (the deposit press calls this per think), or a death while
        # latched (new life, new plan). Reads bot_slot_tmp / bot_char_tmp;
        # clobbers GPRs (called inside the movement pushad frame).
        # sub_426860 is __usercall (ECX=char, EDX=def key -> EAX count) and
        # preserves ebx/esi/edi/ebp.
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
        # Empty-handed. If the RETURN latch was set, a banked run just
        # completed (or the bot died carrying) — clear it and roll the NEXT
        # run's threshold. Already-collecting bots leave the roll alone.
        a.raw(b'\x83\x3C\x95' + le32(bot_sk_return_va) + b'\x00')
        a.jz('sup_keep')
        a.raw(b'\xC7\x04\x95' + le32(bot_sk_return_va) + le32(0))  # -> collect
        a.call_lbl('sk_roll_thresh')                            # re-roll for next run
        a.raw(b'\xC3')
        a.label('sup_nonzero')
        a.raw(b'\x8B\x0C\x95' + le32(bot_sk_thresh_va))         # ecx = thresh[slot]
        a.raw(b'\x85\xC9'); a.jnz('sup_have_thresh')
        a.call_lbl('sk_roll_thresh')                            # lazy first roll
        a.raw(b'\x8B\x15' + le32(bot_slot_va))                  # edx = slot (reload)
        a.raw(b'\x8B\x0C\x95' + le32(bot_sk_thresh_va))         # ecx = thresh[slot]
        a.raw(b'\x8B\x04\x95' + le32(bot_sk_carry_va))          # eax = carry (reload)
        a.label('sup_have_thresh')
        a.raw(b'\x39\xC8')                                      # cmp eax, ecx
        a.jb('sup_keep')                                        # carry < thresh
        a.raw(b'\xC7\x04\x95' + le32(bot_sk_return_va) + le32(1))  # -> return phase
        a.label('sup_keep')
        a.raw(b'\xC3')

        # -----------------------------------------------------------------
        # sk_next_hop(ECX = current node idx) -> EAX = neighbour descending
        # the active row, CUR itself when a portal hop was emitted
        # (route_portal_hop = pad idx+1, exactly the ctf/drop convention),
        # or -1 (caller falls back to the random wp_advance — deliberately
        # reached at mineral zones and at the target's own node, dist == 0).
        # Row priority: a latched GOODY pursuit (bot_pile_target kind: 1 =
        # pile field, 2+cat = filler-category field — mode-independent)
        # outranks the SK phase logic (mineral field / own-bin row, SK
        # matches only). Inside the movement pushad frame; clobbers GPRs.
        # -----------------------------------------------------------------
        a.label('sk_next_hop')
        a.raw(b'\x89\x0D' + le32(route_cur_va))                 # route_cur = cur
        if portal_route:
            a.raw(b'\xC7\x05' + le32(route_portal_hop_va) + le32(0))
        a.raw(b'\x8B\x15' + le32(bot_slot_va))                  # edx = slot
        a.raw(b'\x83\x3C\x95' + le32(route_suspend_va) + b'\x00')
        a.jnz('snh_fail')                                       # suspended -> roam
        a.raw(b'\x8B\x04\x95' + le32(bot_goody_va))             # eax = pursuit kind
        a.raw(b'\x85\xC0'); a.jz('snh_phase')                   # no pursuit -> phase
        if pile_field:
            a.raw(b'\x83\xF8\x01')                              # kind == 1 (pile)?
            a.jnz('snh_kind_item')
            a.raw(b'\xB8' + le32(layout.va('sk_pile_dist')))    # eax = pile field
            a.jmp('snh_have_row')
            a.label('snh_kind_item')
        if goody_fields:
            a.raw(b'\x83\x3D' + le32(layout.va('item_routing_active')) + b'\x00')
            a.jz('snh_fail')
            a.raw(b'\x83\xE8\x02')                              # eax = kind - 2 (category)
            a.raw(b'\x83\xF8' + bytes([cfg.ITEM_CATEGORIES]))   # cat in range?
            a.jae('snh_fail')
            a.raw(b'\x69\xC0' + le32(VMAX * 4))                 # eax = cat * row stride
            a.raw(b'\x05' + le32(layout.va('item_dist')))       # + base
            a.jmp('snh_have_row')
        else:
            a.jmp('snh_fail')
        a.label('snh_phase')
        a.raw(b'\x83\x3D' + le32(sk_active_va) + b'\x00')       # SK routing armed?
        a.jz('snh_fail')
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

        # -----------------------------------------------------------------
        # build_item_routes: per-match filler-item routing fields — one
        # multi-source bfs_run_seeded row per CATEGORY (health/energy/
        # shield), seeded with every bound anchor of that category at
        # distance 0. Mode-independent (fillers exist in DM/CTF/SK); arms
        # item_routing_active when any category seeded. Fillers respawn in
        # place, so like the mineral field there are no rebuilds. Called
        # from detour_df90 after load_items. pushad/popad, no args.
        #
        # sk_pile_route_refresh: rebuild the pile field (multi-source over
        # the live ring's bound nodes) when sk_pile_dirty is set — pile
        # registration, TTL expiry, or a bot grabbing one. Called from the
        # page flip right after sk_pile_tick; a no-op otherwise.
        # -----------------------------------------------------------------
        if not goody_fields:
            a.label('build_item_routes'); a.raw(b'\xC3')
        else:
            item_dist_va   = layout.va('item_dist')
            item_active_va = layout.va('item_routing_active')
            item_count_va  = layout.va('item_count')
            item_cat_va    = layout.va('item_cat')
            item_node_va   = layout.va('item_node')

            a.label('build_item_routes')
            a.raw(b'\x60')                                          # pushad
            a.raw(b'\x83\x3D' + le32(vcount_va) + b'\x00')          # graph loaded?
            a.jz('bir_out')
            a.raw(b'\x83\x3D' + le32(item_count_va) + b'\x00')      # any fillers?
            a.jz('bir_out')
            a.raw(b'\xC7\x05' + le32(bfr_i_va) + le32(0))           # cat = 0
            a.label('bir_cat_loop')
            a.raw(b'\xA1' + le32(bfr_i_va))                         # eax = cat
            a.raw(b'\x83\xF8' + bytes([cfg.ITEM_CATEGORIES]))       # cats done?
            a.jae('bir_out')
            # Clear this category's row + the SPFA inq flags; seed anchors.
            a.raw(b'\x69\xF8' + le32(VMAX * 4))                     # edi = cat * stride
            a.raw(b'\x81\xC7' + le32(item_dist_va))                 # + base
            a.raw(b'\x89\x3D' + le32(bfs_disti_va))                 # bfs_disti = row
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
            a.label('bir_seed_loop')
            a.raw(b'\x3B\x35' + le32(item_count_va))                # i >= item count?
            a.jae('bir_seed_done')
            a.raw(b'\x83\xFE' + bytes([cfg.ITEM_TABLE_MAX]))        # i >= cap?
            a.jae('bir_seed_done')
            a.raw(b'\x8B\x04\xB5' + le32(item_cat_va))              # eax = item_cat[i]
            a.raw(b'\x3B\x05' + le32(bfr_i_va))                     # == this category?
            a.jnz('bir_seed_next')
            a.raw(b'\x8B\x04\xB5' + le32(item_node_va))             # eax = node[i]
            a.raw(b'\x83\xF8\xFF'); a.jz('bir_seed_next')           # unbound
            a.raw(b'\x3B\x05' + le32(vcount_va)); a.jae('bir_seed_next')
            a.raw(b'\x80\xB8' + le32(bfs_inq_va) + b'\x00')         # inq[node] set?
            a.jnz('bir_seed_next')                                  # already seeded
            a.raw(b'\xC6\x80' + le32(bfs_inq_va) + b'\x01')         # inq[node] = 1
            a.raw(b'\x8B\x0D' + le32(bfs_disti_va))                 # ecx = row
            a.raw(b'\xC7\x04\x81' + le32(0))                        # row[node] = 0
            a.raw(b'\x8B\x0D' + le32(bfs_tail_va))                  # ecx = tail
            a.raw(b'\x81\xE1' + le32(VMAX - 1))                     # ring index
            a.raw(b'\x89\x04\x8D' + le32(bfs_queue_va))             # queue[tail & mask]
            a.raw(b'\xFF\x05' + le32(bfs_tail_va))                  # tail++
            a.label('bir_seed_next')
            a.raw(b'\x46')                                          # ++i
            a.jmp('bir_seed_loop')
            a.label('bir_seed_done')
            a.raw(b'\x83\x3D' + le32(bfs_tail_va) + b'\x00')        # any seeds?
            a.jz('bir_cat_next')
            a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(0))        # full-field semantics
            a.call_lbl('bfs_run_seeded')
            a.raw(b'\xC7\x05' + le32(item_active_va) + le32(1))     # arm item routing
            a.label('bir_cat_next')
            a.raw(b'\xFF\x05' + le32(bfr_i_va))                     # ++cat
            a.jmp('bir_cat_loop')
            a.label('bir_out')
            a.raw(b'\x61')                                          # popad
            a.raw(b'\xC3')

        if not (pile_field and layout.has_field('sk_pile_dirty')
                and layout.has_field('sk_pile_node')):
            a.label('sk_pile_route_refresh'); a.raw(b'\xC3')
        else:
            pile_dirty_va = layout.va('sk_pile_dirty')
            pile_node_va  = layout.va('sk_pile_node')
            pile_valid_fr_va = layout.va('sk_pile_valid')

            a.label('sk_pile_route_refresh')
            a.raw(b'\x60')                                          # pushad
            a.raw(b'\x83\x3D' + le32(pile_dirty_va) + b'\x00')      # anything changed?
            a.jz('spr_out')
            a.raw(b'\xC7\x05' + le32(pile_dirty_va) + le32(0))
            # Clear + reseed even with zero live piles (row goes all -1, so
            # a stale latch simply stops descending).
            a.raw(b'\xBF' + le32(layout.va('sk_pile_dist')))        # edi = row
            a.raw(b'\xC7\x05' + le32(bfs_disti_va)
                  + le32(layout.va('sk_pile_dist')))                # bfs_disti = row
            a.raw(b'\xB9' + le32(VMAX))                             # ecx = VMAX
            a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
            a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
            a.raw(b'\x83\x3D' + le32(vcount_va) + b'\x00')          # graph loaded?
            a.jz('spr_out')
            a.raw(b'\xBF' + le32(bfs_inq_va))                       # edi = bfs_inq
            a.raw(b'\xB9' + le32(VMAX // 4))                        # ecx = VMAX/4 dwords
            a.raw(b'\x31\xC0')                                      # eax = 0
            a.raw(b'\xF3\xAB')                                      # rep stosd
            a.raw(b'\xC7\x05' + le32(bfs_head_va) + le32(0))        # head = 0
            a.raw(b'\xC7\x05' + le32(bfs_tail_va) + le32(0))        # tail = 0
            a.raw(b'\x31\xF6')                                      # esi = 0 (p)
            a.label('spr_seed_loop')
            a.raw(b'\x83\xFE' + bytes([cfg.SK_PILE_TABLE_MAX]))     # ring done?
            a.jae('spr_seed_done')
            a.raw(b'\x83\x3C\xB5' + le32(pile_valid_fr_va) + b'\x00')  # ttl > 0?
            a.jz('spr_seed_next')
            a.raw(b'\x8B\x04\xB5' + le32(pile_node_va))             # eax = node[p]
            a.raw(b'\x83\xF8\xFF'); a.jz('spr_seed_next')           # unbound
            a.raw(b'\x3B\x05' + le32(vcount_va)); a.jae('spr_seed_next')
            a.raw(b'\x80\xB8' + le32(bfs_inq_va) + b'\x00')         # inq[node] set?
            a.jnz('spr_seed_next')
            a.raw(b'\xC6\x80' + le32(bfs_inq_va) + b'\x01')         # inq[node] = 1
            a.raw(b'\xC7\x04\x85' + le32(layout.va('sk_pile_dist')) + le32(0))
            a.raw(b'\x8B\x0D' + le32(bfs_tail_va))                  # ecx = tail
            a.raw(b'\x81\xE1' + le32(VMAX - 1))                     # ring index
            a.raw(b'\x89\x04\x8D' + le32(bfs_queue_va))             # queue[tail & mask]
            a.raw(b'\xFF\x05' + le32(bfs_tail_va))                  # tail++
            a.label('spr_seed_next')
            a.raw(b'\x46')                                          # ++p
            a.jmp('spr_seed_loop')
            a.label('spr_seed_done')
            a.raw(b'\x83\x3D' + le32(bfs_tail_va) + b'\x00')        # any seeds?
            a.jz('spr_out')
            a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(0))        # full-field semantics
            a.call_lbl('bfs_run_seeded')
            a.label('spr_out')
            a.raw(b'\x61')                                          # popad
            a.raw(b'\xC3')
