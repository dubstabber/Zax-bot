"""Latch-driven pursuit approaches at ``s542360_wp_have_cur``: portal pad
final approach, dropped-flag pursuit (ROUTED/DIRECT), enemy-carrier chase
(ROUTED/DIRECT), goody pursuit (piles + filler items) and the roam switch
wander-bump press."""

import struct

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
    wp_progress_timeout_va = c.wp_progress_timeout_va
    wp_stuck_reached_radius_sq_va = c.wp_stuck_reached_radius_sq_va
    overlay_vertices_va = c.overlay_vertices_va
    dx_accum_va = c.dx_accum_va
    dy_accum_va = c.dy_accum_va
    routing = c.routing
    routing_active_va = c.routing_active_va
    route_suspend_va = c.route_suspend_va
    portal_move = c.portal_move
    bot_portal_target_va = c.bot_portal_target_va
    portal_table_va = c.portal_table_va
    portal_count_va = c.portal_count_va
    portal_active_mv_va = c.portal_active_mv_va
    drop_move = c.drop_move
    flag_drop_valid_mv_va = c.flag_drop_valid_mv_va
    flag_drop_pos_mv_va = c.flag_drop_pos_mv_va
    flag_drop_node_mv_va = c.flag_drop_node_mv_va
    bot_drop_target_va = c.bot_drop_target_va
    bot_drop_cd_va = c.bot_drop_cd_va
    bot_drop_try_va = c.bot_drop_try_va
    bot_drop_best_va = c.bot_drop_best_va
    drop_enabled_va = c.drop_enabled_va
    drop_radius_va = c.drop_radius_va
    drop_reached_va = c.drop_reached_va
    drop_direct_va = c.drop_direct_va
    drop_abandon_va = c.drop_abandon_va
    drop_missing_goal_va = c.drop_missing_goal_va
    flag_present_mv_va = c.flag_present_mv_va
    flag_count_mv_va = c.flag_count_mv_va
    switch_wander = c.switch_wander
    bot_switch_target_va = c.bot_switch_target_va
    bot_switch_cd_va = c.bot_switch_cd_va
    bot_switch_try_va = c.bot_switch_try_va
    bot_switch_snap_va = c.bot_switch_snap_va
    switch_table_sw_va = c.switch_table_sw_va
    switch_count_sw_va = c.switch_count_sw_va
    sk_active_mv_va = c.sk_active_mv_va
    bot_pile_target_va = c.bot_pile_target_va
    bot_pile_cd_va = c.bot_pile_cd_va
    bot_pile_try_va = c.bot_pile_try_va
    bot_pile_best_va = c.bot_pile_best_va
    sk_pile_valid_mv_va = c.sk_pile_valid_mv_va
    sk_pile_radius_va = c.sk_pile_radius_va
    sk_pile_reached_va = c.sk_pile_reached_va
    goody_move = c.goody_move
    goody_tx_va = c.goody_tx_va
    goody_ty_va = c.goody_ty_va
    goody_node_va = c.goody_node_va
    goody_idx_va = c.goody_idx_va
    goody_scan_rad_va = c.goody_scan_rad_va
    goody_scan_cat_va = c.goody_scan_cat_va
    item_active_mv_va = c.item_active_mv_va
    item_cat_mv_va = c.item_cat_mv_va
    item_radius_mv_va = c.item_radius_mv_va
    goody_direct_va = c.goody_direct_va
    goody_abandon_va = c.goody_abandon_va
    sk_pile_dirty_mv_va = c.sk_pile_dirty_mv_va
    chase_move = c.chase_move
    bot_chase_flag_va = c.bot_chase_flag_va
    bot_chase_cd_va = c.bot_chase_cd_va
    chase_pos_mv_va = c.chase_pos_mv_va
    chase_node_mv_va = c.chase_node_mv_va
    chase_ttl_mv_va = c.chase_ttl_mv_va
    chase_dsq_tmp_va = c.chase_dsq_tmp_va
    chase_flag_present_va = c.chase_flag_present_va
    chase_flag_count_va = c.chase_flag_count_va

    a.label('s542360_wp_have_cur')
    if portal_move:
        # --- Portal pad final approach (latch-driven, mode-independent) -----
        # Takes precedence over every node behaviour: a latched bot walks at
        # the pad center until the teleport fires (the jump detect in the
        # stuck stage then cold-reacquires at the exit) or the watchdog gives
        # up. Fast path when unlatched: two loads + jz.
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(bot_portal_target_va))  # eax = latch (idx+1)
        a.raw(b'\x85\xC0'); a.jz('s542360_ptl_done')
        a.raw(b'\x48')                                    # eax = pad idx
        a.raw(b'\x3B\x05' + le32(portal_count_va))        # stale idx (map change)?
        a.jae('s542360_ptl_clear')
        if portal_active_mv_va:
            a.raw(b'\x83\x3C\x85' + le32(portal_active_mv_va) + b'\x00')
            a.jz('s542360_ptl_clear')                     # pad went inactive -> drop
        # desired = pad center - bot
        a.raw(b'\xD9\x04\xC5' + le32(portal_table_va))    # fld pad.x
        a.raw(b'\xD8\x25' + le32(bot_pos_va))             # fsub bot.x
        a.raw(b'\xD9\x1D' + le32(dx_accum_va))            # fstp dx_accum
        a.raw(b'\xD9\x04\xC5' + le32(portal_table_va + 4))  # fld pad.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))         # fsub bot.y
        a.raw(b'\xD9\x1D' + le32(dy_accum_va))            # fstp dy_accum
        # Watchdog — mirror of the CTF flag final approach: strict dsq
        # improvement resets wp_try; stalling ramps it (drives the wall-slide
        # sweep at the emit); a full progress-timeout drops the latch and
        # suspends routing so deterministic re-picks don't loop.
        a.raw(b'\xD9\x05' + le32(dx_accum_va))            # fld dx
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xD9\x05' + le32(dy_accum_va))            # fld dy
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xDE\xC1')                                # faddp -> ST0 = dsq(bot, pad)
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))     # eax = stuck_count[slot]
        a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))
        a.jae('s542360_ptl_no_progress')                  # physically pinned
        a.raw(b'\xD9\x04\x8D' + le32(wp_best_dsq_va))     # fld best (ST0=best, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip st0,st1 (best:dsq, pop)
        a.jbe('s542360_ptl_no_progress')                  # best <= dsq -> no improvement
        a.raw(b'\xD9\x1C\x8D' + le32(wp_best_dsq_va))     # best = dsq (pop)
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.jmp('s542360_emit')
        a.label('s542360_ptl_no_progress')
        a.raw(b'\xDD\xD8')                                # fstp st(0) (drop dsq)
        a.raw(b'\xFF\x04\x8D' + le32(wp_try_va))          # ++wp_try[slot]
        a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))          # eax = wp_try
        a.raw(b'\x3B\x05' + le32(wp_progress_timeout_va))
        a.jb('s542360_emit')                              # keep pressing; slide sweeps
        if layout.has_field('bot_pad_try'):
            # PAD-PRESS PATIENCE (mirror of the door patience): the trigger
            # is a thin sliver on a collidable teleporter prop, so one
            # watchdog window often ends before the wall-slide sweep has
            # walked a heading onto it (live snapshots caught a carrier
            # suspending at the pad, roaming 240 thinks, then succeeding on
            # the second visit). Restart the watchdog and keep pressing for
            # up to PORTAL_PRESS_PATIENCE timeout cycles before giving up.
            a.raw(b'\xFF\x04\x8D' + le32(layout.va('bot_pad_try')))  # ++pad_try
            a.raw(b'\x83\x3C\x8D' + le32(layout.va('bot_pad_try'))
                  + bytes([cfg.PORTAL_PRESS_PATIENCE]))
            a.ja('s542360_ptl_impatient')                 # budget exhausted
            a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))       # fresh watchdog
            a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
            a.jmp('s542360_emit')                         # keep pressing the pad
            a.label('s542360_ptl_impatient')
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_pad_try')) + le32(0))
        if routing:
            a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00')
            a.jz('s542360_ptl_to_clear')
            a.raw(b'\xC7\x04\x8D' + le32(route_suspend_va)
                  + le32(cfg.WP_ROUTE_SUSPEND_FRAMES))    # roam before re-picking the pad
            a.label('s542360_ptl_to_clear')
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
        a.label('s542360_ptl_clear')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot (reload)
        a.raw(b'\xC7\x04\x8D' + le32(bot_portal_target_va) + le32(0))
        a.label('s542360_ptl_done')
    if drop_move:
        # --- Dropped-flag pursuit (latch entry + ROUTED/DIRECT phase split;
        # see drop_move above). Runs after the pad latch (a pad-latched bot
        # finishes its teleport first). DIRECT phase jumps to the emit like
        # the pad approach; ROUTED phase falls through so the node machinery
        # moves the bot (drop_next_hop overrides the arrival next-hop below).
        a.raw(b'\x83\x3D' + le32(drop_enabled_va) + b'\x00')
        a.jz('s542360_drp_done')
        a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00')  # CTF match only
        a.jz('s542360_drp_done')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        # Tick the per-bot pursuit cooldown; while it runs, no pursuit at all
        # (after a grab so the stale pre-scan position can't re-latch, after
        # exhausted patience so the bot stops grinding an unreachable drop).
        a.raw(b'\x8B\x04\x8D' + le32(bot_drop_cd_va))     # eax = cd[slot]
        a.raw(b'\x85\xC0'); a.jz('s542360_drp_cd0')
        a.raw(b'\x48')                                    # dec eax
        a.raw(b'\x89\x04\x8D' + le32(bot_drop_cd_va))
        a.jmp('s542360_drp_done')
        a.label('s542360_drp_cd0')
        a.raw(b'\x8B\x04\x8D' + le32(bot_drop_target_va)) # eax = latch (idx+1 / 0)
        a.raw(b'\x85\xC0'); a.jnz('s542360_drp_have')
        # --- Entry scan: nearest valid away-flag drop. best is seeded with
        # FLT_MAX; each candidate passes the pursue-radius gate UNLESS it is
        # this bot's missing GOAL flag (route_missing_goal — the bot's whole
        # objective lies on the ground, so it latches from any distance).
        # ecx = slot survives the loop (only eax/esi/ebx/FPU are used).
        a.raw(b'\xBB\xFF\xFF\xFF\xFF')                    # ebx = -1
        a.raw(b'\xC7\x05' + le32(dx_accum_va) + le32(0x7F7FFFFF))
        a.raw(b'\xD9\x05' + le32(dx_accum_va))            # fld FLT_MAX (best)
        a.raw(b'\x31\xF6')                                # esi = 0 (flag i)
        a.label('s542360_drp_scan')
        a.raw(b'\x3B\x35' + le32(flag_count_mv_va))       # i >= flag_count?
        a.jae('s542360_drp_scan_pop')
        a.raw(b'\x83\xFE' + bytes([cfg.FLAG_TABLE_MAX]))  # i >= table max?
        a.jae('s542360_drp_scan_pop')
        a.raw(b'\x83\x3C\xB5' + le32(flag_drop_valid_mv_va) + b'\x00')
        a.jz('s542360_drp_scan_next')                     # no known drop
        a.raw(b'\x83\x3C\xB5' + le32(flag_present_mv_va) + b'\x00')
        a.jnz('s542360_drp_scan_next')                    # returned home meanwhile
        a.raw(b'\xD9\x04\xF5' + le32(flag_drop_pos_mv_va))     # fld drop.x
        a.raw(b'\xD8\x25' + le32(bot_pos_va))             # fsub bot.x
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xD9\x04\xF5' + le32(flag_drop_pos_mv_va + 4)) # fld drop.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))         # fsub bot.y
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xDE\xC1')                                # faddp -> ST0=dsq, ST1=best
        a.raw(b'\x3B\x34\x8D' + le32(drop_missing_goal_va))  # i == missing goal[slot]?
        a.jz('s542360_drp_scan_cmp')                      # objective -> no radius gate
        a.raw(b'\xD9\x05' + le32(drop_radius_va))         # fld radius (ST0=r, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip r:dsq (pop r)
        a.jb('s542360_drp_scan_skip')                     # r < dsq -> out of range
        a.label('s542360_drp_scan_cmp')
        a.raw(b'\xD8\xD1')                                # fcom st(1) (dsq:best)
        a.raw(b'\xDF\xE0'); a.raw(b'\x9E')                # fnstsw ax; sahf
        a.jae('s542360_drp_scan_skip')                    # dsq >= best -> keep best
        a.raw(b'\xD9\xC9')                                # fxch (ST0=best, ST1=dsq)
        a.raw(b'\xDD\xD8')                                # fstp st0 (pop old best)
        a.raw(b'\x89\xF3')                                # ebx = i (new best)
        a.jmp('s542360_drp_scan_next')
        a.label('s542360_drp_scan_skip')
        a.raw(b'\xDD\xD8')                                # fstp st0 (pop dsq)
        a.label('s542360_drp_scan_next')
        a.raw(b'\x46')                                    # ++i
        a.jmp('s542360_drp_scan')
        a.label('s542360_drp_scan_pop')
        a.raw(b'\xDD\xD8')                                # fstp st0 (pop best; FPU empty)
        a.raw(b'\x83\xFB\xFF')                            # candidate found?
        a.jz('s542360_drp_done')
        # LATCH: bot_drop_target = idx+1; direct-phase trackers parked (the
        # node watchdog fields wp_try/wp_best_dsq stay with the node logic —
        # the routed phase runs on them; direct entry resets its own below).
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8D\x43\x01')                            # lea eax, [ebx+1]
        a.raw(b'\x89\x04\x8D' + le32(bot_drop_target_va))
        a.raw(b'\xC7\x04\x8D' + le32(bot_drop_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(bot_drop_best_va) + le32(0x7F7FFFFF))
        # fall through with eax = idx+1
        a.label('s542360_drp_have')
        a.raw(b'\x48')                                    # eax = flag idx
        # Validate every think: stale idx (map change), drop consumed (scan
        # cleared it), or flag back home (event-instant) -> drop the latch.
        a.raw(b'\x3B\x05' + le32(flag_count_mv_va))
        a.jae('s542360_drp_clear')
        a.raw(b'\x83\x3C\x85' + le32(flag_drop_valid_mv_va) + b'\x00')
        a.jz('s542360_drp_clear')
        a.raw(b'\x83\x3C\x85' + le32(flag_present_mv_va) + b'\x00')
        a.jnz('s542360_drp_clear')
        # desired = drop position - bot (staged for the direct-phase emit)
        a.raw(b'\xD9\x04\xC5' + le32(flag_drop_pos_mv_va))     # fld drop.x [eax*8]
        a.raw(b'\xD8\x25' + le32(bot_pos_va))             # fsub bot.x
        a.raw(b'\xD9\x1D' + le32(dx_accum_va))            # fstp dx_accum
        a.raw(b'\xD9\x04\xC5' + le32(flag_drop_pos_mv_va + 4)) # fld drop.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))         # fsub bot.y
        a.raw(b'\xD9\x1D' + le32(dy_accum_va))            # fstp dy_accum
        a.raw(b'\xD9\x05' + le32(dx_accum_va))            # fld dx
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xD9\x05' + le32(dy_accum_va))            # fld dy
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xDE\xC1')                                # faddp -> ST0 = dsq(bot, drop)
        # Drifted far away (knockback, detour)? Opportunistic latches drop
        # silently; a bot whose OBJECTIVE is this flag routes from anywhere.
        a.raw(b'\x3B\x04\x8D' + le32(drop_missing_goal_va))  # idx == missing goal?
        a.jz('s542360_drp_phase')                         # objective -> keep
        a.raw(b'\xD9\x05' + le32(drop_abandon_va))        # fld abandon (ST0=a, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip a:dsq (pop a)
        a.jae('s542360_drp_phase')                        # a >= dsq -> still close enough
        a.raw(b'\xDD\xD8')                                # fstp st0 (drop dsq)
        a.jmp('s542360_drp_clear')
        a.label('s542360_drp_phase')                      # ST0 = dsq
        # Phase split: DIRECT iff within the direct radius, OR the bot's
        # target node IS the drop's bound node AND it has PHYSICALLY arrived
        # near it (within the stuck-arrival radius). The physical check is
        # load-bearing: `cur == drop node` alone fires the moment the routed
        # hop ASSIGNS that node — live ping-pong snapshots on Hydro caught a
        # bot fresh out of a teleport, still in the exit pocket 243 px away
        # with cur already advanced to the drop node, straight-steering a
        # line that grazed the return pad; the post-teleport veto wedged it
        # until the wall-slide sweep crossed the pad's trigger sliver — an
        # engine re-teleport, and the cross-arena latch routed it straight
        # back: an infinite teleport ping-pong. Until the bot genuinely
        # reaches the node, the ROUTED phase steers node-to-node along the
        # authored edge, which skirts the teleporter prop exactly like a
        # normal carrier leaving the exit pocket.
        a.raw(b'\xD9\x05' + le32(drop_direct_va))         # fld direct (ST0=d, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip d:dsq (pop d)
        a.jae('s542360_drp_direct')                       # d >= dsq -> inside
        a.raw(b'\x8B\x14\x8D' + le32(current_wp_va))      # edx = current_wp[slot]
        a.raw(b'\x3B\x14\x85' + le32(flag_drop_node_mv_va))  # cur == drop node?
        a.jnz('s542360_drp_routed')
        a.raw(b'\x8D\x14\xD5' + le32(overlay_vertices_va))  # lea edx, [edx*8 + verts]
        a.raw(b'\xD9\x02')                                # fld node.x
        a.raw(b'\xD8\x25' + le32(bot_pos_va))             # fsub bot.x
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xD9\x42\x04')                            # fld node.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))         # fsub bot.y
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xDE\xC1')                                # faddp -> ST0=node_dsq, ST1=drop dsq
        a.raw(b'\xD9\x05' + le32(wp_stuck_reached_radius_sq_va))  # fld arrival thr
        a.raw(b'\xDF\xF1')                                # fcomip thr:node_dsq (pop thr)
        a.raw(b'\xDD\xD8')                                # fstp st0 (pop node_dsq; EFLAGS kept)
        a.jae('s542360_drp_direct')                       # thr >= node_dsq -> arrived
        a.label('s542360_drp_routed')
        # ROUTED phase: the node machinery moves the bot (drop_next_hop
        # overrides its next-hop at each arrival). Park the direct trackers
        # so entering direct later starts fresh.
        a.raw(b'\xDD\xD8')                                # fstp st0 (drop dsq)
        a.raw(b'\xC7\x04\x8D' + le32(bot_drop_best_va) + le32(0x7F7FFFFF))
        a.raw(b'\xC7\x04\x8D' + le32(bot_drop_try_va) + le32(0))
        a.jmp('s542360_drp_done')
        a.label('s542360_drp_direct')                     # ST0 = dsq
        # Fresh direct entry (best still parked at FLT_MAX)? Reset wp_try so
        # a stale node-phase stall can't instantly time the pursuit out.
        a.raw(b'\x81\x3C\x8D' + le32(bot_drop_best_va) + le32(0x7F7FFFFF))
        a.jnz('s542360_drp_reach')
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.label('s542360_drp_reach')
        # Reached? The flag's own touch script consumes the copy on overlap
        # (same-team return / enemy pickup); end the pursuit with the grab
        # cooldown (longer than a scan interval so the stale position cannot
        # re-latch) and emit one last frame toward it.
        a.raw(b'\xD9\x05' + le32(drop_reached_va))        # fld reached (ST0=r, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip r:dsq (pop r)
        a.jb('s542360_drp_watch')                         # r < dsq -> keep going
        a.raw(b'\xDD\xD8')                                # fstp st0 (drop dsq)
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\xC7\x04\x8D' + le32(bot_drop_target_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(bot_drop_cd_va)
              + le32(cfg.CTF_DROP_GRAB_COOLDOWN_FRAMES))
        a.raw(b'\xC7\x04\x8D' + le32(bot_drop_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.jmp('s542360_emit')
        a.label('s542360_drp_watch')                      # ST0 = dsq
        # Direct-phase watchdog with PRESS PATIENCE (mirror of the door/pad
        # patience): strict dsq improvement (vs bot_drop_best, the pursuit's
        # own tracker) resets wp_try; stalling ramps it (drives the
        # wall-slide sweep at the emit); each full progress-timeout grants a
        # fresh cycle up to CTF_DROP_PRESS_PATIENCE before the retry
        # cooldown blacklists the pursuit. The v1 single 30-frame window was
        # the live-diagnosed "runs at it, then ignores it" loop.
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))     # eax = stuck_count[slot]
        a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))
        a.jae('s542360_drp_no_progress')                  # physically pinned
        a.raw(b'\xD9\x04\x8D' + le32(bot_drop_best_va))   # fld best (ST0=best, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip st0,st1 (best:dsq, pop)
        a.jbe('s542360_drp_no_progress')                  # best <= dsq -> no improvement
        a.raw(b'\xD9\x1C\x8D' + le32(bot_drop_best_va))   # best = dsq (pop)
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.jmp('s542360_emit')
        a.label('s542360_drp_no_progress')
        a.raw(b'\xDD\xD8')                                # fstp st0 (drop dsq)
        a.raw(b'\xFF\x04\x8D' + le32(wp_try_va))          # ++wp_try[slot]
        a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))          # eax = wp_try
        a.raw(b'\x3B\x05' + le32(wp_progress_timeout_va))
        a.jb('s542360_emit')                              # keep steering; slide sweeps
        a.raw(b'\xFF\x04\x8D' + le32(bot_drop_try_va))    # ++patience used
        a.raw(b'\x83\x3C\x8D' + le32(bot_drop_try_va)
              + bytes([cfg.CTF_DROP_PRESS_PATIENCE]))
        a.ja('s542360_drp_impatient')                     # budget exhausted
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))     # fresh cycle
        a.raw(b'\xC7\x04\x8D' + le32(bot_drop_best_va) + le32(0x7F7FFFFF))
        a.jmp('s542360_emit')                             # keep pressing
        a.label('s542360_drp_impatient')
        a.raw(b'\xC7\x04\x8D' + le32(bot_drop_cd_va)
              + le32(cfg.CTF_DROP_RETRY_COOLDOWN_FRAMES))
        a.label('s542360_drp_clear')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot (reload)
        a.raw(b'\xC7\x04\x8D' + le32(bot_drop_target_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(bot_drop_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # node logic starts clean
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.label('s542360_drp_done')
    if chase_move:
        # --- Enemy-carrier chase (validation + ROUTED/DIRECT phase split) --
        # Latched by the perception scan's LOS sighting (bot_chase_flag =
        # home flag idx+1); the shared intel chase_pos/node/ttl is serviced
        # per frame by chase_route_refresh. Runs after the pad and drop
        # latches (a flag on the ground outranks the carrier holding one);
        # outranks the goody/switch behaviours. ROUTED phase falls through —
        # the node machinery moves the bot and chase_next_hop overrides the
        # arrival next-hop; DIRECT phase (inside the direct radius or
        # physically at the carrier's bound node) steers straight at the
        # carrier. The target MOVES, so the direct-phase stall signal is
        # the PHYSICAL stuck detector — dsq improvement is meaningless
        # against a fleeing carrier (dsq grows while the chaser runs at
        # full speed). Killing the carrier drops the flag; the drop
        # pursuit takes over at the next think.
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        # Re-latch cooldown ticks once per think; while it runs, no chase
        # (armed by a pinned timeout so a wall-separated sighting cannot
        # grind; the fire-side stamp also respects it).
        a.raw(b'\x8B\x04\x8D' + le32(bot_chase_cd_va))    # eax = cd[slot]
        a.raw(b'\x85\xC0'); a.jz('s542360_chs_cd0')
        a.raw(b'\x48')                                    # dec eax
        a.raw(b'\x89\x04\x8D' + le32(bot_chase_cd_va))
        a.jmp('s542360_chs_done')
        a.label('s542360_chs_cd0')
        a.raw(b'\x8B\x04\x8D' + le32(bot_chase_flag_va))  # eax = latch (idx+1 / 0)
        a.raw(b'\x85\xC0'); a.jz('s542360_chs_done')
        if drop_move:
            # A dropped-flag pursuit outranks the chase — the flag on the
            # ground IS the prize. Hand over cleanly (no cooldown).
            a.raw(b'\x83\x3C\x8D' + le32(bot_drop_target_va) + b'\x00')
            a.jnz('s542360_chs_clear')
        a.raw(b'\x48')                                    # eax = flag idx
        # Validate every think: stale idx (map change), expired sighting
        # memory, or the flag back home (event-instant; no carrier exists).
        a.raw(b'\x83\xF8\x02'); a.jae('s542360_chs_clear')
        a.raw(b'\x3B\x05' + le32(chase_flag_count_va))
        a.jae('s542360_chs_clear')
        a.raw(b'\x83\x3C\x85' + le32(chase_ttl_mv_va) + b'\x00')
        a.jz('s542360_chs_clear')
        a.raw(b'\x83\x3C\x85' + le32(chase_flag_present_va) + b'\x00')
        a.jnz('s542360_chs_clear')
        # Own-carry gate: grabbing any flag ends the chase (deliver first).
        # idx survives the engine calls on the stack; pop does not touch
        # EFLAGS but the test runs after it anyway.
        a.raw(b'\x50')                                    # push idx
        a.raw(b'\x8B\x0D' + le32(layout.va('bot_char_tmp')))
        a.call_lbl('chr_carrying')                        # eax = 1 iff carrying
        a.raw(b'\x5A')                                    # pop edx (= flag idx)
        a.raw(b'\x85\xC0'); a.jnz('s542360_chs_clear')
        # desired = carrier last-seen pos - bot (staged for the direct emit)
        a.raw(b'\xD9\x04\xD5' + le32(chase_pos_mv_va))    # fld pos.x [edx*8]
        a.raw(b'\xD8\x25' + le32(bot_pos_va))             # fsub bot.x
        a.raw(b'\xD9\x1D' + le32(dx_accum_va))            # fstp dx_accum
        a.raw(b'\xD9\x04\xD5' + le32(chase_pos_mv_va + 4))  # fld pos.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))         # fsub bot.y
        a.raw(b'\xD9\x1D' + le32(dy_accum_va))            # fstp dy_accum
        a.raw(b'\xD9\x05' + le32(dx_accum_va))            # fld dx
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xD9\x05' + le32(dy_accum_va))            # fld dy
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xDE\xC1')                                # faddp -> ST0 = dsq
        a.raw(b'\xD9\x1D' + le32(chase_dsq_tmp_va))       # fstp dsq spill (FPU empty)
        # Radius gates as unsigned float-bit compares (both non-negative).
        a.raw(b'\xA1' + le32(chase_dsq_tmp_va))           # eax = dsq bits
        a.raw(b'\x3D' + struct.pack('<f', float(cfg.CTF_CHASE_ABANDON_RADIUS_SQ)))
        a.ja('s542360_chs_clear')                         # carrier outran us
        a.raw(b'\x3D' + struct.pack('<f', float(cfg.CTF_CHASE_DIRECT_RADIUS_SQ)))
        a.jbe('s542360_chs_direct')                       # close -> straight steer
        # Not inside the direct radius: DIRECT anyway iff the bot targets
        # the carrier's bound node AND has physically arrived near it (the
        # same load-bearing stuck-arrival gate as the drop pursuit — cur
        # alone fires the moment the routed hop ASSIGNS the node).
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x34\x8D' + le32(current_wp_va))      # esi = current_wp[slot]
        a.raw(b'\x3B\x34\x95' + le32(chase_node_mv_va))   # cur == carrier node?
        a.jnz('s542360_chs_routed')
        a.raw(b'\x8D\x34\xF5' + le32(overlay_vertices_va))  # lea esi, [esi*8 + verts]
        a.raw(b'\xD9\x06')                                # fld node.x
        a.raw(b'\xD8\x25' + le32(bot_pos_va))             # fsub bot.x
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xD9\x46\x04')                            # fld node.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))         # fsub bot.y
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xDE\xC1')                                # faddp -> ST0 = node_dsq
        a.raw(b'\xD9\x05' + le32(wp_stuck_reached_radius_sq_va))  # fld arrival thr
        a.raw(b'\xDF\xF1')                                # fcomip thr:node_dsq (pop thr)
        a.raw(b'\xDD\xD8')                                # fstp st0 (pop node_dsq; EFLAGS kept)
        a.jae('s542360_chs_direct')                       # thr >= node_dsq -> arrived
        a.label('s542360_chs_routed')
        # ROUTED: fall through — the node machinery steers node-to-node and
        # chase_next_hop descends the carrier row at each arrival.
        a.jmp('s542360_chs_done')
        a.label('s542360_chs_direct')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))     # eax = stuck_count[slot]
        a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))
        a.jae('s542360_chs_pinned')                       # physically pinned
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))  # moving -> no sweep
        a.jmp('s542360_emit')
        a.label('s542360_chs_pinned')
        a.raw(b'\xFF\x04\x8D' + le32(wp_try_va))          # ++wp_try (drives the sweep)
        a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))          # eax = wp_try
        a.raw(b'\x3B\x05' + le32(wp_progress_timeout_va))
        a.jb('s542360_emit')                              # keep pressing; slide sweeps
        # Pinned a full watchdog window (wall micro-feature / body-block):
        # give up this chase and arm the re-latch cooldown. Fire targeting
        # is independent — the bot keeps shooting the carrier if visible.
        a.raw(b'\xC7\x04\x8D' + le32(bot_chase_cd_va)
              + le32(cfg.CTF_CHASE_COOLDOWN_FRAMES))
        a.label('s542360_chs_clear')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot (reload)
        a.raw(b'\xC7\x04\x8D' + le32(bot_chase_flag_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # node logic starts clean
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.label('s542360_chs_done')
    if goody_move:
        # --- Goody pursuit: piles + filler items, TWO-PHASE graph-routed ----
        # (upgrade of the straight-steer pile divert, which ground walls when
        # a pile registered across one — user-reported; same class of bug as
        # the CTF drop-pursuit v1). Runs after the pad and (mutually
        # exclusive by mode) drop latches; outranks the switch bump. The
        # latch bot_pile_target holds the pursuit KIND: 0 none, 1 pile,
        # 2+category filler item. Each think the live target is RE-RESOLVED
        # (nearest live pile / nearest item of the latched category via the
        # goody_scan_* helpers -> goody_tx/ty/node/idx), so a category
        # descent that reaches a closer same-kind item simply takes it.
        # ROUTED phase: park the trackers and fall through — sk_next_hop
        # descends the matching multi-source field at each node arrival,
        # walls are routed AROUND, pads hop like every other descent.
        # DIRECT phase (within goody_direct_radius_sq, or physically at the
        # target's bound node): straight steer through the standard
        # watchdog + press patience. Reaching a pile consumes its ring slot
        # (+ pile-field rebuild) with the grab cooldown; a reached item
        # takes the longer item cooldown (the engine consumed it on
        # overlap; the anchor respawns in 10-15 s).
        a.raw(b'\x83\x3D' + le32(sk_active_mv_va) + b'\x00')
        a.jnz('s542360_gd_on')
        a.raw(b'\x83\x3D' + le32(item_active_mv_va) + b'\x00')
        a.jz('s542360_gd_done')
        a.label('s542360_gd_on')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        # Shared goody cooldown ticks once per think; while it runs there is
        # no latch (every path arming it also clears the latch).
        a.raw(b'\x8B\x04\x8D' + le32(bot_pile_cd_va))     # eax = cd[slot]
        a.raw(b'\x85\xC0'); a.jz('s542360_gd_cd0')
        a.raw(b'\x48')                                    # dec eax
        a.raw(b'\x89\x04\x8D' + le32(bot_pile_cd_va))
        a.jmp('s542360_gd_done')
        a.label('s542360_gd_cd0')
        if (cfg.CTF_CARRIER_ESCAPE_ENABLED
                and layout.has_field('bot_carry')):
            # Carrier ESCAPE priority: no goody diverts while carrying — a
            # damaged carrier must not detour to health packs mid-escape.
            # An existing latch (flag grabbed mid-divert) is dropped ONCE
            # via gd_clear (which also resets the node watchdog); when
            # nothing is latched the whole block is skipped so the resets
            # cannot run every think and starve the wall-slide.
            a.raw(b'\x83\x3C\x8D' + le32(layout.va('bot_carry')) + b'\x00')
            a.jz('s542360_gd_nocarry')
            a.raw(b'\x83\x3C\x8D' + le32(bot_pile_target_va) + b'\x00')
            a.jz('s542360_gd_done')                       # nothing latched -> skip
            a.jmp('s542360_gd_clear')                     # latched -> unlatch once
            a.label('s542360_gd_nocarry')
        if (cfg.ITEM_NEED_GATE_ENABLED
                and layout.has_field('goody_need_mask')):
            # Refresh the pickup-need mask (health/energy/shield bits from
            # the bot's live state — the engine's own pickup-useful
            # predicates) before this think's entry/resolve scans consult
            # it. The helper clobbers ecx; reload slot.
            a.call_lbl('goody_update_need')
            a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))    # ecx = slot (reload)
        a.raw(b'\x8B\x04\x8D' + le32(bot_pile_target_va)) # eax = kind (0 = none)
        a.raw(b'\x85\xC0'); a.jnz('s542360_gd_have')
        if drop_move:
            # A live CTF dropped-flag pursuit outranks any goody entry.
            a.raw(b'\x83\x3C\x8D' + le32(bot_drop_target_va) + b'\x00')
            a.jnz('s542360_gd_done')
        if chase_move:
            # So does an enemy-carrier chase (combat beats snacks).
            a.raw(b'\x83\x3C\x8D' + le32(bot_chase_flag_va) + b'\x00')
            a.jnz('s542360_gd_done')
        # ENTRY. Piles first (SK matches only), then fillers (any mode).
        a.raw(b'\x83\x3D' + le32(sk_active_mv_va) + b'\x00')
        a.jz('s542360_gd_entry_items')
        a.raw(b'\xA1' + le32(sk_pile_radius_va))          # radius = pile pursue
        a.raw(b'\xA3' + le32(goody_scan_rad_va))
        a.call_lbl('goody_scan_piles')                    # ebx = idx or -1
        a.raw(b'\x83\xFB\xFF'); a.jz('s542360_gd_entry_items')
        a.raw(b'\xB8\x01\x00\x00\x00')                    # kind = 1 (pile)
        a.jmp('s542360_gd_latch')
        a.label('s542360_gd_entry_items')
        a.raw(b'\x83\x3D' + le32(item_active_mv_va) + b'\x00')
        a.jz('s542360_gd_done')
        a.raw(b'\xA1' + le32(item_radius_mv_va))          # radius = item pursue
        a.raw(b'\xA3' + le32(goody_scan_rad_va))
        a.raw(b'\xC7\x05' + le32(goody_scan_cat_va) + b'\xFF\xFF\xFF\xFF')  # any cat
        a.call_lbl('goody_scan_items')                    # ebx = idx or -1
        a.raw(b'\x83\xFB\xFF'); a.jz('s542360_gd_done')
        a.raw(b'\x8B\x04\x9D' + le32(item_cat_mv_va))     # eax = item_cat[idx]
        a.raw(b'\x83\xC0\x02')                            # kind = category + 2
        a.label('s542360_gd_latch')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x89\x04\x8D' + le32(bot_pile_target_va)) # latch = kind
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_best_va) + le32(0x7F7FFFFF))
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))   # fresh watchdog
        # fall through with eax = kind
        a.label('s542360_gd_have')
        # RESOLVE this think's live target. Unlimited scan radius — the
        # abandon check below owns the give-up distance (routed paths
        # legitimately move AWAY from the target around walls).
        a.raw(b'\x50')                                    # push kind
        a.raw(b'\xC7\x05' + le32(goody_scan_rad_va) + le32(0x7F7FFFFF))
        a.raw(b'\x83\xF8\x01')                            # pile kind?
        a.jnz('s542360_gd_res_item')
        a.call_lbl('goody_scan_piles')
        a.jmp('s542360_gd_res_done')
        a.label('s542360_gd_res_item')
        a.raw(b'\x83\xE8\x02')                            # eax = category
        a.raw(b'\xA3' + le32(goody_scan_cat_va))
        a.call_lbl('goody_scan_items')
        a.label('s542360_gd_res_done')
        a.raw(b'\x58')                                    # pop eax (kind; unused below)
        a.raw(b'\x83\xFB\xFF')                            # any target left?
        a.jz('s542360_gd_clear')                          # all gone -> unlatch
        # desired = goody target - bot (staged for the direct emit)
        a.raw(b'\xD9\x05' + le32(goody_tx_va))            # fld target.x
        a.raw(b'\xD8\x25' + le32(bot_pos_va))             # fsub bot.x
        a.raw(b'\xD9\x1D' + le32(dx_accum_va))            # fstp dx_accum
        a.raw(b'\xD9\x05' + le32(goody_ty_va))            # fld target.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))         # fsub bot.y
        a.raw(b'\xD9\x1D' + le32(dy_accum_va))            # fstp dy_accum
        a.raw(b'\xD9\x05' + le32(dx_accum_va))            # fld dx
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xD9\x05' + le32(dy_accum_va))            # fld dy
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xDE\xC1')                                # faddp -> ST0 = dsq(bot, target)
        # Drifted out of business range? Silently unlatch.
        a.raw(b'\xD9\x05' + le32(goody_abandon_va))       # fld abandon (ST0=a, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip a:dsq (pop a)
        a.jae('s542360_gd_keep')                          # a >= dsq -> in range
        a.raw(b'\xDD\xD8')                                # fstp st0 (drop dsq)
        a.jmp('s542360_gd_clear')
        a.label('s542360_gd_keep')                        # ST0 = dsq
        # Reached? (pile touch script / item pickup consumed on overlap)
        a.raw(b'\xD9\x05' + le32(sk_pile_reached_va))     # fld reached (ST0=r, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip r:dsq (pop r)
        a.jb('s542360_gd_split')                          # r < dsq -> keep going
        a.raw(b'\xDD\xD8')                                # fstp st0 (drop dsq)
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x83\x3C\x8D' + le32(bot_pile_target_va) + b'\x01')  # pile kind?
        a.jnz('s542360_gd_reach_item')
        a.raw(b'\xA1' + le32(goody_idx_va))               # eax = ring slot
        a.raw(b'\x83\xF8' + bytes([cfg.SK_PILE_TABLE_MAX]))
        a.jae('s542360_gd_reach_item')                    # defensive range
        a.raw(b'\xC7\x04\x85' + le32(sk_pile_valid_mv_va) + le32(0))  # consume entry
        a.raw(b'\xC7\x05' + le32(sk_pile_dirty_mv_va) + le32(1))      # rebuild field
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_cd_va)
              + le32(cfg.SK_PILE_GRAB_COOLDOWN_FRAMES))
        a.jmp('s542360_gd_reach_done')
        a.label('s542360_gd_reach_item')
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_cd_va)
              + le32(cfg.ITEM_GRAB_COOLDOWN_FRAMES))
        a.label('s542360_gd_reach_done')
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_target_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.jmp('s542360_emit')
        a.label('s542360_gd_split')                       # ST0 = dsq
        # Phase split — DIRECT iff within the direct radius, OR the target's
        # bound node is the current node AND the bot has PHYSICALLY arrived
        # near it (stuck-arrival radius; the same load-bearing gate as the
        # drop pursuit — cur alone fires the moment the routed hop ASSIGNS
        # the node).
        a.raw(b'\xD9\x05' + le32(goody_direct_va))        # fld direct (ST0=d, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip d:dsq (pop d)
        a.jae('s542360_gd_direct')                        # d >= dsq -> inside
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x14\x8D' + le32(current_wp_va))      # edx = current_wp[slot]
        a.raw(b'\x3B\x15' + le32(goody_node_va))          # cur == target node?
        a.jnz('s542360_gd_routed')
        a.raw(b'\x8D\x14\xD5' + le32(overlay_vertices_va))  # lea edx, [edx*8 + verts]
        a.raw(b'\xD9\x02')                                # fld node.x
        a.raw(b'\xD8\x25' + le32(bot_pos_va))             # fsub bot.x
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xD9\x42\x04')                            # fld node.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))         # fsub bot.y
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xDE\xC1')                                # faddp -> ST0=node_dsq, ST1=dsq
        a.raw(b'\xD9\x05' + le32(wp_stuck_reached_radius_sq_va))  # fld arrival thr
        a.raw(b'\xDF\xF1')                                # fcomip thr:node_dsq (pop thr)
        a.raw(b'\xDD\xD8')                                # fstp st0 (pop node_dsq; EFLAGS kept)
        a.jae('s542360_gd_direct')                        # thr >= node_dsq -> arrived
        a.label('s542360_gd_routed')
        # ROUTED: the node machinery moves the bot (sk_next_hop descends the
        # pursuit field at each arrival). Park the direct trackers.
        a.raw(b'\xDD\xD8')                                # fstp st0 (drop dsq)
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_best_va) + le32(0x7F7FFFFF))
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_try_va) + le32(0))
        a.jmp('s542360_gd_done')
        a.label('s542360_gd_direct')                      # ST0 = dsq
        # Fresh direct entry (best still parked)? Reset wp_try so a stale
        # node-phase stall can't instantly time the pursuit out.
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x81\x3C\x8D' + le32(bot_pile_best_va) + le32(0x7F7FFFFF))
        a.jnz('s542360_gd_watch')
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.label('s542360_gd_watch')
        # Watchdog with press patience (exact mirror of the drop direct
        # phase, on the pursuit's own progress tracker).
        a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))     # eax = stuck_count[slot]
        a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))
        a.jae('s542360_gd_no_progress')                   # physically pinned
        a.raw(b'\xD9\x04\x8D' + le32(bot_pile_best_va))   # fld best (ST0=best, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip st0,st1 (best:dsq, pop)
        a.jbe('s542360_gd_no_progress')                   # best <= dsq -> no improvement
        a.raw(b'\xD9\x1C\x8D' + le32(bot_pile_best_va))   # best = dsq (pop)
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.jmp('s542360_emit')
        a.label('s542360_gd_no_progress')
        a.raw(b'\xDD\xD8')                                # fstp st0 (drop dsq)
        a.raw(b'\xFF\x04\x8D' + le32(wp_try_va))          # ++wp_try[slot]
        a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))          # eax = wp_try
        a.raw(b'\x3B\x05' + le32(wp_progress_timeout_va))
        a.jb('s542360_emit')                              # keep steering; slide sweeps
        a.raw(b'\xFF\x04\x8D' + le32(bot_pile_try_va))    # ++patience used
        a.raw(b'\x83\x3C\x8D' + le32(bot_pile_try_va)
              + bytes([cfg.SK_PILE_PRESS_PATIENCE]))
        a.ja('s542360_gd_impatient')                      # budget exhausted
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))    # fresh cycle
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_best_va) + le32(0x7F7FFFFF))
        a.jmp('s542360_emit')                             # keep pressing
        a.label('s542360_gd_impatient')
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_cd_va)
              + le32(cfg.SK_PILE_RETRY_COOLDOWN_FRAMES))
        a.label('s542360_gd_clear')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot (reload)
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_target_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(bot_pile_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # node logic starts clean
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.label('s542360_gd_done')
    if switch_wander:
        # --- Roam switch bump approach (latch-driven) ----------------------
        # Runs after the pad/drop latches (both outrank a bump). Fast path
        # when unlatched and off-cooldown: three loads + two jz.
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        # Re-roll cooldown ticks once per think; while it runs there is no
        # latch (every path that arms the cooldown also clears the latch).
        a.raw(b'\x8B\x04\x8D' + le32(bot_switch_cd_va))   # eax = cd[slot]
        a.raw(b'\x85\xC0'); a.jz('s542360_sww_cd0')
        a.raw(b'\x48')                                    # dec eax
        a.raw(b'\x89\x04\x8D' + le32(bot_switch_cd_va))
        a.jmp('s542360_sww_done')
        a.label('s542360_sww_cd0')
        a.raw(b'\x8B\x04\x8D' + le32(bot_switch_target_va))  # eax = latch (idx+1 / 0)
        a.raw(b'\x85\xC0'); a.jz('s542360_sww_done')
        if drop_move:
            # A dropped-flag pursuit latched meanwhile outranks the bump —
            # hand over cleanly (no cooldown: the bump never ran).
            a.raw(b'\x83\x3C\x8D' + le32(bot_drop_target_va) + b'\x00')
            a.jnz('s542360_sww_clear')
        if chase_move:
            # An enemy-carrier chase also outranks the bump.
            a.raw(b'\x83\x3C\x8D' + le32(bot_chase_flag_va) + b'\x00')
            a.jnz('s542360_sww_clear')
        if (cfg.CTF_CARRIER_ESCAPE_ENABLED
                and layout.has_field('bot_carry')):
            # Carrier ESCAPE priority: hand over an in-progress press the
            # think the flag is grabbed (no cooldown — the bump never ran).
            a.raw(b'\x83\x3C\x8D' + le32(layout.va('bot_carry')) + b'\x00')
            a.jnz('s542360_sww_clear')
        a.raw(b'\x48')                                    # eax = switch idx
        a.raw(b'\x3B\x05' + le32(switch_count_sw_va))     # stale idx (map change)?
        a.jae('s542360_sww_clear')
        # Census changed since latch -> the bump fired (a paired door opened,
        # or a toggler flipped its set). Back off with the full cooldown so
        # continued pressing cannot re-toggle the doors shut.
        a.raw(b'\x50')                                    # push eax (idx)
        a.raw(b'\x89\xC1')                                # ecx = idx
        a.call_lbl('switch_blocked_census')               # eax = census (clobbers ebx/edx/esi)
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot (reload)
        a.raw(b'\x3B\x04\x8D' + le32(bot_switch_snap_va)) # census == snapshot?
        a.raw(b'\x58')                                    # pop eax (idx; EFLAGS kept)
        a.jnz('s542360_sww_backoff')
        # desired = switch center - bot
        a.raw(b'\xD9\x04\xC5' + le32(switch_table_sw_va)) # fld switch.x
        a.raw(b'\xD8\x25' + le32(bot_pos_va))             # fsub bot.x
        a.raw(b'\xD9\x1D' + le32(dx_accum_va))            # fstp dx_accum
        a.raw(b'\xD9\x04\xC5' + le32(switch_table_sw_va + 4))  # fld switch.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))         # fsub bot.y
        a.raw(b'\xD9\x1D' + le32(dy_accum_va))            # fstp dy_accum
        # Watchdog — mirror of the pad approach: strict dsq improvement
        # resets wp_try; stalling ramps it (drives the wall-slide sweep at
        # the emit); each full progress-timeout grants a fresh cycle up to
        # SWITCH_WANDER_PRESS_PATIENCE before the cooldown blacklists the
        # attempt (the prop is collidable, so the final pixels never
        # "arrive" — patience, not arrival, ends a successful press).
        a.raw(b'\xD9\x05' + le32(dx_accum_va))            # fld dx
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xD9\x05' + le32(dy_accum_va))            # fld dy
        a.raw(b'\xD8\xC8')                                # fmul st,st
        a.raw(b'\xDE\xC1')                                # faddp -> ST0 = dsq(bot, switch)
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))     # eax = stuck_count[slot]
        a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))
        a.jae('s542360_sww_no_progress')                  # physically pinned
        a.raw(b'\xD9\x04\x8D' + le32(wp_best_dsq_va))     # fld best (ST0=best, ST1=dsq)
        a.raw(b'\xDF\xF1')                                # fcomip st0,st1 (best:dsq, pop)
        a.jbe('s542360_sww_no_progress')                  # best <= dsq -> no improvement
        a.raw(b'\xD9\x1C\x8D' + le32(wp_best_dsq_va))     # best = dsq (pop)
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.jmp('s542360_emit')
        a.label('s542360_sww_no_progress')
        a.raw(b'\xDD\xD8')                                # fstp st(0) (drop dsq)
        a.raw(b'\xFF\x04\x8D' + le32(wp_try_va))          # ++wp_try[slot]
        a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))          # eax = wp_try
        a.raw(b'\x3B\x05' + le32(wp_progress_timeout_va))
        a.jb('s542360_emit')                              # keep pressing; slide sweeps
        a.raw(b'\xFF\x04\x8D' + le32(bot_switch_try_va))  # ++patience used
        a.raw(b'\x83\x3C\x8D' + le32(bot_switch_try_va)
              + bytes([cfg.SWITCH_WANDER_PRESS_PATIENCE]))
        a.ja('s542360_sww_backoff')                       # budget exhausted
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))     # fresh cycle
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
        a.jmp('s542360_emit')                             # keep pressing
        a.label('s542360_sww_backoff')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot (reload)
        a.raw(b'\xC7\x04\x8D' + le32(bot_switch_cd_va)
              + le32(cfg.SWITCH_WANDER_COOLDOWN_FRAMES))
        a.label('s542360_sww_clear')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot (reload)
        a.raw(b'\xC7\x04\x8D' + le32(bot_switch_target_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(bot_switch_try_va) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # node logic starts clean
        a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))
        a.label('s542360_sww_done')
