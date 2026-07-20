"""Goal final approaches: CTF flag base steer-at-target (with its own
watchdog) and the SK own-bin deposit press."""

from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout
from .tuning import WP_SLIDE_TRIGGER_FRAMES


def emit(a: Asm, layout: ScratchLayout, c) -> None:
    bot_pos_va = c.bot_pos_va
    bot_slot_tmp_va = c.bot_slot_tmp_va
    current_wp_va = c.current_wp_va
    wp_try_va = c.wp_try_va
    wp_best_dsq_va = c.wp_best_dsq_va
    stuck_count_va = c.stuck_count_va
    failed_edge_va = c.failed_edge_va
    wp_reached_radius_sq_va = c.wp_reached_radius_sq_va
    wp_progress_timeout_va = c.wp_progress_timeout_va
    overlay_vertices_va = c.overlay_vertices_va
    dx_accum_va = c.dx_accum_va
    dy_accum_va = c.dy_accum_va
    routing = c.routing
    route_goal_flag_va = c.route_goal_flag_va
    flag_route_node_va = c.flag_route_node_va
    flag_table_va = c.flag_table_va
    route_suspend_va = c.route_suspend_va
    route_block_hits_va = c.route_block_hits_va
    seek_move = c.seek_move
    bot_seek_va = c.bot_seek_va
    seek_active_va = c.seek_active_va
    seek_node_va = c.seek_node_va
    switch_table_va = c.switch_table_va
    bot_team_va = c.bot_team_va
    door_gate = c.door_gate
    route_block_door_va = c.route_block_door_va
    sk_move = c.sk_move
    sk_team_mv_va = c.sk_team_mv_va
    sk_suspend_mv_va = c.sk_suspend_mv_va
    sk_active_mv_va = c.sk_active_mv_va
    bot_sk_return_va = c.bot_sk_return_va
    bot_sk_dep_try_va = c.bot_sk_dep_try_va
    sk_bin_table_mv_va = c.sk_bin_table_mv_va
    sk_bin_valid_mv_va = c.sk_bin_valid_mv_va
    sk_bin_node_mv_va = c.sk_bin_node_mv_va

    if routing:
        # Tick down this bot's routing suspension exactly once per think (the
        # other ctf_pick_goal callers — ctf_next_hop, the page-flip force-tick
        # loop — only READ it).
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(route_suspend_va))   # eax = route_suspend[slot]
        a.raw(b'\x85\xC0'); a.jz('s542360_rs_dec_done')
        a.raw(b'\x48')                                    # dec eax
        a.raw(b'\x89\x04\x8D' + le32(route_suspend_va))   # route_suspend[slot] = eax
        a.jnz('s542360_rs_dec_done')                      # still suspended
        # Suspension just expired: forget the blocked edge so resumed routing
        # retries it fresh (a door may have opened in the meantime).
        a.raw(b'\xC7\x04\x8D' + le32(failed_edge_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(route_block_hits_va) + le32(0))
        if door_gate:
            a.raw(b'\xC7\x04\x8D' + le32(route_block_door_va) + b'\xFF\xFF\xFF\xFF')
        a.label('s542360_rs_dec_done')
        a.call_lbl('ctf_pick_goal')                       # route_goal_flag for this bot
        a.raw(b'\xA1' + le32(route_goal_flag_va))         # eax = goal flag idx
        a.raw(b'\x83\xF8\xFF'); a.jz('s542360_wp_not_final')  # no goal -> normal
        if seek_move:
            # Seek final approach takes precedence: bot_seek was set by the
            # arrival that chose the seek field, the team seek is still live,
            # and current_wp IS the switch node -> steer at the switch center
            # (bump it) through the same watchdog as the flag approach. eax
            # (goal) is preserved for the normal path below.
            a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))    # ecx = slot
            a.raw(b'\x83\x3C\x8D' + le32(bot_seek_va) + b'\x00')
            a.jz('s542360_fa_no_seek')
            a.raw(b'\x8B\x14\x8D' + le32(bot_team_va))    # edx = bot_team[slot]
            a.raw(b'\x83\xE2\x01')                        # and edx, 1
            a.raw(b'\x8B\x3C\x95' + le32(seek_active_va)) # edi = seek_active[team]
            a.raw(b'\x85\xFF'); a.jz('s542360_fa_no_seek')
            a.raw(b'\x8B\x34\x95' + le32(seek_node_va))   # esi = seek_node[team]
            a.raw(b'\x8B\x14\x8D' + le32(current_wp_va))  # edx = current_wp[slot]
            a.raw(b'\x39\xD6'); a.jnz('s542360_fa_no_seek')  # cur != switch node
            a.raw(b'\x4F')                                # edi = switch idx (active-1)
            a.raw(b'\xD9\x04\xFD' + le32(switch_table_va))    # fld switch.x
            a.raw(b'\xD8\x25' + le32(bot_pos_va))         # fsub bot.x
            a.raw(b'\xD9\x1D' + le32(dx_accum_va))        # fstp dx_accum
            a.raw(b'\xD9\x04\xFD' + le32(switch_table_va + 4))  # fld switch.y
            a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))     # fsub bot.y
            a.raw(b'\xD9\x1D' + le32(dy_accum_va))        # fstp dy_accum
            a.jmp('s542360_fa_have_target')
            a.label('s542360_fa_no_seek')
        a.raw(b'\x8B\x0C\x85' + le32(flag_route_node_va)) # ecx = flag_route_node[goal]
        a.raw(b'\x8B\x15' + le32(bot_slot_tmp_va))        # edx = slot
        a.raw(b'\x8B\x14\x95' + le32(current_wp_va))      # edx = current_wp[slot] (cur)
        a.raw(b'\x39\xCA'); a.jnz('s542360_wp_not_final') # cur != goal node -> normal
        # desired = flag_table[goal] - bot (eax still = goal). Steer to the flag.
        a.raw(b'\xD9\x04\xC5' + le32(flag_table_va))      # fld [flag_table + eax*8] (flag.x)
        a.raw(b'\xD8\x25' + le32(bot_pos_va))             # fsub bot.x
        a.raw(b'\xD9\x1D' + le32(dx_accum_va))            # fstp dx_accum
        a.raw(b'\xD9\x04\xC5' + le32(flag_table_va + 4))  # fld [flag_table + eax*8 + 4] (flag.y)
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))         # fsub bot.y
        a.raw(b'\xD9\x1D' + le32(dy_accum_va))            # fstp dy_accum
        if seek_move:
            a.label('s542360_fa_have_target')
        # --- Final-approach watchdog. This branch used to jump straight to the
        # emit, bypassing the arrival/progress machinery entirely — a carrier
        # whose straight line to the base was blocked (door, pinch) steered
        # into it FOREVER with no slide escalation and no timeout, until the
        # goal changed (e.g. its flag got stolen again). Mirror the node
        # watchdog here with the FLAG as the target: strict dsq improvement
        # resets wp_try (and wp_best_dsq/wp_try are freshly reset by every
        # path that assigns current_wp, so entering final approach starts
        # clean); no progress ramps wp_try, which drives the wall-slide sweep
        # at the emit; a full progress-timeout gives up on routing for
        # WP_ROUTE_SUSPEND_FRAMES (random graph roam — the same recovery the
        # goal change provided by luck) and falls into the normal node logic.
        a.raw(b'\xD9\x05' + le32(dx_accum_va))            # fld dx
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xD9\x05' + le32(dy_accum_va))            # fld dy
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xDE\xC1')                                # faddp -> ST0 = dsq(bot, flag)
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))     # eax = stuck_count[slot]
        a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))
        a.jae('s542360_fa_no_progress')                   # physically pinned
        a.raw(b'\xD9\x04\x8D' + le32(wp_best_dsq_va))     # fld best (ST0=best, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip st0,st1 (best:dsq, pop)
        a.jbe('s542360_fa_no_progress')                   # best <= dsq -> no improvement
        a.raw(b'\xD9\x1C\x8D' + le32(wp_best_dsq_va))     # best = dsq (pop)
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.jmp('s542360_emit')
        a.label('s542360_fa_no_progress')
        a.raw(b'\xDD\xD8')                                # fstp st(0) (drop dsq)
        a.raw(b'\xFF\x04\x8D' + le32(wp_try_va))          # ++wp_try[slot]
        a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))          # eax = wp_try
        a.raw(b'\x3B\x05' + le32(wp_progress_timeout_va)) # cmp eax, [progress_timeout]
        a.jb('s542360_emit')                              # keep steering; slide sweeps
        a.raw(b'\xC7\x04\x8D' + le32(route_suspend_va)
              + le32(cfg.WP_ROUTE_SUSPEND_FRAMES))        # give up routing for a while
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
        # fall through to the normal node logic: the bot is within reach of the
        # goal node, arrives, and ctf_next_hop (whose ctf_pick_goal now reads
        # the suspension) hands it to the random wp_advance.
        a.label('s542360_wp_not_final')
    if sk_move:
        # --- SK deposit final approach --------------------------------------
        # Once a RETURN-phase bot's current node IS its own bin's nearest
        # node the graph can take it no closer, so steer straight at the bin
        # CENTER — the bin is a collidable prop whose CollideTrigger fires
        # on the bump and the engine's canned action consumes + scores the
        # whole load (sub_561AB0). The per-think sk_update_phase call sees
        # the emptied inventory the same think, clears the RETURN latch, and
        # this block stops firing — the arrival logic resumes and the next
        # sk_next_hop routes back out for more minerals. Exhausted press
        # patience (wedged approach) suspends routing exactly like the CTF
        # final-approach watchdog.
        a.raw(b'\x83\x3D' + le32(sk_active_mv_va) + b'\x00')
        a.jz('s542360_skfa_done')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x83\x3C\x8D' + le32(sk_suspend_mv_va) + b'\x00')
        a.jnz('s542360_skfa_done')                        # suspended -> roam
        a.raw(b'\x83\x3C\x8D' + le32(bot_sk_return_va) + b'\x00')
        a.jz('s542360_skfa_done')                         # collect phase
        a.raw(b'\x8B\x14\x8D' + le32(sk_team_mv_va))        # edx = bot_team[slot]
        a.raw(b'\x83\xE2' + bytes([cfg.SK_BIN_TABLE_MAX - 1]))  # and edx, 15
        a.raw(b'\x83\x3C\x95' + le32(sk_bin_valid_mv_va) + b'\x00')
        a.jz('s542360_skfa_done')                         # no authored bin
        a.raw(b'\x8B\x34\x95' + le32(sk_bin_node_mv_va))  # esi = bin node
        a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))      # eax = current_wp[slot]
        a.raw(b'\x39\xF0'); a.jnz('s542360_skfa_done')    # cur != bin node
        # Standing on the bin node: refresh the carry state each think so
        # the successful deposit ends the press the same frame.
        a.call_lbl('sk_update_phase')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot (reload)
        a.raw(b'\x83\x3C\x8D' + le32(bot_sk_return_va) + b'\x00')
        a.jz('s542360_skfa_deposited')                    # emptied -> done
        a.raw(b'\x8B\x14\x8D' + le32(sk_team_mv_va))        # edx = bot_team[slot]
        a.raw(b'\x83\xE2' + bytes([cfg.SK_BIN_TABLE_MAX - 1]))
        # desired = bin center - bot
        a.raw(b'\xD9\x04\xD5' + le32(sk_bin_table_mv_va))     # fld bin.x [edx*8]
        a.raw(b'\xD8\x25' + le32(bot_pos_va))             # fsub bot.x
        a.raw(b'\xD9\x1D' + le32(dx_accum_va))            # fstp dx_accum
        a.raw(b'\xD9\x04\xD5' + le32(sk_bin_table_mv_va + 4)) # fld bin.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))         # fsub bot.y
        a.raw(b'\xD9\x1D' + le32(dy_accum_va))            # fstp dy_accum
        # Watchdog + press patience (mirror of the switch bump press).
        a.raw(b'\xD9\x05' + le32(dx_accum_va))            # fld dx
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xD9\x05' + le32(dy_accum_va))            # fld dy
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xDE\xC1')                                # faddp -> ST0 = dsq(bot, bin)
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))     # eax = stuck_count[slot]
        a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))
        a.jae('s542360_skfa_no_progress')                 # physically pinned
        a.raw(b'\xD9\x04\x8D' + le32(wp_best_dsq_va))     # fld best (ST0=best, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip st0,st1 (best:dsq, pop)
        a.jbe('s542360_skfa_no_progress')                 # best <= dsq -> no improvement
        a.raw(b'\xD9\x1C\x8D' + le32(wp_best_dsq_va))     # best = dsq (pop)
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.jmp('s542360_emit')
        a.label('s542360_skfa_no_progress')
        a.raw(b'\xDD\xD8')                                # fstp st0 (drop dsq)
        a.raw(b'\xFF\x04\x8D' + le32(wp_try_va))          # ++wp_try[slot]
        a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))          # eax = wp_try
        a.raw(b'\x3B\x05' + le32(wp_progress_timeout_va))
        a.jb('s542360_emit')                              # keep pressing; slide sweeps
        a.raw(b'\xFF\x04\x8D' + le32(bot_sk_dep_try_va))  # ++patience used
        a.raw(b'\x83\x3C\x8D' + le32(bot_sk_dep_try_va)
              + bytes([cfg.SK_DEPOSIT_PRESS_PATIENCE]))
        a.ja('s542360_skfa_impatient')                    # budget exhausted
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))     # fresh cycle
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
        a.jmp('s542360_emit')                             # keep pressing the bin
        a.label('s542360_skfa_impatient')
        a.raw(b'\xC7\x04\x8D' + le32(bot_sk_dep_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(sk_suspend_mv_va)
              + le32(cfg.WP_ROUTE_SUSPEND_FRAMES))        # roam, retry later
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
        a.jmp('s542360_skfa_done')                        # fall into node logic
        a.label('s542360_skfa_deposited')
        # Deposit landed: clean trackers so the node logic resumes fresh.
        a.raw(b'\xC7\x04\x8D' + le32(bot_sk_dep_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.label('s542360_skfa_done')
    # Arrival test: dsq(bot, vertices[cur]) < wp_reached_radius_sq ?
    # If the bot is wedged and already near the node, accept a larger "stuck
    # arrival" radius. This avoids the far-bot failure where collision leaves a
    # CTF bot 75-100px from the node, outside the normal 64px radius, and the
    # router retries the same blocked final pixels forever.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))          # eax = cur
    a.raw(b'\x8D\x0C\xC5' + le32(overlay_vertices_va))    # lea ecx, [eax*8 + verts]
    a.raw(b'\xD9\x01')                                    # fld [ecx]     v.x
    a.raw(b'\xD8\x25' + le32(bot_pos_va))                 # fsub bot.x
    a.raw(b'\xD8\xC8')                                    # fmul st,st    dx²
    a.raw(b'\xD9\x41\x04')                                # fld [ecx+4]   v.y
    a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))             # fsub bot.y
    a.raw(b'\xD8\xC8')                                    # fmul st,st    dy²
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0 = dsq
    # Keep dsq on the FPU for the progress check. fcomip (radius : dsq) pops the
    # radius and leaves dsq; CF=1 iff radius < dsq (NOT arrived).
    a.raw(b'\xD9\x05' + le32(wp_reached_radius_sq_va))    # fld radius
    a.raw(b'\xDF\xF1')                                    # fcomip st0,st1
    a.jb('s542360_wp_not_arrived')                        # radius < dsq -> maybe stuck-near
    a.raw(b'\xDD\xD8')                                    # fstp st(0)  (arrived: pop dsq)
    a.jmp('s542360_wp_arrived')

