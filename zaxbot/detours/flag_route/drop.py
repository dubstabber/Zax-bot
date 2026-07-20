"""Dropped-flag routing: ``drop_route_refresh`` (per-drop BFS row
rebuild on node change) + ``drop_next_hop`` (arrival-time descent
with pad-hop emission)."""

from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout, c) -> None:
    route_cur_va = c.route_cur_va
    bfs_disti_va = c.bfs_disti_va
    bfr_i_va = c.bfr_i_va
    flag_count_va = c.flag_count_va
    vcount_va = c.vcount_va
    edges_va = c.edges_va
    ecount_va = c.ecount_va
    bot_slot_va = c.bot_slot_va
    VMAX = c.VMAX
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

