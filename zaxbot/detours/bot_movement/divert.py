"""Stages 2-3: reactive lava flee and the (dormant) pickup divert."""

from ... import addresses as ax
from ...asm import Asm, le32
from ...layout import ScratchLayout
from .tuning import WP_SLIDE_TRIGGER_FRAMES


def _emit_reactive_lava_flee(a: Asm, layout: ScratchLayout) -> None:
    """Reactive hazard response, keyed off HEALTH damage (Cur Damage at
    char+0x7C rising — shield is bypassed by lava, and is gone by the time a
    shield-first barrier finally chips health). The response depends on whether
    the bot is making waypoint progress:

      * NOT progressing (``wp_try >= WP_SLIDE_TRIGGER_FRAMES``) => the bot is
        wedged against an impassable damaging barrier it can't get past (e.g. the
        energy-bar gates the bot can't slip between). REROUTE: retreat to the
        previous (safe) node and let ``wp_advance`` pick a different neighbour,
        so the bot goes AROUND instead of grinding the barrier to death. (Pure
        reverse would just oscillate it back into the barrier.)
      * progressing (open hazard the bot walks across, e.g. lava) => arm a short
        REVERSE window; ``_emit_normalize_and_emit`` flips the heading so the bot
        backs off the way it came, then resumes following.

    Either way it abandons any pickup divert + arms its cooldown, and it is the
    single owner of the per-bot ``bot_last_damage`` tracker. The reverse countdown
    reuses the dormant ``bot_wander_ticks`` field. ``ecx`` = slot. Falls through
    to the pickup-divert stage."""
    bot_slot_tmp_va           = layout.va('bot_slot_tmp')
    bot_char_tmp_va           = layout.va('bot_char_tmp')
    bot_last_damage_va        = layout.va('bot_last_damage')
    flee_counter_va           = layout.va('bot_wander_ticks')   # repurposed: reverse countdown
    flee_enabled_va           = layout.va('lava_flee_enabled')
    flee_frames_va            = layout.va('lava_flee_frames')
    pickup_div_active_va      = layout.va('pickup_div_active')
    pickup_cd_va              = layout.va('pickup_cd')
    pickup_cooldown_frames_va = layout.va('pickup_cooldown_frames')
    wp_try_va                 = layout.va('bot_wp_try')
    current_wp_va             = layout.va('bot_current_wp')
    prev_wp_va                = layout.va('bot_prev_wp')
    wp_best_dsq_va            = layout.va('bot_pickup_y_cache')  # min dsq-to-node
    slide_turn_va             = layout.va('bot_flee_ticks')      # wall-slide ramp

    a.raw(b'\x83\x3D' + le32(flee_enabled_va) + b'\x00')  # cmp [lava_flee_enabled], 0
    a.jz('s542360_flee_done')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    # Tick the flee countdown down every frame.
    a.raw(b'\x8B\x04\x8D' + le32(flee_counter_va))        # eax = flee_counter[slot]
    a.raw(b'\x85\xC0'); a.jz('s542360_flee_tick0')
    a.raw(b'\x48')                                        # dec eax
    a.raw(b'\x89\x04\x8D' + le32(flee_counter_va))        # flee_counter[slot] = eax
    a.label('s542360_flee_tick0')
    # While a reverse flee is active, hold wp_try low and best_dsq fresh so the
    # follower's progress watchdog and wall-slide don't read the intentional
    # backward motion as "stuck" and reroute the bot back into the lava.
    a.raw(b'\x8B\x04\x8D' + le32(flee_counter_va))        # eax = flee_counter[slot]
    a.raw(b'\x85\xC0'); a.jz('s542360_flee_noreset')
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))               # wp_try = 0
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.label('s542360_flee_noreset')
    # Health damage this frame? cur_damage(+0x7C) is a non-negative float; raw
    # bits compare as unsigned ints (monotonic for >= 0). ecx still = slot.
    a.raw(b'\xA1' + le32(bot_char_tmp_va))                # eax = bot char ptr
    a.raw(b'\x8B\x40' + bytes([ax.CHAR_CUR_DAMAGE_OFF]))  # eax = [char+0x7C] cur_damage bits
    a.raw(b'\x8B\x14\x8D' + le32(bot_last_damage_va))     # edx = bot_last_damage[slot] (prev)
    a.raw(b'\x89\x04\x8D' + le32(bot_last_damage_va))     # bot_last_damage[slot] = cur
    a.raw(b'\x39\xD0')                                    # cmp eax, edx
    a.jbe('s542360_flee_done')                            # cur <= prev -> no new health damage
    # Took health damage. Abandon any pickup divert + arm its cooldown first.
    a.raw(b'\xC7\x04\x8D' + le32(pickup_div_active_va) + le32(0))  # div_active = 0
    a.raw(b'\xA1' + le32(pickup_cooldown_frames_va))      # eax = cooldown
    a.raw(b'\x89\x04\x8D' + le32(pickup_cd_va))           # pickup_cd[slot] = cooldown
    # COMMIT: if a reverse flee is already active (flee_counter > 0, e.g. backing
    # off lava), STAY in reverse and just re-arm. Don't re-evaluate: the reverse's
    # own backward motion makes wp_try climb (no progress toward the node), and
    # re-evaluating would then flip us into a reroute and toggle the bot back into
    # the lava (observed: cur_damage 100, wp_try 7 about to cross 8). The
    # reroute-vs-reverse choice is therefore made ONCE per damage episode, at the
    # start (flee_counter == 0), using the then-uncontaminated wp_try. (A reroute
    # leaves flee_counter == 0, so a still-blocked barrier re-reroutes each frame.)
    a.raw(b'\x8B\x04\x8D' + le32(flee_counter_va))        # eax = flee_counter[slot]
    a.raw(b'\x85\xC0'); a.jnz('s542360_flee_reverse')     # already reversing -> re-arm, stay reverse
    # Blocked vs open? wp_try high => wedged against an impassable damaging
    # barrier (energy-bar gate) => REROUTE around it; else => REVERSE (lava).
    a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))              # eax = wp_try[slot]
    a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES])) # cmp eax, WP_SLIDE_TRIGGER (8)
    a.jb('s542360_flee_reverse')                          # progressing -> reverse
    a.raw(b'\x8B\x04\x8D' + le32(prev_wp_va))             # eax = prev_wp[slot]
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_flee_reverse')                          # not latched -> reverse fallback
    # Reroute: retreat to the previous (safe) node. Swap current<->prev so
    # wp_advance excludes the barrier-ward node, reset progress/slide, and cancel
    # any reverse (we now head FORWARD to prev, away from the barrier).
    a.raw(b'\x8B\x14\x8D' + le32(current_wp_va))          # edx = current_wp (barrier-ward node)
    a.raw(b'\x89\x04\x8D' + le32(current_wp_va))          # current_wp = prev (safe)
    a.raw(b'\x89\x14\x8D' + le32(prev_wp_va))             # prev_wp = old current
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))    # wp_try = 0
    a.raw(b'\xC7\x04\x8D' + le32(slide_turn_va) + le32(0))  # slide_turn = 0
    a.raw(b'\xC7\x04\x8D' + le32(flee_counter_va) + le32(0))  # cancel any reverse
    a.jmp('s542360_flee_done')
    a.label('s542360_flee_reverse')
    a.raw(b'\xA1' + le32(flee_frames_va))                 # eax = LAVA_FLEE_FRAMES
    a.raw(b'\x89\x04\x8D' + le32(flee_counter_va))        # flee_counter[slot] = frames
    a.label('s542360_flee_done')


