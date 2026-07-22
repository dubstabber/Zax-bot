"""Stages 0-1: bot-controller identify + per-tick setup, and the
position-delta stuck detector (teleport-jump re-acquire included)."""

from ... import addresses as ax
from ... import config as cfg
from ...asm import Asm, le32
from ...hook.bot_lookup import emit_addr_to_slot, emit_is_bot_controller
from ...layout import ScratchLayout


def _emit_identify_and_setup(a: Asm, layout: ScratchLayout) -> None:
    """Entry: classify the controller, set up the pushad frame, fetch the live
    bot char, reset nav on respawn, and read the bot's world position."""
    bot_pos_va        = layout.va('bot_pos')
    bot_slot_tmp_va   = layout.va('bot_slot_tmp')
    bot_char_tmp_va   = layout.va('bot_char_tmp')
    current_wp_va     = layout.va('bot_current_wp')
    prev_wp_va        = layout.va('bot_prev_wp')
    wp_try_va         = layout.va('bot_wp_try')
    wp_best_dsq_va    = layout.va('bot_pickup_y_cache')   # min dsq-to-node
    bot_last_char_va  = layout.va('bot_pickup_x_cache')   # respawn detection
    failed_edge_va    = layout.va('bot_pickup_valid')     # packed failed-edge marker
    slide_turn_va     = layout.va('bot_flee_ticks')       # wall-slide ramp
    pickup_div_active_va = layout.va('pickup_div_active')
    pickup_cd_va         = layout.va('pickup_cd')
    bot_last_damage_va   = layout.va('bot_last_damage')
    frame_counter_va     = layout.va('frame_counter')
    movement_enabled_va  = layout.va('movement_enabled')

    a.label('detour_542360')
    emit_is_bot_controller(a, layout,
                           on_not_bot='s542360_normal',
                           label_prefix='s542360')

    emit_addr_to_slot(a, layout)                          # eax = slot
    a.raw(b'\xA3' + le32(bot_slot_tmp_va))                # save slot
    # Force-tick handshake: mark this bot as ticked-by-the-engine this frame so
    # the page-flip force-tick loop won't double-tick it (it only force-ticks
    # bots the engine SKIPPED — those far from the host's camera). The page-flip
    # resets this flag each frame. Reuses the dormant per-bot bot_last_item_scan.
    if cfg.BOT_FORCE_TICK_ENABLED and layout.has_field('bot_last_item_scan'):
        a.raw(b'\x83\x3C\x85' + le32(layout.va('bot_last_item_scan')) + b'\x02')
        a.jz('s542360_tick_marked')                       # recovery tick: keep sentinel
        a.raw(b'\xC7\x04\x85' + le32(layout.va('bot_last_item_scan'))
              + le32(1))                                  # bot_ticked[slot] = 1  ([..+eax*4])
        a.label('s542360_tick_marked')
    # The engine's caller (sub_543B60 at 0x543CF2) reads [EBX+0x9C] after we
    # return, so callee-saved regs must survive. pushad covers all 8 GPRs;
    # downstream `[esp+4]`/`[esp+8]` arg reads bump to `[esp+0x24]`/`[esp+0x28]`.
    a.raw(b'\x60')                                        # pushad

    a.raw(b'\xFF\x05' + le32(frame_counter_va))           # ++frame_counter

    # Panic switch.
    a.raw(b'\x83\x3D' + le32(movement_enabled_va) + b'\x00')
    a.jz('s542360_zero')

    # Live char fetch from mgr+0x290[bot_indices[slot]] rather than a cache:
    # when a bot dies the engine clears its slot but a cache would still hold
    # the stale pointer, and sub_4FB0A0 on freed memory crashes (EIP=0).
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x14\x8D' + le32(layout.va('bot_indices')))  # edx = bot_indices[slot]
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))           # eax = [mgr]
    a.raw(b'\x85\xC0'); a.jz('s542360_zero')              # mgr NULL
    a.raw(b'\x8B\x80\x90\x02\x00\x00')                    # eax = [mgr + 0x290]
    a.raw(b'\x85\xC0'); a.jz('s542360_zero')              # array NULL
    a.raw(b'\x8B\x14\x90')                                # edx = chars[idx]
    a.raw(b'\x85\xD2'); a.jz('s542360_wp_mark_dead')      # char NULL -> dead this frame
    a.raw(b'\x89\x15' + le32(bot_char_tmp_va))

    # --- Respawn detection: if the engine replaced the char (death->respawn or
    # first spawn), drop the latch so the bot re-acquires the nearest node.
    # ecx = slot, edx = live char (both live from the fetch above).
    a.raw(b'\x8B\x04\x8D' + le32(bot_last_char_va))       # eax = bot_last_char[slot]
    a.raw(b'\x39\xD0')                                    # cmp eax, edx
    a.jz('s542360_char_same')
    a.raw(b'\xC7\x04\x8D' + le32(current_wp_va) + b'\xFF\xFF\xFF\xFF')  # current_wp = -1
    a.raw(b'\xC7\x04\x8D' + le32(prev_wp_va) + b'\xFF\xFF\xFF\xFF')     # prev_wp    = -1
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))    # best_dsq   = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))                  # wp_try     = 0
    a.raw(b'\xC7\x04\x8D' + le32(failed_edge_va) + le32(0))             # failed_edge_marker = 0
    a.raw(b'\xC7\x04\x8D' + le32(slide_turn_va) + le32(0))             # slide_turn = 0
    a.raw(b'\xC7\x04\x8D' + le32(pickup_div_active_va) + le32(0))      # drop any pickup divert
    a.raw(b'\xC7\x04\x8D' + le32(pickup_cd_va) + le32(0))             # clear divert cooldown
    a.raw(b'\xC7\x04\x8D' + le32(bot_last_damage_va) + le32(0))        # reset cur_damage tracker
    a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_wander_ticks')) + le32(0))  # reset lava-flee countdown
    if layout.has_field('bot_route_suspend'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_route_suspend')) + le32(0))  # respawn = fresh routing
    if layout.has_field('route_block_hits'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('route_block_hits')) + le32(0))  # reset blocked-edge retry count
    if layout.has_field('bot_seek'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_seek')) + le32(0))  # drop seek participation
    if layout.has_field('bot_wedge_cycles'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_wedge_cycles')) + le32(0))  # fresh wedge counter
    if layout.has_field('bot_portal_target'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_portal_target')) + le32(0))  # drop pad approach
    if layout.has_field('bot_portal_cd'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_portal_cd')) + le32(0))  # fresh wander cooldown
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_pad_try')) + le32(0))    # fresh pad patience
    if layout.has_field('bot_drop_target'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_drop_target')) + le32(0))  # drop dropped-flag pursuit
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_drop_cd')) + le32(0))      # fresh pursuit cooldown
        if layout.has_field('bot_drop_try'):
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_drop_try')) + le32(0))  # fresh press patience
    if layout.has_field('bot_switch_target'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_switch_target')) + le32(0))  # drop switch bump
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_switch_cd')) + le32(0))      # fresh roll cooldown
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_switch_try')) + le32(0))     # fresh press patience
    if layout.has_field('bot_chase_flag'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_chase_flag')) + le32(0))  # drop carrier chase
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_chase_cd')) + le32(0))    # fresh chase cooldown
    if layout.has_field('bot_carry'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_carry')) + le32(0))  # a respawn carries nothing
    if layout.has_field('bot_sk_return'):
        # Fresh SK phase state: a respawned bot carries nothing (the death
        # drop consumed the load), so it starts in COLLECT with clean
        # deposit patience and no pile divert.
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_sk_return')) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_sk_dep_try')) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_pile_target')) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_pile_cd')) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_pile_try')) + le32(0))
    a.raw(b'\x89\x14\x8D' + le32(bot_last_char_va))       # bot_last_char[slot] = edx
    a.label('s542360_char_same')

    # Read bot position into bot_pos.
    a.raw(b'\x68' + le32(bot_pos_va))                     # push &bot_pos
    a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))            # ecx = bot char
    a.call_va(ax.SUB_4FB0A0_VA)                           # __thiscall, ret 4


def _emit_stuck_detection(a: Asm, layout: ScratchLayout) -> None:
    """d² between current and last position. Drives the wall-slide ramp (a
    wedged bot makes no progress so this climbs) and the pickup-divert
    wall-wedge abandon. On portal-routing builds the same d² also feeds the
    TELEPORT-JUMP detector: a move bigger than portal_jump_sq in one think can
    only be a teleport (or an engine relocate), so the whole nav latch is
    dropped and the follower cold-acquires the NEAREST node at the exit point
    this very think — the post-teleport re-acquire the portal feature needs,
    and it also catches bots knocked through script teleporters they never
    chose."""
    bot_pos_va       = layout.va('bot_pos')
    bot_slot_tmp_va  = layout.va('bot_slot_tmp')
    last_x_va        = layout.va('bot_last_x')
    last_y_va        = layout.va('bot_last_y')
    stuck_count_va   = layout.va('bot_stuck_count')
    stuck_delta_sq_va = layout.va('stuck_delta_sq')
    tp_jump = layout.has_field('tp_jump_d2') and layout.has_field('portal_jump_sq')

    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xD9\x05' + le32(bot_pos_va))                 # fld pos.x
    a.raw(b'\xD8\x24\x8D' + le32(last_x_va))              # fsub last_x[slot]
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xD9\x05' + le32(bot_pos_va + 4))             # fld pos.y
    a.raw(b'\xD8\x24\x8D' + le32(last_y_va))              # fsub last_y[slot]
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0 = d²
    if tp_jump:
        a.raw(b'\xD9\x15' + le32(layout.va('tp_jump_d2')))  # fst tp_jump_d2 (keeps ST0)
    a.raw(b'\xD8\x1D' + le32(stuck_delta_sq_va))          # fcomp threshold (pops)
    a.raw(b'\xDF\xE0')                                    # fnstsw ax
    a.raw(b'\x9E')                                        # sahf
    a.jae('s542360_not_stuck')                            # d² >= threshold -> moved
    a.raw(b'\xFF\x04\x8D' + le32(stuck_count_va))         # ++stuck_count[slot]
    a.jmp('s542360_stuck_done')
    a.label('s542360_not_stuck')
    a.raw(b'\xC7\x04\x8D' + le32(stuck_count_va) + le32(0))
    a.label('s542360_stuck_done')
    a.raw(b'\xA1' + le32(bot_pos_va))                     # refresh last position
    a.raw(b'\x89\x04\x8D' + le32(last_x_va))
    a.raw(b'\xA1' + le32(bot_pos_va + 4))
    a.raw(b'\x89\x04\x8D' + le32(last_y_va))
    if tp_jump and layout.has_field('bot_portal_cd'):
        # Post-teleport wander-entry cooldown ticks down once per think.
        a.raw(b'\x8B\x04\x8D' + le32(layout.va('bot_portal_cd')))  # eax = cd[slot]
        a.raw(b'\x85\xC0'); a.jz('s542360_tp_cd0')
        a.raw(b'\x48')                                    # dec eax
        a.raw(b'\x89\x04\x8D' + le32(layout.va('bot_portal_cd')))
        a.label('s542360_tp_cd0')
    if tp_jump:
        # Teleport-jump detect. Both values are non-negative IEEE floats, so
        # the raw bit patterns compare correctly as unsigned ints — no FPU
        # needed. The first think after a match change sees last=(0,0) and a
        # huge delta; the resets below are all idempotent with the fresh
        # state df90 just wrote, so no special-casing. route_suspend is left
        # alone deliberately (a suspension must survive being teleported).
        a.raw(b'\xA1' + le32(layout.va('tp_jump_d2')))    # eax = d² bits
        a.raw(b'\x3B\x05' + le32(layout.va('portal_jump_sq')))
        a.jbe('s542360_tp_done')                          # normal movement
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_current_wp')) + b'\xFF\xFF\xFF\xFF')
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_prev_wp')) + b'\xFF\xFF\xFF\xFF')
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_pickup_y_cache')) + le32(0x7F7FFFFF))  # wp_best_dsq
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_wp_try')) + le32(0))
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_pickup_valid')) + le32(0))  # failed-edge marker
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_flee_ticks')) + le32(0))    # slide_turn
        a.raw(b'\xC7\x04\x8D' + le32(stuck_count_va) + le32(0))
        if layout.has_field('bot_wedge_cycles'):
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_wedge_cycles')) + le32(0))  # new area, fresh counter
        if layout.has_field('bot_portal_target'):
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_portal_target')) + le32(0))
        if layout.has_field('bot_drop_target'):
            # A teleported bot's latched dropped flag is usually a whole arena
            # away now — drop the pursuit (the entry scan re-latches if it is
            # genuinely still nearby at the exit).
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_drop_target')) + le32(0))
        if layout.has_field('bot_switch_target'):
            # Same for a latched switch bump — the switch is an arena away.
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_switch_target')) + le32(0))
        if layout.has_field('bot_pile_target'):
            # Same for a latched SK pile divert.
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_pile_target')) + le32(0))
        if layout.has_field('bot_chase_flag'):
            # And a latched enemy-carrier chase — the carrier is an arena
            # away now; a fresh sighting re-latches if it is genuinely near.
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_chase_flag')) + le32(0))
        if layout.has_field('bot_portal_cd'):
            # Teleported: arm the wander-entry cooldown so the roam roll at
            # the exit node (which IS the return pad's node) can't bounce the
            # bot straight back, and reset the pad-press patience budget.
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_portal_cd'))
                  + le32(cfg.PORTAL_WANDER_COOLDOWN_FRAMES))
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_pad_try')) + le32(0))
        if layout.has_field('route_block_door'):
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('route_block_door')) + b'\xFF\xFF\xFF\xFF')
        a.label('s542360_tp_done')


