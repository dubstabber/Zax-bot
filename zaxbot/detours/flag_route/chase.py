"""Enemy-carrier chase routing: ``chase_route_refresh`` (page-flip TTL
tick + carrier node bind + per-flag BFS row rebuild on node change) and
``chase_next_hop`` (arrival-time descent with pad-hop emission).

Mirror of the dropped-flag machinery (``drop.py``) with a MOVING target:
the carrier's position is stamped by any bot's LOS sighting
(``bot_perception.py``), the page flip binds it to its nearest graph node
every frame while the intel TTL runs, and the row is rebuilt only when
that node CHANGES (one bounded bfs_run per change; a walking carrier
crosses nodes every second or two). Full-field semantics (bfs_skip = 0)
like the drop rows; the wedge machinery covers closed doors."""

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
    wp_scratch_va = c.wp_scratch_va
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

    chase_route = (
        door_route
        and cfg.CTF_CHASE_ENABLED
        and layout.has_field('chase_dist')
        and layout.has_field('chase_pos')
        and layout.has_field('chase_node')
        and layout.has_field('chase_ttl')
        and layout.has_field('chase_root')
        and layout.has_field('bot_chase_flag')
    )
    if not chase_route:
        a.label('chase_next_hop')
        a.raw(b'\xB8\xFF\xFF\xFF\xFF\xC3')                      # mov eax,-1; ret
        a.label('chase_route_refresh')
        a.raw(b'\xC3')
        return

    chase_pos_va  = layout.va('chase_pos')
    chase_node_va = layout.va('chase_node')
    chase_ttl_va  = layout.va('chase_ttl')
    chase_root_va = layout.va('chase_root')
    chase_dist_va = layout.va('chase_dist')
    bot_chase_flag_va = layout.va('bot_chase_flag')

    # -----------------------------------------------------------------
    # chase_next_hop(ECX = current node idx) -> EAX = neighbour descending
    # this bot's latched chase_dist row, or -1 (caller falls back to
    # ctf_next_hop / wp_advance). Inside the movement pushad frame; may
    # clobber any GPR. Clears route_portal_hop like drop_next_hop.
    # -----------------------------------------------------------------
    a.label('chase_next_hop')
    a.raw(b'\x89\x0D' + le32(route_cur_va))                 # route_cur = cur
    if portal_route:
        a.raw(b'\xC7\x05' + le32(route_portal_hop_va) + le32(0))
    a.raw(b'\x8B\x15' + le32(bot_slot_va))                  # edx = slot
    a.raw(b'\x8B\x04\x95' + le32(bot_chase_flag_va))        # eax = latch (idx+1)
    a.raw(b'\x48')                                          # eax = flag idx
    a.raw(b'\x83\xF8\x02'); a.jae('chnh_fail')              # rows 0/1 only
    a.raw(b'\x3B\x05' + le32(flag_count_va)); a.jae('chnh_fail')
    a.raw(b'\x83\x3C\x85' + le32(chase_ttl_va) + b'\x00')
    a.jz('chnh_fail')                                       # intel expired
    a.raw(b'\x8B\x0C\x85' + le32(chase_node_va))            # ecx = carrier node
    a.raw(b'\x83\xF9\xFF'); a.jz('chnh_fail')               # unbound
    a.raw(b'\x3B\x0C\x85' + le32(chase_root_va))            # row built from it?
    a.jnz('chnh_fail')                                      # stale row
    a.raw(b'\x69\xC0' + le32(VMAX * 4))                     # eax = idx * row stride
    a.raw(b'\x05' + le32(chase_dist_va))                    # + chase_dist base
    a.raw(b'\x89\xC5')                                      # ebp = row
    a.raw(b'\x8B\x1D' + le32(route_cur_va))                 # ebx = cur
    a.raw(b'\x3B\x1D' + le32(vcount_va)); a.jae('chnh_fail')  # defensive range
    a.raw(b'\x8B\x4C\x9D\x00')                              # ecx = row[cur]
    a.raw(b'\x83\xF9\xFF'); a.jz('chnh_fail')               # cur unreachable
    a.raw(b'\xBA\xFF\xFF\xFF\xFF')                          # edx = best (-1)
    a.raw(b'\x31\xF6')                                      # esi = 0 (edge idx)
    a.label('chnh_scan')
    a.raw(b'\x3B\x35' + le32(ecount_va))                    # edges done?
    a.jae('chnh_done')
    a.raw(b'\x8B\x04\xB5' + le32(edges_va))                 # eax = edges[esi]
    a.raw(b'\x0F\xB7\xF8')                                  # movzx edi, ax (i)
    a.raw(b'\xC1\xE8\x10')                                  # shr eax, 16   (j)
    a.raw(b'\x39\xDF')                                      # i == cur?
    a.jz('chnh_nb_j')                                       # nb = j (eax)
    a.raw(b'\x39\xD8')                                      # j == cur?
    a.jz('chnh_nb_i')                                       # nb = i (edi)
    a.jmp('chnh_next')
    a.label('chnh_nb_i')
    a.raw(b'\x89\xF8')                                      # eax = edi
    a.label('chnh_nb_j')
    a.raw(b'\x3B\x05' + le32(vcount_va))                    # out of range?
    a.jae('chnh_next')
    a.raw(b'\x8B\x7C\x85\x00')                              # edi = row[nb]
    a.raw(b'\x39\xCF')                                      # nb dist < best?
    a.jae('chnh_next')
    a.raw(b'\x89\xF9')                                      # best_d = edi
    a.raw(b'\x89\xC2')                                      # best = nb
    a.label('chnh_next')
    a.raw(b'\x46')                                          # ++esi
    a.jmp('chnh_scan')
    a.label('chnh_done')
    if portal_route:
        # Pad hop on the chase row (mirror of dnh_pp): a cross-arena chase
        # descent funnels into the pad-entry node exactly like the drop
        # descent did on Hydro — without the hop the descent dead-ends
        # there and shuttles. EBX = cur, EBP = row, ECX = best_d,
        # EDX = best node here — identical shape to dnh.
        a.raw(b'\x31\xF6')                                  # esi = 0 (p)
        a.label('chnh_pp_loop')
        a.raw(b'\x3B\x35' + le32(portal_count_va))          # p >= portal_count?
        a.jae('chnh_pp_done')
        a.raw(b'\x83\xFE' + bytes([cfg.PORTAL_TABLE_MAX]))
        a.jae('chnh_pp_done')
        a.raw(b'\x83\x3C\xB5' + le32(portal_has_dest_va) + b'\x00')
        a.jz('chnh_pp_next')                                # no directed edge
        if portal_active_va:
            a.raw(b'\x83\x3C\xB5' + le32(portal_active_va) + b'\x00')
            a.jz('chnh_pp_next')                            # pad currently unusable
        a.raw(b'\x8B\x04\xB5' + le32(portal_node_va))       # eax = pad node
        a.raw(b'\x39\xD8')                                  # pad at cur?
        a.jnz('chnh_pp_next')
        a.raw(b'\x8B\x04\xB5' + le32(portal_dest_node_va))  # eax = dest node
        a.raw(b'\x83\xF8\xFF')                              # unbound?
        a.jz('chnh_pp_next')
        a.raw(b'\x3B\x05' + le32(vcount_va))                # defensive range
        a.jae('chnh_pp_next')
        a.raw(b'\x8B\x7C\x85\x00')                          # edi = row[dest node]
        a.raw(b'\x39\xCF')                                  # cmp edi, best_d
        a.jae('chnh_pp_next')                               # not strictly closer
        a.raw(b'\x89\xF9')                                  # best_d = edi
        a.raw(b'\x8D\x46\x01')                              # lea eax, [esi+1]
        a.raw(b'\xA3' + le32(route_portal_hop_va))          # route_portal_hop = p+1
        a.label('chnh_pp_next')
        a.raw(b'\x46')                                      # ++p
        a.jmp('chnh_pp_loop')
        a.label('chnh_pp_done')
        a.raw(b'\x83\x3D' + le32(route_portal_hop_va) + b'\x00')
        a.jz('chnh_node_ret')
        a.raw(b'\x89\xD8')                                  # eax = cur (latch drives movement)
        a.raw(b'\xC3')
        a.label('chnh_node_ret')
    a.raw(b'\x89\xD0')                                      # eax = best (or -1)
    a.raw(b'\xC3')
    a.label('chnh_fail')
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
    a.raw(b'\xC3')

    # -----------------------------------------------------------------
    # chase_route_refresh: page-flip service — per flag row (0/1): tick the
    # sighting TTL, bind chase_pos to its nearest graph node while the
    # intel is live, and rebuild the row (one bfs_run) only when that node
    # changed. Expired/unbindable intel invalidates node + root so
    # chase_next_hop cleanly returns -1. pushad/popad.
    # -----------------------------------------------------------------
    a.label('chase_route_refresh')
    a.raw(b'\x60')                                          # pushad
    a.raw(b'\x83\x3D' + le32(vcount_va) + b'\x00')          # graph loaded?
    a.jz('chrr_out')
    a.raw(b'\xC7\x05' + le32(bfr_i_va) + le32(0))           # i = 0
    a.label('chrr_loop')
    a.raw(b'\xA1' + le32(bfr_i_va))                         # eax = i
    a.raw(b'\x83\xF8\x02'); a.jae('chrr_out')                # rows 0/1 only
    a.raw(b'\x3B\x05' + le32(flag_count_va)); a.jae('chrr_out')
    # TTL tick (this is the ONE per-frame decrement — sightings refresh it).
    a.raw(b'\x8B\x0C\x85' + le32(chase_ttl_va))             # ecx = ttl[i]
    a.raw(b'\x85\xC9'); a.jz('chrr_dead')
    a.raw(b'\x49')                                          # dec ecx
    a.raw(b'\x89\x0C\x85' + le32(chase_ttl_va))
    a.raw(b'\x85\xC9'); a.jnz('chrr_alive')
    a.label('chrr_dead')
    a.raw(b'\xC7\x04\x85' + le32(chase_node_va) + b'\xFF\xFF\xFF\xFF')
    a.raw(b'\xC7\x04\x85' + le32(chase_root_va) + b'\xFF\xFF\xFF\xFF')
    a.jmp('chrr_next')
    a.label('chrr_alive')
    # Bind the carrier's CURRENT position to its nearest graph node.
    a.raw(b'\x8B\x0C\xC5' + le32(chase_pos_va))             # ecx = pos.x [i*8]
    a.raw(b'\x89\x0D' + le32(wp_scratch_va))
    a.raw(b'\x8B\x0C\xC5' + le32(chase_pos_va + 4))         # ecx = pos.y
    a.raw(b'\x89\x0D' + le32(wp_scratch_va + 4))
    a.call_lbl('wp_find_nearest')                           # ebx = nearest or -1
    a.raw(b'\xA1' + le32(bfr_i_va))                         # eax = i (reload)
    a.raw(b'\x83\xFB\xFF')                                  # unbindable?
    a.jnz('chrr_bound')
    a.raw(b'\xC7\x04\x85' + le32(chase_node_va) + b'\xFF\xFF\xFF\xFF')
    a.raw(b'\xC7\x04\x85' + le32(chase_root_va) + b'\xFF\xFF\xFF\xFF')
    a.jmp('chrr_next')
    a.label('chrr_bound')
    a.raw(b'\x89\x1C\x85' + le32(chase_node_va))            # chase_node[i] = ebx
    a.raw(b'\x3B\x1C\x85' + le32(chase_root_va))            # node == root?
    a.jz('chrr_next')                                        # row up to date
    # Rebuild row i rooted at the new node (full-field semantics).
    a.raw(b'\x89\x1D' + le32(bfs_start_va))                 # bfs_start = node
    a.raw(b'\x69\xC0' + le32(VMAX * 4))                     # eax = i * row stride
    a.raw(b'\x05' + le32(chase_dist_va))                    # + base
    a.raw(b'\xA3' + le32(bfs_disti_va))                     # bfs_disti = row
    a.raw(b'\x89\xC7')                                      # edi = row
    a.raw(b'\xB9' + le32(VMAX))                             # ecx = VMAX
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
    a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
    a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(0))        # doors NOT gated
    a.call_lbl('bfs_run')
    a.raw(b'\xA1' + le32(bfr_i_va))                         # eax = i
    a.raw(b'\x8B\x1C\x85' + le32(chase_node_va))            # ebx = node (reload)
    a.raw(b'\x89\x1C\x85' + le32(chase_root_va))            # root[i] = node
    a.label('chrr_next')
    a.raw(b'\xFF\x05' + le32(bfr_i_va))                     # ++i
    a.jmp('chrr_loop')
    a.label('chrr_out')
    a.raw(b'\x61')                                          # popad
    a.raw(b'\xC3')
