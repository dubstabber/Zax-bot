"""``ctf_next_hop`` — arrival-time goal-biased next-hop descent over
the active distance field (open/full selection, directional door
gate, switch-seek request, portal pad hop)."""

from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout, c) -> None:
    routing_active_va = c.routing_active_va
    route_cur_va = c.route_cur_va
    route_goal_va = c.route_goal_va
    flag_dist_va = c.flag_dist_va
    vcount_va = c.vcount_va
    edges_va = c.edges_va
    ecount_va = c.ecount_va
    bot_slot_va = c.bot_slot_va
    bot_team_va = c.bot_team_va
    ROW = c.ROW
    door_route = c.door_route
    flag_dist_open_va = c.flag_dist_open_va
    edge_door_va = c.edge_door_va
    edge_pass_va = c.edge_pass_va
    cnh_blk_va = c.cnh_blk_va
    door_mask_i_va = c.door_mask_i_va
    door_mask_j_va = c.door_mask_j_va
    door_blocked_va = c.door_blocked_va
    door_count_va = c.door_count_va
    route_use_open_va = c.route_use_open_va
    TEAM_ROW = c.TEAM_ROW
    seek = c.seek
    portal_route = c.portal_route
    portal_node_va = c.portal_node_va
    portal_dest_node_va = c.portal_dest_node_va
    portal_has_dest_va = c.portal_has_dest_va
    portal_count_va = c.portal_count_va
    route_portal_hop_va = c.route_portal_hop_va
    portal_active_va = c.portal_active_va
    seek_active_va = c.seek_active_va
    seek_node_va = c.seek_node_va
    seek_pending_va = c.seek_pending_va
    seek_req_node_va = c.seek_req_node_va
    seek_req_goal_va = c.seek_req_goal_va
    seek_best_va = c.seek_best_va
    seek_req_open_va = c.seek_req_open_va
    seek_dist_va = c.seek_dist_va
    bot_seek_va = c.bot_seek_va
    SEEK_ROW = c.SEEK_ROW
    lanes = c.lanes
    cnh_curd_va = c.cnh_curd_va
    cnh_lane_va = c.cnh_lane_va
    route_carry_va = c.route_carry_va
    bot_role_va = c.bot_role_va

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
            # --- ON-THE-WAY join gate. The active seek serves its REQUESTER;
            # a bot joins the descent only when the switch is a LOCAL detour
            # for it too: seek_walk(cur -> switch) + full(switch -> goal) <=
            # full(cur -> goal) + JOIN_SLACK. Unconditional joining was
            # live-diagnosed on Battle on the Ice (2026-07-20): every rebuild
            # of the flapping self-closing team door re-activated the
            # adjacent switch, and teammates far PAST the door — one half the
            # map away at node 13 — turned around to descend to it (snap6:
            # bot_seek all set; slot 1 backtracked 14->54). A bot whose
            # full-field goal distance is unreachable joins unconditionally
            # (nothing better exists for it). edx = team, ebx = cur,
            # edi = goal full-row offset, ecx = seek_dist[cur].
            a.raw(b'\x8B\x04\x95' + le32(seek_node_va))   # eax = seek_node[team]
            a.raw(b'\x3B\x05' + le32(vcount_va))          # defensive range
            a.jae('cnh_seek_fb')
            a.raw(b'\x8B\x84\x87' + le32(flag_dist_va))   # eax = full[goal][switch node]
            a.raw(b'\x83\xF8\xFF')                        # goal full-unreachable from switch?
            a.jz('cnh_seek_fb')                           # -> switch on no path to goal
            a.raw(b'\x01\xC8')                            # eax += ecx (walk + post-open route)
            a.raw(b'\x8B\xB4\x9F' + le32(flag_dist_va))   # esi = full[goal][cur]
            a.raw(b'\x83\xFE\xFF')                        # cur full-unreachable?
            a.jz('cnh_seek_join')                         # -> nothing better; join
            a.raw(b'\x83\xC6' + bytes([cfg.SWITCH_SEEK_JOIN_SLACK]))  # esi += slack
            a.raw(b'\x39\xF0')                            # cmp detour, full[cur]+slack
            a.ja('cnh_seek_fb')                           # off-path -> keep normal routing
            a.label('cnh_seek_join')
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
    if lanes:
        # --- Route-lane select. cnh_curd = the FIXED strict-descent
        # threshold (under lane 1 the running best in ECX becomes a MAX and
        # can no longer double as the descent gate). Lane 1 applies only to
        # a NON-carrying attacker with the lane bit, and never to a seek
        # descent (targeted switch walk stays exact).
        a.raw(b'\x89\x0D' + le32(cnh_curd_va))          # cnh_curd = cur_d
        a.raw(b'\xC7\x05' + le32(cnh_lane_va) + le32(0))
        a.raw(b'\x83\x3D' + le32(route_carry_va) + b'\x00')
        a.jnz('cnh_lane_done')                          # carrier -> shortest home
        a.raw(b'\x8B\x15' + le32(bot_slot_va))          # edx = slot
        if seek:
            a.raw(b'\x83\x3C\x95' + le32(bot_seek_va) + b'\x00')
            a.jnz('cnh_lane_done')                      # seek descent -> exact
        a.raw(b'\xF6\x04\x95' + le32(c.bot_role_va) + b'\x02')  # lane bit?
        a.jz('cnh_lane_done')
        a.raw(b'\xC7\x05' + le32(cnh_lane_va) + le32(1))
        a.label('cnh_lane_done')
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
    if lanes:
        # Strict descent from CUR is the progress guarantee for BOTH lanes;
        # the running best in ECX then tracks the MIN (lane 0 — identical
        # to the pre-lane behaviour, ECX seeded with cur_d) or the MAX
        # (lane 1 — the least-progress descending branch, which peels off
        # the shortest path at forks; first qualifier accepted via the
        # best-node -1 sentinel).
        a.raw(b'\x3B\x3D' + le32(cnh_curd_va))          # nd vs cur_d
        a.jae('cnh_scan_next')                          # not strictly descending
        a.raw(b'\x83\x3D' + le32(cnh_lane_va) + b'\x00')
        a.jnz('cnh_lane1_cmp')
        a.raw(b'\x39\xCF')                              # lane 0: nd < running min?
        a.jae('cnh_scan_next')
        a.jmp('cnh_accept')
        a.label('cnh_lane1_cmp')
        a.raw(b'\x83\xFA\xFF')                          # first qualifying nb?
        a.jz('cnh_accept')
        a.raw(b'\x39\xCF')                              # lane 1: nd > running max?
        a.jbe('cnh_scan_next')
        a.label('cnh_accept')
    else:
        a.raw(b'\x39\xCF')                              # cmp edi, ecx (nd - best_d)
        a.jae('cnh_scan_next')                          # nd >= best_d -> not closer
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

