"""Progress-toward-target watchdog: arrival test, stuck/near checks,
progress tracking, timeout recovery (alternate edge, retreat,
fresh-nearest reacquire) and the wedge-cluster HARD RESET."""

from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout
from .tuning import WP_SLIDE_TRIGGER_FRAMES


def emit(a: Asm, layout: ScratchLayout, c) -> None:
    bot_pos_va = c.bot_pos_va
    bot_slot_tmp_va = c.bot_slot_tmp_va
    current_wp_va = c.current_wp_va
    prev_wp_va = c.prev_wp_va
    wp_try_va = c.wp_try_va
    wp_best_dsq_va = c.wp_best_dsq_va
    stuck_count_va = c.stuck_count_va
    failed_edge_va = c.failed_edge_va
    slide_turn_va = c.slide_turn_va
    wp_progress_timeout_va = c.wp_progress_timeout_va
    wp_stuck_reached_radius_sq_va = c.wp_stuck_reached_radius_sq_va
    failed_cur_tmp_va = c.failed_cur_tmp_va
    prev_tmp_va = c.prev_tmp_va
    overlay_vertex_count_va = c.overlay_vertex_count_va
    overlay_vertices_va = c.overlay_vertices_va
    wp_scratch_va = c.wp_scratch_va
    routing = c.routing
    route_goal_flag_va = c.route_goal_flag_va
    routing_active_va = c.routing_active_va
    route_suspend_va = c.route_suspend_va
    route_block_hits_va = c.route_block_hits_va
    door_gate = c.door_gate
    route_block_door_va = c.route_block_door_va
    door_blocked_va = c.door_blocked_va
    door_count_va = c.door_count_va
    wedge_reset = c.wedge_reset
    bot_wedge_cycles_va = c.bot_wedge_cycles_va
    wpfn_excl_va = c.wpfn_excl_va
    fight_stall = c.fight_stall

    a.label('s542360_wp_not_arrived')                     # ST0 = dsq
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))         # eax = stuck_count[slot]
    a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))
    a.jae('s542360_wp_stuck_near_check')
    a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))              # eax = wp_try[slot]
    a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))
    a.jb('s542360_wp_progress')                           # not wedged; normal progress watchdog
    a.label('s542360_wp_stuck_near_check')
    a.raw(b'\xD9\x05' + le32(wp_stuck_reached_radius_sq_va))  # fld stuck-arrival radius
    a.raw(b'\xDF\xF1')                                    # fcomip radius, dsq; pop radius
    a.jb('s542360_wp_maybe_prev_arrived')                  # not near cur; maybe already back at prev
    a.raw(b'\xDD\xD8')                                    # fstp st(0)  (near enough: pop dsq)
    # STUCK-radius arrival: enter past the wedge-counter reset (the 128px
    # ball pokes through walls, so these arrivals must not count as real
    # progress for the hard-reset escalation) but still through the
    # door-side arrival gate.
    a.jmp('s542360_wp_arrived_gate')

    # If the bot just failed an edge and is physically wedged near the previous
    # node, count the previous node as reached immediately. Latest dump:
    # current=14, prev=15, failed_edge_marker=(14,15), bot is ~72px from prev but ~333px
    # from current. Waiting for the full timeout leaves it visibly stalled; this
    # swap lets the normal arrival code advance from prev while excluding the
    # failed current node.
    a.label('s542360_wp_maybe_prev_arrived')               # ST0 = dsq-to-current
    a.raw(b'\xDD\xD8')                                    # fstp st(0)  (drop current dsq)
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x1C\x8D' + le32(failed_edge_va))         # ebx = failed_edge_marker[slot]
    a.raw(b'\x85\xDB')                                    # test ebx, ebx
    a.jz('s542360_wp_no_progress_popped')
    a.raw(b'\x8B\x04\x8D' + le32(prev_wp_va))             # eax = prev_wp[slot]
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_wp_no_progress_popped')
    a.raw(b'\x3B\x05' + le32(overlay_vertex_count_va))    # prev >= vertex_count?
    a.jae('s542360_wp_no_progress_popped')
    # Only apply this shortcut for the edge that actually failed. The marker is
    # unordered: ((max(prev,cur)+1)<<16) | (min(prev,cur)+1), so it blocks both
    # directions without treating every future backtrack as bad.
    a.raw(b'\x8B\x14\x8D' + le32(current_wp_va))          # edx = current_wp[slot]
    a.raw(b'\x89\xC6')                                    # esi = prev
    a.raw(b'\x89\xD7')                                    # edi = current
    a.raw(b'\x39\xFE')                                    # cmp esi, edi
    a.jbe('s542360_wp_prev_edge_ordered')
    a.raw(b'\x87\xFE')                                    # xchg esi, edi
    a.label('s542360_wp_prev_edge_ordered')
    a.raw(b'\x46')                                        # inc esi (min+1)
    a.raw(b'\x47')                                        # inc edi (max+1)
    a.raw(b'\xC1\xE7\x10')                                # shl edi, 16
    a.raw(b'\x09\xFE')                                    # or esi, edi
    a.raw(b'\x39\xDE')                                    # cmp esi, ebx
    a.jnz('s542360_wp_no_progress_popped')
    a.raw(b'\x8D\x14\xC5' + le32(overlay_vertices_va))    # edx = &verts[prev]
    a.raw(b'\xD9\x02')                                    # fld [edx]     prev.x
    a.raw(b'\xD8\x25' + le32(bot_pos_va))                 # fsub bot.x
    a.raw(b'\xD8\xC8')                                    # fmul st,st    dx²
    a.raw(b'\xD9\x42\x04')                                # fld [edx+4]   prev.y
    a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))             # fsub bot.y
    a.raw(b'\xD8\xC8')                                    # fmul st,st    dy²
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0 = prev_dsq
    a.raw(b'\xD9\x05' + le32(wp_stuck_reached_radius_sq_va))  # fld stuck-arrival radius
    a.raw(b'\xDF\xF1')                                    # fcomip radius, prev_dsq; pop radius
    a.jb('s542360_wp_prev_not_close')                     # radius < prev_dsq
    a.raw(b'\xDD\xD8')                                    # fstp st(0)  (drop prev_dsq)
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\x8D' + le32(prev_wp_va))             # eax = old prev
    a.raw(b'\x8B\x14\x8D' + le32(current_wp_va))          # edx = old current / failed node
    a.raw(b'\x89\x04\x8D' + le32(current_wp_va))          # current_wp = old prev
    a.raw(b'\x89\x14\x8D' + le32(prev_wp_va))             # prev_wp = old current
    # Same stuck-radius caveat as above: skip the wedge-counter reset.
    a.jmp('s542360_wp_arrived_gate')

    a.label('s542360_wp_prev_not_close')
    a.raw(b'\xDD\xD8')                                    # fstp st(0)  (drop prev_dsq)
    a.jmp('s542360_wp_no_progress_popped')

    # --- Progress-toward-target watchdog (off-graph pin safety net). ST0=dsq.
    a.label('s542360_wp_progress')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot (lea clobbered it)
    # Meaningful progress only. The latest R-dump showed a physically stuck bot
    # with stuck_count in the thousands but wp_try pinned at 0 because tiny
    # sub-pixel distance decreases kept resetting the strict best-dsq check.
    # Once the position-delta detector says "not really moving", force the
    # watchdog down the no-progress path so retreat/reroute can actually fire.
    a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))         # eax = stuck_count[slot]
    a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))
    a.jae('s542360_wp_no_progress')                       # ST0=dsq, no meaningful progress
    a.raw(b'\xD9\x04\x8D' + le32(wp_best_dsq_va))         # fld best_dsq[slot] (ST0=best, ST1=dsq)
    a.raw(b'\xDF\xF1')                                    # fcomip st0,st1 (best:dsq, pop best)
    a.jbe('s542360_wp_no_progress')                       # best <= dsq -> no improvement
    a.raw(b'\xD9\x1C\x8D' + le32(wp_best_dsq_va))         # fstp best_dsq[slot] = dsq
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))    # wp_try = 0
    a.jmp('s542360_wp_steer')

    a.label('s542360_wp_no_progress')
    a.raw(b'\xDD\xD8')                                    # fstp st(0)  (drop dsq)
    a.label('s542360_wp_no_progress_popped')
    a.raw(b'\xFF\x04\x8D' + le32(wp_try_va))              # ++wp_try[slot]
    a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))              # eax = wp_try
    a.raw(b'\x3B\x05' + le32(wp_progress_timeout_va))     # cmp eax, [progress_timeout]
    a.jb('s542360_wp_steer')                              # under budget -> keep steering
    # fall through: wedged off-graph too long -> re-acquire nearest

    a.label('s542360_wp_reacquire')
    if routing:
        # A hard wedge while actively ROUTING (goal set this frame by the
        # have_cur ctf_pick_goal call): BFS is deterministic, so after the
        # local alternate/retreat below the next arrivals would funnel the bot
        # straight back into the same blocked segment (classic case: a door
        # the camera-gated engine never opens, with routing re-selecting the
        # door edge from every direction — the single failed_edge_marker can't
        # hold more than one blocked edge). Suspend routing so the bot roams
        # the graph randomly for a while instead of ping-ponging, then routing
        # resumes automatically.
        a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00')
        a.jz('s542360_wp_reacq_no_suspend')
        a.raw(b'\xA1' + le32(route_goal_flag_va))         # eax = this bot's goal
        a.raw(b'\x83\xF8\xFF')
        a.jz('s542360_wp_reacq_no_suspend')               # roaming wedge -> no suspend
        if door_gate and layout.has_field('bot_door_patience'):
            # DOOR PATIENCE: a routed timeout while wedged against a CLOSED
            # door means the bot may be one slide-sweep away from the door's
            # (tiny) walk-up trigger oval — live trace caught the red door
            # opening the very sample the suspension threw the bot away.
            # Latch the wedge door now; while it is closed, restart the
            # watchdog and keep pressing instead of suspending, up to
            # WP_DOOR_PRESS_PATIENCE timeout cycles.
            bot_door_patience_va = layout.va('bot_door_patience')
            a.call_lbl('door_capture_wedge')              # latch nearest blocked door
            a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))    # ecx = slot
            a.raw(b'\x8B\x04\x8D' + le32(route_block_door_va))  # eax = latched door
            a.raw(b'\x83\xF8\xFF')
            a.jz('s542360_wp_door_node_try')              # no bot-near door -> node gate
            a.label('s542360_wp_door_have')
            a.raw(b'\x3B\x05' + le32(door_count_va))      # stale idx?
            a.jae('s542360_wp_door_impatient')
            a.raw(b'\x83\x3C\x85' + le32(door_blocked_va) + b'\x00')
            a.jz('s542360_wp_door_impatient')             # door open -> not this case
            a.raw(b'\xFF\x04\x8D' + le32(bot_door_patience_va))  # ++patience[slot]
            a.raw(b'\x83\x3C\x8D' + le32(bot_door_patience_va)
                  + bytes([cfg.WP_DOOR_PRESS_PATIENCE]))
            a.ja('s542360_wp_door_impatient')             # budget exhausted -> suspend
            a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))   # fresh watchdog
            a.jmp('s542360_wp_steer')                     # keep pressing the door
            # No blocked door near the BOT — but the timeout may still be a
            # door wedge: live 2026-07-20 follow-up caught the carrier
            # grinding the wall 136px west of the doorway (beyond the
            # bot-radius capture) while its TARGET node 47 sat 30px behind
            # the closed door. Latch by the arrival-gate predicate instead
            # (target node door-adjacent + bot across it) so press patience
            # engages — the slide walks the bot along the wall into the
            # doorway/trigger — instead of alternating onto another
            # cross-wall node and arming the suspension.
            a.label('s542360_wp_door_node_try')
            a.call_lbl('door_capture_node_gate')          # latch by target node
            a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))    # ecx = slot
            a.raw(b'\x8B\x04\x8D' + le32(route_block_door_va))  # eax = latched door
            a.raw(b'\x83\xF8\xFF')
            a.jnz('s542360_wp_door_have')                 # latched -> press patience
            a.label('s542360_wp_door_impatient')
            a.raw(b'\xC7\x04\x8D' + le32(bot_door_patience_va) + le32(0))
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        if fight_stall:
            # FIGHT STALL: a routed progress stall with a live enemy in close
            # range is usually the fight (knockback, body-block), not
            # geometry. Suspending here made ctf_pick_goal report no goal, so
            # a flag CARRIER roamed randomly mid-fight instead of pressing
            # home (user-reported 2026-07-20). Keep routing; the marker /
            # alternate / hard-reset machinery below still runs.
            a.raw(b'\x83\x3C\x8D' + le32(layout.va('bot_enemy_near')) + b'\x00')
            a.jnz('s542360_wp_reacq_no_suspend')
        a.raw(b'\xC7\x04\x8D' + le32(route_suspend_va)
              + le32(cfg.WP_ROUTE_SUSPEND_FRAMES))
        a.label('s542360_wp_reacq_no_suspend')
    # Couldn't reach cur for WP_PROGRESS_TIMEOUT frames (a wall blocks the
    # straight edge, or the node is otherwise unreachable from here). If LATCHED,
    # try to route AROUND the failed edge immediately: pick an alternate
    # neighbour of prev, excluding the failed cur. R-dumps on the CTF stall
    # showed the old "retreat to prev" recovery still wedged between the same
    # two nodes, cycling current/prev without escaping. If prev has no alternate
    # neighbour, fall back to the old retreat-to-prev behavior. If NOT latched,
    # re-acquire the nearest node. If that re-acquire returns the SAME failed
    # target, keep wp_try/slide_turn intact so the wall-slide can continue
    # sweeping instead of resetting every timeout.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\x8D' + le32(prev_wp_va))             # eax = prev_wp[slot]
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_wp_reacq_nearest')                      # not latched -> reacquire nearest
    # Latched: prefer an alternate neighbour of prev, excluding failed cur.
    # Spill both node ids across wp_advance; with random-neighbour enabled the
    # helper uses wp_scratch internally, so use movement scratch that will be
    # overwritten before the final steer vector is emitted.
    a.raw(b'\x8B\x14\x8D' + le32(current_wp_va))          # edx = failed cur
    a.raw(b'\x89\x15' + le32(failed_cur_tmp_va))          # spill failed cur
    a.raw(b'\xA3' + le32(prev_tmp_va))                    # spill prev (eax)
    a.raw(b'\x89\xC1')                                    # ecx = prev
    # edx already = failed cur. wp_advance(prev, failed_cur) returns a neighbour
    # of prev that is NOT failed_cur when one exists; otherwise failed_cur/-1.
    a.call_lbl('wp_advance')
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_wp_timeout_retreat')
    a.raw(b'\x3B\x05' + le32(failed_cur_tmp_va))          # alt == failed cur?
    a.jz('s542360_wp_timeout_retreat')
    # Alternate exists: current = alt, prev = old prev. This keeps the latch on
    # the graph but heads away from the failed edge immediately.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x89\x04\x8D' + le32(current_wp_va))          # current_wp[slot] = alt
    a.raw(b'\x8B\x15' + le32(prev_tmp_va))                # edx = old prev
    a.raw(b'\x89\x14\x8D' + le32(prev_wp_va))             # prev_wp[slot] = old prev
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))               # wp_try = 0
    # failed_edge_marker = unordered(old prev, failed cur), with +1 packing so
    # zero remains the "no blocked edge" sentinel.
    a.raw(b'\x8B\x15' + le32(prev_tmp_va))                # edx = old prev
    a.raw(b'\xA1' + le32(failed_cur_tmp_va))              # eax = failed cur
    a.raw(b'\x39\xC2')                                    # cmp edx, eax
    a.jbe('s542360_wp_alt_edge_ordered')
    a.raw(b'\x92')                                        # xchg eax, edx
    a.label('s542360_wp_alt_edge_ordered')
    a.raw(b'\x42')                                        # inc edx (min+1)
    a.raw(b'\x40')                                        # inc eax (max+1)
    a.raw(b'\xC1\xE0\x10')                                # shl eax, 16
    a.raw(b'\x09\xC2')                                    # or edx, eax
    a.raw(b'\x89\x14\x8D' + le32(failed_edge_va))         # failed_edge_marker[slot] = edx
    if routing:
        a.raw(b'\xC7\x04\x8D' + le32(route_block_hits_va) + le32(0))  # fresh retry budget
    a.raw(b'\xC7\x04\x8D' + le32(slide_turn_va) + le32(0))           # slide_turn = 0
    if door_gate:
        a.call_lbl('door_capture_wedge')                  # latch nearest blocked door (pushad-safe)
    if wedge_reset:
        # One recovery action taken with no arrival since — count toward the
        # wedge hard reset (see s542360_wp_hard_reset).
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\xFF\x04\x8D' + le32(bot_wedge_cycles_va))  # ++wedge_cycles[slot]
        a.raw(b'\x83\x3C\x8D' + le32(bot_wedge_cycles_va)
              + bytes([cfg.WP_WEDGE_RESET_CYCLES]))
        a.jae('s542360_wp_hard_reset')
    a.jmp('s542360_wp_steer')

    # No alternate: swap cur <-> prev (old behavior).
    a.label('s542360_wp_timeout_retreat')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xA1' + le32(prev_tmp_va))                    # eax = old prev
    a.raw(b'\x8B\x15' + le32(failed_cur_tmp_va))          # edx = failed cur
    a.raw(b'\x89\x04\x8D' + le32(current_wp_va))          # current_wp[slot] = prev
    a.raw(b'\x89\x14\x8D' + le32(prev_wp_va))             # prev_wp[slot]    = old cur
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))               # wp_try = 0
    # failed_edge_marker = unordered(old prev, failed cur).
    a.raw(b'\x8B\x15' + le32(prev_tmp_va))                # edx = old prev
    a.raw(b'\xA1' + le32(failed_cur_tmp_va))              # eax = failed cur
    a.raw(b'\x39\xC2')                                    # cmp edx, eax
    a.jbe('s542360_wp_retreat_edge_ordered')
    a.raw(b'\x92')                                        # xchg eax, edx
    a.label('s542360_wp_retreat_edge_ordered')
    a.raw(b'\x42')                                        # inc edx (min+1)
    a.raw(b'\x40')                                        # inc eax (max+1)
    a.raw(b'\xC1\xE0\x10')                                # shl eax, 16
    a.raw(b'\x09\xC2')                                    # or edx, eax
    a.raw(b'\x89\x14\x8D' + le32(failed_edge_va))         # failed_edge_marker[slot] = edx
    if routing:
        a.raw(b'\xC7\x04\x8D' + le32(route_block_hits_va) + le32(0))  # fresh retry budget
    a.raw(b'\xC7\x04\x8D' + le32(slide_turn_va) + le32(0))           # slide_turn = 0
    if door_gate:
        a.call_lbl('door_capture_wedge')                  # latch nearest blocked door (pushad-safe)
    if wedge_reset:
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\xFF\x04\x8D' + le32(bot_wedge_cycles_va))  # ++wedge_cycles[slot]
        a.raw(b'\x83\x3C\x8D' + le32(bot_wedge_cycles_va)
              + bytes([cfg.WP_WEDGE_RESET_CYCLES]))
        a.jae('s542360_wp_hard_reset')
    a.jmp('s542360_wp_steer')

    a.label('s542360_wp_reacq_nearest')
    a.raw(b'\xA1' + le32(bot_pos_va))                     # stage bot pos -> wp_scratch
    a.raw(b'\xA3' + le32(wp_scratch_va))
    a.raw(b'\xA1' + le32(bot_pos_va + 4))
    a.raw(b'\xA3' + le32(wp_scratch_va + 4))
    a.call_lbl('wp_find_nearest')                         # ebx = nearest idx or -1
    a.raw(b'\x83\xFB\xFF')                                # cmp ebx, -1
    a.jz('s542360_wp_steer')                              # no candidate -> keep cur
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # reload slot
    a.raw(b'\x3B\x1C\x8D' + le32(current_wp_va))          # same nearest as failed cur?
    if wedge_reset:
        a.jz('s542360_wp_sweep_check')                    # keep sweeping, but bounded
    else:
        a.jz('s542360_wp_steer')                          # preserve high wp_try + slide sweep
    a.raw(b'\x89\x1C\x8D' + le32(current_wp_va))          # current_wp[slot] = nearest
    a.raw(b'\xC7\x04\x8D' + le32(prev_wp_va) + b'\xFF\xFF\xFF\xFF')   # prev_wp = -1
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))               # wp_try = 0
    a.raw(b'\xC7\x04\x8D' + le32(failed_edge_va) + le32(0))          # failed_edge_marker = 0
    if routing:
        a.raw(b'\xC7\x04\x8D' + le32(route_block_hits_va) + le32(0))
    if door_gate:
        a.raw(b'\xC7\x04\x8D' + le32(route_block_door_va) + b'\xFF\xFF\xFF\xFF')
    a.raw(b'\xC7\x04\x8D' + le32(slide_turn_va) + le32(0))           # slide_turn = 0
    if wedge_reset:
        # A fresh nearest node is still a recovery action, not an arrival —
        # if these keep chaining without any arrival the bot is orbiting a
        # wall pocket; count toward the hard reset.
        a.raw(b'\xFF\x04\x8D' + le32(bot_wedge_cycles_va))  # ++wedge_cycles[slot]
        a.raw(b'\x83\x3C\x8D' + le32(bot_wedge_cycles_va)
              + bytes([cfg.WP_WEDGE_RESET_CYCLES]))
        a.jae('s542360_wp_hard_reset')
    a.jmp('s542360_wp_steer')

    if wedge_reset:
        # Unlatched bot stuck on the SAME nearest node: keep the high wp_try
        # so the wall-slide sweep continues — but bound it. If wp_try passes
        # 4 full timeout windows without a single arrival, the node is
        # presumed unreachable from this side (live 2026-07-20: the reacquire
        # kept re-picking the wrong-side node across the wall) -> hard reset.
        a.label('s542360_wp_sweep_check')
        a.raw(b'\xA1' + le32(wp_progress_timeout_va))     # eax = timeout knob
        a.raw(b'\xC1\xE0\x02')                            # eax *= 4
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x39\x04\x8D' + le32(wp_try_va))          # cmp wp_try[slot], eax
        a.jae('s542360_wp_hard_reset')
        a.jmp('s542360_wp_steer')

        # --- Wedge-cluster HARD RESET (live 2026-07-20, Battle on the Ice
        # R snaps 1-3). A bot on the WRONG SIDE of a wall/door whose latched
        # nodes sit across it cycles the local recovery forever: the
        # alternate-neighbour path only explores neighbours of prev (all
        # across the wall — live: cur flipped 77<->47 with prev=78 while the
        # bot stood north of the closed south team door), retreat swaps
        # within the same pair, and the unlatched reacquire re-picks the
        # Euclidean-nearest node (78, also across the wall) — the reachable
        # around-route entry (48) was never tried. After WP_WEDGE_RESET_
        # CYCLES recovery actions without a single arrival (or a sweep stuck
        # 4 windows on one node), cold-acquire the nearest node EXCLUDING
        # the wedge cluster: failed cur, prev, and the failed-edge marker's
        # two nodes (+1-packed). The marker is deliberately KEPT as wedge
        # memory so consecutive resets keep widening the exclusion set.
        a.label('s542360_wp_hard_reset')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))
        a.raw(b'\xA3' + le32(wpfn_excl_va))               # excl[0] = cur
        a.raw(b'\x8B\x04\x8D' + le32(prev_wp_va))
        a.raw(b'\xA3' + le32(wpfn_excl_va + 4))           # excl[1] = prev (-1 ok)
        a.raw(b'\xC7\x05' + le32(wpfn_excl_va + 8) + b'\xFF\xFF\xFF\xFF')
        a.raw(b'\xC7\x05' + le32(wpfn_excl_va + 12) + b'\xFF\xFF\xFF\xFF')
        a.raw(b'\x8B\x1C\x8D' + le32(failed_edge_va))     # ebx = marker (+1-packed, 0 = none)
        a.raw(b'\x85\xDB')
        a.jz('s542360_wp_hr_nomark')
        a.raw(b'\x0F\xB7\xC3')                            # movzx eax, bx
        a.raw(b'\x48')                                    # dec eax (undo +1)
        a.raw(b'\xA3' + le32(wpfn_excl_va + 8))           # excl[2] = marker lo node
        a.raw(b'\x89\xD8')                                # eax = marker
        a.raw(b'\xC1\xE8\x10')                            # shr eax, 16
        a.raw(b'\x48')                                    # dec eax
        a.raw(b'\xA3' + le32(wpfn_excl_va + 12))          # excl[3] = marker hi node
        a.label('s542360_wp_hr_nomark')
        a.raw(b'\xA1' + le32(bot_pos_va))                 # stage bot pos -> wp_scratch
        a.raw(b'\xA3' + le32(wp_scratch_va))
        a.raw(b'\xA1' + le32(bot_pos_va + 4))
        a.raw(b'\xA3' + le32(wp_scratch_va + 4))
        a.call_lbl('wp_find_nearest_ex')                  # ebx = escape idx or -1
        a.raw(b'\x83\xFB\xFF')
        a.jz('s542360_wp_steer')                          # nothing outside the cluster
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x89\x1C\x8D' + le32(current_wp_va))      # current_wp[slot] = escape node
        a.raw(b'\xC7\x04\x8D' + le32(prev_wp_va) + b'\xFF\xFF\xFF\xFF')
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(bot_wedge_cycles_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(slide_turn_va) + le32(0))
        if routing:
            a.raw(b'\xC7\x04\x8D' + le32(route_block_hits_va) + le32(0))
        a.jmp('s542360_wp_steer')