def _emit_pickup_divert(a: Asm, layout: ScratchLayout) -> None:
    """Stage-2 pickup divert: a self-contained prefix to waypoint following.
    When a collectible pickup is near, steer to it instead of the current node,
    then resume the graph after a cooldown. Disabled -> a single cmp/jz falls
    straight through to ``s542360_pd_skip``, leaving the waypoint follower
    byte-for-byte unchanged. bot_pos is already read by the stuck stage."""
    bot_pos_va        = layout.va('bot_pos')
    bot_slot_tmp_va   = layout.va('bot_slot_tmp')
    bot_char_tmp_va   = layout.va('bot_char_tmp')
    wp_try_va         = layout.va('bot_wp_try')
    stuck_count_va    = layout.va('bot_stuck_count')
    stuck_frames_threshold_va = layout.va('stuck_frames_threshold')
    bot_last_damage_va = layout.va('bot_last_damage')

    pickup_table_va             = layout.va('pickup_table')
    pickup_count_va             = layout.va('pickup_count')
    pickup_divert_enabled_va    = layout.va('pickup_divert_enabled')
    pickup_divert_radius_sq_va  = layout.va('pickup_divert_radius_sq')
    pickup_reached_radius_sq_va = layout.va('pickup_reached_radius_sq')
    pickup_cooldown_frames_va   = layout.va('pickup_cooldown_frames')
    pickup_divert_timeout_va    = layout.va('pickup_divert_timeout')
    pickup_divert_avoid_damage_va = layout.va('pickup_divert_avoid_damage')
    pickup_cd_va                = layout.va('pickup_cd')
    pickup_div_active_va        = layout.va('pickup_div_active')
    pickup_div_x_va             = layout.va('pickup_div_x')
    pickup_div_y_va             = layout.va('pickup_div_y')
    pickup_div_try_va           = layout.va('pickup_div_try')

    # Borrowed accumulators for (dx, dy) — fire/aim per-call scratch, mutually
    # exclusive with this detour. curr_dist_sq doubles as the int->float spill.
    dx_accum_va = layout.va('curr_dist_sq')
    dy_accum_va = layout.va('cand_tmp')

    # === Pickup divert (Stage 2) ========================================
    a.raw(b'\x83\x3D' + le32(pickup_divert_enabled_va) + b'\x00')      # cmp [divert_enabled], 0
    a.jz('s542360_pd_skip')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot

    # Tick the post-grab cooldown down (every frame).
    a.raw(b'\x8B\x04\x8D' + le32(pickup_cd_va))           # eax = pickup_cd[slot]
    a.raw(b'\x85\xC0'); a.jz('s542360_pd_cd0')
    a.raw(b'\x48')                                        # dec eax
    a.raw(b'\x89\x04\x8D' + le32(pickup_cd_va))           # pickup_cd[slot] = eax
    a.label('s542360_pd_cd0')

    # (The reactive cur_damage check moved to _emit_reactive_lava_flee, which
    # runs before this stage, owns the bot_last_damage tracker, drops any active
    # divert + arms this cooldown on health damage, and is not gated by
    # pickup_divert_enabled so it works even with diverts off.)

    # Already diverting? -> maintain it.
    a.raw(b'\x8B\x04\x8D' + le32(pickup_div_active_va))   # eax = div_active[slot]
    a.raw(b'\x85\xC0'); a.jnz('s542360_pd_diverting')

    # Not diverting: only look for a pickup once the cooldown has expired.
    a.raw(b'\x8B\x04\x8D' + le32(pickup_cd_va))           # eax = pickup_cd[slot]
    a.raw(b'\x85\xC0'); a.jnz('s542360_pd_skip')

    # --- Scan pickup_table for the nearest within PICKUP_DIVERT_RADIUS_SQ.
    # ebx = best idx (-1 = none); best dsq seeded with the radius and kept on the
    # FPU (ST0) across the loop. esi = index, edi = count, eax = fnstsw scratch.
    a.raw(b'\xBB\xFF\xFF\xFF\xFF')                        # mov ebx, -1
    a.raw(b'\x8B\x3D' + le32(pickup_count_va))            # mov edi, [pickup_count]
    a.raw(b'\x85\xFF'); a.jz('s542360_pd_no_find')        # 0 pickups (FPU still empty)
    a.raw(b'\xD9\x05' + le32(pickup_divert_radius_sq_va)) # fld radius (best = ST0)
    a.raw(b'\x31\xF6')                                    # xor esi, esi
    a.label('s542360_pd_scan')
    a.raw(b'\x39\xFE'); a.jae('s542360_pd_scan_pop')      # esi >= count -> done (pop best)
    a.raw(b'\xD9\x04\xF5' + le32(pickup_table_va))        # fld [table + esi*8]    (ST0=x, ST1=best)
    a.raw(b'\xD8\x25' + le32(bot_pos_va))                 # fsub bot.x
    a.raw(b'\xD8\xC8')                                    # fmul st,st -> dx²
    a.raw(b'\xD9\x04\xF5' + le32(pickup_table_va + 4))    # fld [table + esi*8 + 4]
    a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))             # fsub bot.y
    a.raw(b'\xD8\xC8')                                    # fmul st,st -> dy²
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0=dsq, ST1=best
    a.raw(b'\xD8\xD1')                                    # fcom st(1)
    a.raw(b'\xDF\xE0'); a.raw(b'\x9E')                    # fnstsw ax; sahf
    a.jae('s542360_pd_scan_skip')                         # dsq >= best -> keep best
    a.raw(b'\xD9\xC9')                                    # fxch st(1)   (ST0=best, ST1=dsq)
    a.raw(b'\xDD\xD8')                                    # fstp st(0)   (pop best; ST0=dsq=new best)
    a.raw(b'\x89\xF3')                                    # mov ebx, esi
    a.jmp('s542360_pd_scan_next')
    a.label('s542360_pd_scan_skip')
    a.raw(b'\xDD\xD8')                                    # fstp st(0)   (pop dsq; keep best)
    a.label('s542360_pd_scan_next')
    a.raw(b'\x46')                                        # inc esi
    a.jmp('s542360_pd_scan')
    a.label('s542360_pd_scan_pop')
    a.raw(b'\xDD\xD8')                                    # fstp st(0)   (pop best; FPU empty)
    a.label('s542360_pd_no_find')
    a.raw(b'\x83\xFB\xFF')                                # cmp ebx, -1
    a.jz('s542360_pd_skip')                               # nothing in range -> waypoints

    # Latch the winner as the divert target (ecx = slot).
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\xDD' + le32(pickup_table_va))        # eax = table[ebx*8].x
    a.raw(b'\x89\x04\x8D' + le32(pickup_div_x_va))        # div_x[slot] = eax
    a.raw(b'\x8B\x04\xDD' + le32(pickup_table_va + 4))    # eax = table[ebx*8].y
    a.raw(b'\x89\x04\x8D' + le32(pickup_div_y_va))        # div_y[slot] = eax
    a.raw(b'\xC7\x04\x8D' + le32(pickup_div_active_va) + le32(1))  # div_active = 1
    a.raw(b'\xC7\x04\x8D' + le32(pickup_div_try_va) + le32(0))     # div_try = 0
    # fall into diverting

    a.label('s542360_pd_diverting')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    # desired = latched pickup - bot.
    a.raw(b'\xD9\x04\x8D' + le32(pickup_div_x_va))        # fld div_x[slot]
    a.raw(b'\xD8\x25' + le32(bot_pos_va))                 # fsub bot.x
    a.raw(b'\xD9\x1D' + le32(dx_accum_va))                # fstp dx_accum
    a.raw(b'\xD9\x04\x8D' + le32(pickup_div_y_va))        # fld div_y[slot]
    a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))             # fsub bot.y
    a.raw(b'\xD9\x1D' + le32(dy_accum_va))                # fstp dy_accum
    # Arrival: dsq < reached? -> the engine has (or is about to) auto-grant the
    # item on overlap; end the divert and start the cooldown.
    a.raw(b'\xD9\x05' + le32(dx_accum_va))                # fld dx
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xD9\x05' + le32(dy_accum_va))                # fld dy
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0 = dsq
    a.raw(b'\xD8\x1D' + le32(pickup_reached_radius_sq_va))# fcomp reached (pops dsq)
    a.raw(b'\xDF\xE0'); a.raw(b'\x9E')                    # fnstsw ax; sahf
    a.jae('s542360_pd_not_arrived')                       # dsq >= reached -> keep going
    a.jmp('s542360_pd_end')                               # arrived -> end + cooldown

    a.label('s542360_pd_not_arrived')
    # Fast wall-wedge abandon: the shared stuck counter (set above) climbs when
    # sub_4303F0 refuses to move the bot toward an unreachable (walled) pickup —
    # there is no LOS check in v1, so this is how we bail out quickly.
    a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))         # eax = stuck_count[slot]
    a.raw(b'\x3B\x05' + le32(stuck_frames_threshold_va))  # cmp eax, [stuck_threshold]
    a.jae('s542360_pd_end')                               # wedged -> abandon
    # Timeout backstop: ++div_try; abandon at PICKUP_DIVERT_TIMEOUT.
    a.raw(b'\x8B\x04\x8D' + le32(pickup_div_try_va))      # eax = div_try[slot]
    a.raw(b'\x40')                                        # inc eax
    a.raw(b'\x89\x04\x8D' + le32(pickup_div_try_va))      # div_try[slot] = eax
    a.raw(b'\x3B\x05' + le32(pickup_divert_timeout_va))   # cmp eax, [timeout]
    a.jb('s542360_pd_emit')                               # under budget -> steer at pickup
    # fall into end (timed out)

    a.label('s542360_pd_end')
    # End the divert and start the post-grab cooldown (ecx = slot).
    a.raw(b'\xC7\x04\x8D' + le32(pickup_div_active_va) + le32(0))  # div_active = 0
    a.raw(b'\xA1' + le32(pickup_cooldown_frames_va))      # eax = cooldown
    a.raw(b'\x89\x04\x8D' + le32(pickup_cd_va))           # pickup_cd[slot] = cooldown
    # fall into emit (one last frame toward the target; ~0 vector on arrival).

    a.label('s542360_pd_emit')
    # Keep the waypoint wall-slide quiet during a divert: a stale-high wp_try
    # would otherwise deflect the clean divert angle in s542360_wall_slide.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))    # wp_try[slot] = 0
    a.jmp('s542360_emit')

    a.label('s542360_pd_skip')


