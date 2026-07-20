"""Final stages: normalize + velocity/angle emit, wall-slide angle
sweep, portal/plasma heading vetoes, dead-bot reset, zero-vector
return and the host fall-through to the original prologue."""

from ... import addresses as ax
from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout
from .tuning import WP_SLIDE_TRIGGER_FRAMES, WP_SLIDE_TURN_CAP, WP_SLIDE_SWEEP_MASK, LAVA_SWEEP_COUNT


def _emit_normalize_and_emit(a: Asm, layout: ScratchLayout) -> None:
    """Normalize (dx, dy) to BOT_MOVE_SPEED, write the velocity vector to
    ``[esp+0x24]``, and the heading ``atan2(dy, dx)`` to ``[esp+0x28]``. A
    degenerate (zero) vector routes to the zero-return path."""
    bot_move_speed_va = layout.va('bot_move_speed')
    dx_accum_va = layout.va('curr_dist_sq')
    dy_accum_va = layout.va('cand_tmp')
    bot_slot_tmp_va = layout.va('bot_slot_tmp')
    flee_counter_va = layout.va('bot_wander_ticks')       # reactive lava-flee countdown

    # --- Normalize to BOT_MOVE_SPEED and emit velocity + angle --------------
    a.label('s542360_emit')
    # Reactive lava flee: while the flee window is armed (health damage taken,
    # see _emit_reactive_lava_flee), REVERSE the desired vector so the bot heads
    # back off the lava the way it came. Flipping the float sign bits negates
    # (dx, dy) without touching the magnitude, so the existing normalize + atan2
    # below still pick the right speed tier and a 180deg-rotated heading. A zero
    # vector stays zero (handled by the degenerate path).
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\x8D' + le32(flee_counter_va))        # eax = flee_counter[slot]
    a.raw(b'\x85\xC0'); a.jz('s542360_emit_noflee')
    a.raw(b'\x81\x35' + le32(dx_accum_va) + le32(0x80000000))  # xor [dx], sign -> negate
    a.raw(b'\x81\x35' + le32(dy_accum_va) + le32(0x80000000))  # xor [dy], sign -> negate
    a.label('s542360_emit_noflee')
    a.raw(b'\xD9\x05' + le32(dx_accum_va))                # fld dx
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xD9\x05' + le32(dy_accum_va))                # fld dy
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0 = len²
    a.raw(b'\xD9\xE4')                                    # ftst (compare len² to 0)
    a.raw(b'\xDF\xE0')                                    # fnstsw ax
    a.raw(b'\x9E')                                        # sahf
    a.jz('s542360_emit_zero_pop')                         # len² == 0 -> degenerate
    a.raw(b'\xD9\xFA')                                    # fsqrt -> |len|
    a.raw(b'\xD9\x05' + le32(bot_move_speed_va))          # fld speed (ST0=speed, ST1=|len|)
    a.raw(b'\xDE\xF1')                                    # fdivrp st(1),st -> ST0 = speed/|len|
    a.raw(b'\xD9\x05' + le32(dx_accum_va))                # fld dx
    a.raw(b'\xD8\xC9')                                    # fmul st, st(1)
    a.raw(b'\xD9\x1D' + le32(dx_accum_va))                # fstp dx (now vx)
    a.raw(b'\xD9\x05' + le32(dy_accum_va))                # fld dy
    a.raw(b'\xD8\xC9')                                    # fmul st, st(1)
    a.raw(b'\xD9\x1D' + le32(dy_accum_va))                # fstp dy (now vy)
    a.raw(b'\xDD\xD8')                                    # fstp st(0) (drop scale)

    a.raw(b'\x8B\x44\x24\x24')                            # eax = out_vec (esp+0x24)
    a.raw(b'\x85\xC0'); a.jz('s542360_emit_skip_vec')
    a.raw(b'\x8B\x0D' + le32(dx_accum_va))                # ecx = vx bits
    a.raw(b'\x89\x08')                                    # *out_vec     = vx
    a.raw(b'\x8B\x0D' + le32(dy_accum_va))                # ecx = vy bits
    a.raw(b'\x89\x48\x04')                                # *(out_vec+4) = vy
    a.label('s542360_emit_skip_vec')
    a.raw(b'\x8B\x44\x24\x28')                            # eax = out_angle (esp+0x28)
    a.raw(b'\x85\xC0'); a.jz('s542360_wall_slide')
    # sub_509100(arg0, arg1) = atan2(Y=arg0, X=arg1) — the FIRST arg is the
    # sin/Y axis, the SECOND is the cos/X axis (confirmed from disasm: the
    # both-positive branch returns atan(arg0/arg1)). __stdcall pops args
    # right-to-left, so the LAST push becomes arg0. The engine (sub_543B60 @
    # 0x543ced) steers the bot purely along (cos(angle), sin(angle)) and so
    # requires cos∝dx, sin∝dy, i.e. angle = atan2(dy, dx). Therefore push dx
    # (= arg1 = X) FIRST, then dy (= arg0 = Y) LAST — exactly the proven-correct
    # fire/aim order (bot_fire_aim.py: `push best_dx; push best_dy`).
    #
    # The previous order (dy then dx) computed atan2(dx, dy) = π/2 − atan2(dy,
    # dx), reflecting every heading across the y=x diagonal: a node due east
    # sent the bot due north, into geometry. sub_4303F0 is all-or-nothing, so
    # the bot refused to move, the wall-slide swept endlessly, and bots looked
    # "stuck in walls / not following waypoints". This swap is the root-cause fix.
    a.raw(b'\xFF\x35' + le32(dx_accum_va))                # push dx (arg1 = X / cos)
    a.raw(b'\xFF\x35' + le32(dy_accum_va))                # push dy (arg0 = Y / sin)
    a.call_va(ax.SUB_509100)                             # __stdcall, st0 = atan2(dy,dx), pops 8
    a.raw(b'\x8B\x44\x24\x28')                            # reload out_angle
    a.raw(b'\xD9\x18')                                    # fstp dword [eax]
    a.jmp('s542360_wall_slide')

    a.label('s542360_emit_zero_pop')
    a.raw(b'\xDD\xD8')                                    # fstp st(0)  (drop len²)
    a.jmp('s542360_zero')


def _emit_wall_slide(a: Asm, layout: ScratchLayout) -> None:
    """Deflect the emitted ANGLE while wedged — no freeze. The engine moves the
    bot purely by the angle; a magnitude already sits in the velocity vector.
    Mirrors the controller block vector into the dormant wander fields for
    diagnostics, then ramps a per-bot deflection driven by wp_try."""
    bot_slot_tmp_va  = layout.va('bot_slot_tmp')
    wp_try_va        = layout.va('bot_wp_try')
    stuck_count_va   = layout.va('bot_stuck_count')
    slide_turn_va    = layout.va('bot_flee_ticks')        # wall-slide ramp
    diag_block_x_va  = layout.va('bot_wander_x')          # block-vec diag mirror
    diag_block_y_va  = layout.va('bot_wander_y')
    frame_counter_va = layout.va('frame_counter')
    wp_slide_turn_step_va = layout.va('wp_slide_turn_step')
    dx_accum_va = layout.va('curr_dist_sq')

    # --- Wall-slide: deflect the ANGLE while wedged, no freeze --------------
    # The engine moves the bot purely by the angle; a magnitude already sits in
    # the velocity vector. When the bot has been physically stuck for
    # WP_SLIDE_TRIGGER_FRAMES (its straight-at-node heading is blocked), ramp a
    # per-bot deflection and add slide_turn * WP_SLIDE_TURN_STEP to the angle so
    # the bot sweeps to a clear heading and slides along the wall. The ramp
    # decays slowly while moving so it tracks the wall rather than snapping back
    # into it.
    a.label('s542360_wall_slide')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot

    # Diagnostic mirror: copy controller block vector [+0x14/+0x18] into the
    # dormant wander_x/y[slot] so an ai_move R-dump shows whether the engine
    # populates it near walls (input for a future geometric slide).
    a.raw(b'\x8B\x74\x24\x18')                            # esi = saved orig ECX (controller)
    a.raw(b'\x85\xF6'); a.jz('s542360_ws_diag_zero')
    a.raw(b'\x8B\x46\x14')                                # eax = block.x bits
    a.raw(b'\x89\x04\x8D' + le32(diag_block_x_va))        # wander_x[slot] = block.x
    a.raw(b'\x8B\x46\x18')                                # eax = block.y bits
    a.raw(b'\x89\x04\x8D' + le32(diag_block_y_va))        # wander_y[slot] = block.y
    a.jmp('s542360_ws_diag_done')
    a.label('s542360_ws_diag_zero')
    a.raw(b'\xC7\x04\x8D' + le32(diag_block_x_va) + le32(0))
    a.raw(b'\xC7\x04\x8D' + le32(diag_block_y_va) + le32(0))
    a.label('s542360_ws_diag_done')

    # Update the deflection from LACK OF PROGRESS (wp_try), with stuck_count as
    # a secondary "not moving at all" trigger. While the bot fails to get closer
    # to its node, cycle the heading one step every (SWEEP_MASK+1) frames to try
    # to find a way around the wall. While progressing (both counters below the
    # trigger) clear the deflection so the bot steers straight at its node.
    # Infinite circling is prevented by the RETREAT in the follow block.
    a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))         # eax = stuck_count[slot]
    a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES])) # cmp eax, TRIGGER
    a.jae('s542360_ws_triggered')                         # physically frozen -> sweep
    a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))              # eax = wp_try[slot]
    a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))  # cmp eax, TRIGGER
    a.jb('s542360_ws_reset')                             # progressing -> straight
    a.label('s542360_ws_triggered')
    a.raw(b'\x8B\x15' + le32(frame_counter_va))           # edx = frame_counter
    a.raw(b'\x83\xE2' + bytes([WP_SLIDE_SWEEP_MASK]))     # and edx, MASK
    a.jnz('s542360_ws_have_turn')                         # hold heading between steps
    a.raw(b'\x8B\x04\x8D' + le32(slide_turn_va))          # eax = slide_turn[slot]
    a.raw(b'\x40')                                        # inc eax
    a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TURN_CAP]))       # cmp eax, CAP (full circle)
    a.jb('s542360_ws_store_turn')
    a.raw(b'\x31\xC0')                                    # xor eax, eax (wrap to 0)
    a.label('s542360_ws_store_turn')
    a.raw(b'\x89\x04\x8D' + le32(slide_turn_va))          # slide_turn[slot] = eax
    a.jmp('s542360_ws_have_turn')
    a.label('s542360_ws_reset')
    a.raw(b'\xC7\x04\x8D' + le32(slide_turn_va) + le32(0))  # progressing -> slide_turn = 0
    a.label('s542360_ws_have_turn')

    a.raw(b'\x8B\x04\x8D' + le32(slide_turn_va))          # eax = slide_turn[slot]
    a.raw(b'\x85\xC0'); a.jz('s542360_portal_veto')       # no deflection -> portal veto
    a.raw(b'\x8B\x54\x24\x28')                            # edx = out_angle (esp+0x28)
    a.raw(b'\x85\xD2'); a.jz('s542360_ret')               # no angle slot
    # angle += slide_turn * WP_SLIDE_TURN_STEP (engine uses cos/sin, no wrap
    # needed — the movement angle is range-agnostic and is overwritten by the
    # fire/aim path before any facing use).
    a.raw(b'\xA3' + le32(dx_accum_va))                    # spill slide_turn int (dx_accum free)
    a.raw(b'\xDB\x05' + le32(dx_accum_va))                # fild dword [dx_accum] -> (float)turn
    a.raw(b'\xD8\x0D' + le32(wp_slide_turn_step_va))      # fmul step -> deflection
    a.raw(b'\xD8\x02')                                    # fadd dword [edx]  (angle)
    a.raw(b'\xD9\x1A')                                    # fstp dword [edx]  (store angle)
    a.jmp('s542360_portal_veto')


def _emit_portal_veto(a: Asm, layout: ScratchLayout) -> None:
    """Post-teleport RETURN-PAD heading veto — the anti-ping-pong wall.

    Runs AFTER the wall-slide finalizes the emitted angle, only while this
    bot's post-teleport cooldown (``bot_portal_cd``) is running. The teleport
    drops the bot at the exit marker inside a collision pocket around the
    teleporter prop, ~28 px from the RETURN pad's thin trigger sliver (live
    proute snapshots caught a carrier pinned at the exact exit coords with
    ``wp_try`` ramping, then bounced arena-to-arena every ~1-2 s). The
    wall-slide sweep is what escapes the pocket — but it tries headings in
    fixed order, and any sliver-ward heading that moves fires an ENGINE
    re-teleport no bot-side gate can stop. So while the cooldown runs, any
    candidate heading whose ``LAVA_LOOKAHEAD_PX`` lookahead point lands
    within sqrt(``portal_veto_radius_sq``) of a pad center is rotated onward
    (``lava_sweep_step`` per try, up to a full circle) — pads become virtual
    walls. The pad the bot deliberately LATCHED (``bot_portal_target``) is
    exempt: routing legitimately sends carriers back through a pad.

    Reuses the lava-veto per-call temps (the two vetoes run sequentially in
    the same call, never concurrently) and ``wp_seg_x/y`` (steer-stage
    temps, dead by this point) for the lookahead point; ``pw_spill`` holds
    the exempt pad index. FPU stays balanced; GPRs are popad-restored."""
    bot_pos_va            = layout.va('bot_pos')
    bot_slot_tmp_va       = layout.va('bot_slot_tmp')
    lava_lookahead_px_va  = layout.va('lava_lookahead_px')
    lava_sweep_step_va    = layout.va('lava_sweep_step')
    veto_angle_va         = layout.va('lava_veto_angle')
    veto_cos_va           = layout.va('lava_veto_cos')
    veto_sin_va           = layout.va('lava_veto_sin')
    lava_k_va             = layout.va('lava_k')
    wp_seg_x_va           = layout.va('wp_seg_x')
    wp_seg_y_va           = layout.va('wp_seg_y')

    a.label('s542360_portal_veto')
    ok = (layout.has_field('bot_portal_cd')
          and layout.has_field('bot_portal_target')
          and layout.has_field('portal_table')
          and layout.has_field('portal_veto_radius_sq')
          and layout.has_field('pw_spill'))
    if not ok:
        return                                              # fall through to plasma veto

    bot_portal_cd_va     = layout.va('bot_portal_cd')
    bot_portal_target_va = layout.va('bot_portal_target')
    portal_table_va      = layout.va('portal_table')
    portal_count_va      = layout.va('portal_count')
    veto_radius_va       = layout.va('portal_veto_radius_sq')
    pw_spill_va          = layout.va('pw_spill')

    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))              # ecx = slot
    a.raw(b'\x83\x3C\x8D' + le32(bot_portal_cd_va) + b'\x00')  # cooldown running?
    a.jz('s542360_plasma_veto')                             # no -> pads are fair game
    a.raw(b'\x8B\x54\x24\x28')                              # edx = out_angle ptr
    a.raw(b'\x85\xD2'); a.jz('s542360_plasma_veto')         # no angle slot
    # Exempt pad = the deliberately latched one (idx, or -1 when unlatched).
    a.raw(b'\x8B\x04\x8D' + le32(bot_portal_target_va))     # eax = latch (idx+1 / 0)
    a.raw(b'\x48')                                          # eax = idx / -1
    a.raw(b'\xA3' + le32(pw_spill_va))                      # pw_spill = exempt idx
    a.raw(b'\x8B\x02')                                      # eax = base angle bits
    a.raw(b'\xA3' + le32(veto_angle_va))                    # veto_angle = base heading
    a.raw(b'\xC7\x05' + le32(lava_k_va) + le32(0))          # k = 0

    a.label('s542360_pvt_loop')
    # Lookahead point of the candidate heading.
    a.raw(b'\xD9\x05' + le32(veto_angle_va))                # fld veto_angle
    a.raw(b'\xD9\xFB')                                      # fsincos -> ST0=cos, ST1=sin
    a.raw(b'\xD9\x1D' + le32(veto_cos_va))                  # fstp cos (-> ST0=sin)
    a.raw(b'\xD9\x1D' + le32(veto_sin_va))                  # fstp sin (-> empty)
    a.raw(b'\xD9\x05' + le32(lava_lookahead_px_va))         # fld look
    a.raw(b'\xD8\x0D' + le32(veto_cos_va))                  # fmul cos
    a.raw(b'\xD8\x05' + le32(bot_pos_va))                   # fadd bot.x
    a.raw(b'\xD9\x1D' + le32(wp_seg_x_va))                  # fstp look.x
    a.raw(b'\xD9\x05' + le32(lava_lookahead_px_va))         # fld look
    a.raw(b'\xD8\x0D' + le32(veto_sin_va))                  # fmul sin
    a.raw(b'\xD8\x05' + le32(bot_pos_va + 4))               # fadd bot.y
    a.raw(b'\xD9\x1D' + le32(wp_seg_y_va))                  # fstp look.y
    # Scan pads: blocked when the lookahead lands inside a non-exempt bubble.
    a.raw(b'\x31\xF6')                                      # esi = 0 (p)
    a.label('s542360_pvt_scan')
    a.raw(b'\x3B\x35' + le32(portal_count_va))              # p >= portal_count?
    a.jae('s542360_pvt_ok')
    a.raw(b'\x83\xFE' + bytes([cfg.PORTAL_TABLE_MAX]))      # p >= table max?
    a.jae('s542360_pvt_ok')
    a.raw(b'\x3B\x35' + le32(pw_spill_va))                  # p == exempt (latched) pad?
    a.jz('s542360_pvt_next')
    a.raw(b'\xD9\x04\xF5' + le32(portal_table_va))          # fld pad.x
    a.raw(b'\xD8\x25' + le32(wp_seg_x_va))                  # fsub look.x
    a.raw(b'\xD8\xC8')                                      # fmul st,st
    a.raw(b'\xD9\x04\xF5' + le32(portal_table_va + 4))      # fld pad.y
    a.raw(b'\xD8\x25' + le32(wp_seg_y_va))                  # fsub look.y
    a.raw(b'\xD8\xC8')                                      # fmul st,st
    a.raw(b'\xDE\xC1')                                      # faddp -> ST0 = d²
    a.raw(b'\xD8\x1D' + le32(veto_radius_va))               # fcomp radius² (pops)
    a.raw(b'\xDF\xE0'); a.raw(b'\x9E')                      # fnstsw ax; sahf
    a.jb('s542360_pvt_blocked')                             # d² < radius² -> pad-ward
    a.label('s542360_pvt_next')
    a.raw(b'\x46')                                          # ++p
    a.jmp('s542360_pvt_scan')

    a.label('s542360_pvt_blocked')
    # Rotate the heading and retry, up to a full circle; if every heading is
    # pad-ward (can't happen with a 40px bubble) keep the base angle.
    a.raw(b'\xD9\x05' + le32(veto_angle_va))                # fld veto_angle
    a.raw(b'\xD8\x05' + le32(lava_sweep_step_va))           # fadd sweep step
    a.raw(b'\xD9\x1D' + le32(veto_angle_va))                # fstp veto_angle
    a.raw(b'\xFF\x05' + le32(lava_k_va))                    # ++k
    a.raw(b'\x83\x3D' + le32(lava_k_va) + bytes([LAVA_SWEEP_COUNT]))
    a.jb('s542360_pvt_loop')
    a.jmp('s542360_plasma_veto')                            # all vetoed -> keep base

    a.label('s542360_pvt_ok')
    a.raw(b'\x8B\x54\x24\x28')                              # edx = out_angle ptr
    a.raw(b'\xA1' + le32(veto_angle_va))                    # eax = chosen angle bits
    a.raw(b'\x89\x02')                                      # [edx] = pad-clear heading
    # fall through to the plasma veto


def _emit_plasma_veto(a: Asm, layout: ScratchLayout) -> None:
    """Proactive lava veto — the "lava is a virtual wall" step. Runs AFTER the
    wall-slide finalizes the emitted angle. Samples the plasma HEAT grid
    ``cfg.LAVA_LOOKAHEAD_PX`` ahead along that heading; if it would step into
    lava (``is_plasma_at``), rotates the emitted angle by ``cfg.LAVA_SWEEP_STEP``
    per try (up to a full circle) until a lava-clear heading is found and
    rewrites ``[esp+0x28]``. A no-op when disabled, on non-plasma maps
    (``plasma_map == 0``), or when there's no angle slot. If every heading is
    blocked it keeps the base angle and the reactive ``char+0x7C`` flee fallback
    catches the contact. Registers are popad-restored at ``s542360_ret`` so the
    eax/ecx/edx clobber from ``is_plasma_at`` is irrelevant; FPU stays balanced.

    Wall-slide vs lava: the wall-slide rotates to escape *geometry* (it can't
    test geometry in-frame, so it sweeps over frames via wp_try); this veto
    rotates to escape *lava* (which it CAN test in-frame via the heat grid). A
    lava-clear heading is almost always also wall-clear (lava isn't geometry),
    so they rarely fight; the wp_try watchdog resolves the rare conflict."""
    bot_pos_va            = layout.va('bot_pos')
    plasma_map_va         = layout.va('plasma_map')
    plasma_qx_va          = layout.va('plasma_qx')
    plasma_qy_va          = layout.va('plasma_qy')
    lava_avoid_enabled_va = layout.va('lava_avoid_enabled')
    lava_lookahead_px_va  = layout.va('lava_lookahead_px')
    lava_sweep_step_va    = layout.va('lava_sweep_step')
    veto_angle_va         = layout.va('lava_veto_angle')
    veto_cos_va           = layout.va('lava_veto_cos')
    veto_sin_va           = layout.va('lava_veto_sin')
    lava_k_va             = layout.va('lava_k')

    a.label('s542360_plasma_veto')
    a.raw(b'\x83\x3D' + le32(lava_avoid_enabled_va) + b'\x00')  # cmp [lava_avoid_enabled], 0
    a.jz('s542360_ret')
    a.raw(b'\x83\x3D' + le32(plasma_map_va) + b'\x00')          # cmp [plasma_map], 0
    a.jz('s542360_ret')                                         # non-plasma map -> no-op
    a.raw(b'\x8B\x54\x24\x28')                                  # edx = out_angle ptr (esp+0x28)
    a.raw(b'\x85\xD2'); a.jz('s542360_ret')                     # no angle slot
    a.raw(b'\x8B\x02')                                          # eax = [edx] base angle bits
    a.raw(b'\xA3' + le32(veto_angle_va))                        # veto_angle = base heading
    a.raw(b'\xC7\x05' + le32(lava_k_va) + le32(0))             # lava_k = 0

    a.label('s542360_pv_loop')
    # cos/sin of the candidate heading.
    a.raw(b'\xD9\x05' + le32(veto_angle_va))                    # fld veto_angle
    a.raw(b'\xD9\xFB')                                          # fsincos -> ST0=cos, ST1=sin
    a.raw(b'\xD9\x1D' + le32(veto_cos_va))                      # fstp veto_cos (-> ST0=sin)
    a.raw(b'\xD9\x1D' + le32(veto_sin_va))                      # fstp veto_sin (-> empty)
    # qx = (int)(bot.x + look*cos)
    a.raw(b'\xD9\x05' + le32(lava_lookahead_px_va))             # fld look
    a.raw(b'\xD8\x0D' + le32(veto_cos_va))                      # fmul cos
    a.raw(b'\xD8\x05' + le32(bot_pos_va))                       # fadd bot.x
    a.raw(b'\xDB\x1D' + le32(plasma_qx_va))                     # fistp plasma_qx
    # qy = (int)(bot.y + look*sin)
    a.raw(b'\xD9\x05' + le32(lava_lookahead_px_va))             # fld look
    a.raw(b'\xD8\x0D' + le32(veto_sin_va))                      # fmul sin
    a.raw(b'\xD8\x05' + le32(bot_pos_va + 4))                   # fadd bot.y
    a.raw(b'\xDB\x1D' + le32(plasma_qy_va))                     # fistp plasma_qy
    a.call_lbl('is_plasma_at')                                  # eax = 1 if lava ahead
    a.raw(b'\x85\xC0'); a.jz('s542360_pv_found')                # lava-clear -> use veto_angle
    # Blocked: rotate by the sweep step and try again, up to a full circle.
    a.raw(b'\xD9\x05' + le32(veto_angle_va))                    # fld veto_angle
    a.raw(b'\xD8\x05' + le32(lava_sweep_step_va))               # fadd sweep_step
    a.raw(b'\xD9\x1D' + le32(veto_angle_va))                    # fstp veto_angle
    a.raw(b'\xFF\x05' + le32(lava_k_va))                        # ++lava_k
    a.raw(b'\x83\x3D' + le32(lava_k_va) + bytes([LAVA_SWEEP_COUNT]))  # cmp [lava_k], COUNT
    a.jb('s542360_pv_loop')
    a.jmp('s542360_ret')                                        # all blocked -> keep base angle

    a.label('s542360_pv_found')
    a.raw(b'\x8B\x54\x24\x28')                                  # edx = out_angle ptr
    a.raw(b'\xA1' + le32(veto_angle_va))                        # eax = chosen angle bits
    a.raw(b'\x89\x02')                                          # [edx] = chosen heading
    a.jmp('s542360_ret')


def _emit_dead_and_zero_return(a: Asm, layout: ScratchLayout) -> None:
    """Bot-dead nav reset (char slot NULL) and the shared zero-vector return
    (panic / NULL char / degenerate normalize). ``s542360_ret`` pops the frame
    and returns ``0x14``."""
    bot_slot_tmp_va = layout.va('bot_slot_tmp')
    current_wp_va   = layout.va('bot_current_wp')
    prev_wp_va      = layout.va('bot_prev_wp')
    wp_try_va       = layout.va('bot_wp_try')
    wp_best_dsq_va  = layout.va('bot_pickup_y_cache')     # min dsq-to-node
    failed_edge_va  = layout.va('bot_pickup_valid')       # packed failed-edge marker
    slide_turn_va   = layout.va('bot_flee_ticks')         # wall-slide ramp
    door_gate       = cfg.DOOR_DETECT_ENABLED and layout.has_field('route_block_door')

    # --- Bot dead this frame (char slot NULL). Reset nav so it cold-acquires
    # on respawn, then emit zero (no live char this frame).
    a.label('s542360_wp_mark_dead')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xC7\x04\x8D' + le32(current_wp_va) + b'\xFF\xFF\xFF\xFF')  # current_wp = -1
    a.raw(b'\xC7\x04\x8D' + le32(prev_wp_va) + b'\xFF\xFF\xFF\xFF')     # prev_wp = -1
    a.raw(b'\xC7\x04\x8D' + le32(slide_turn_va) + le32(0))             # slide_turn = 0
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))    # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))                # wp_try = 0
    a.raw(b'\xC7\x04\x8D' + le32(failed_edge_va) + le32(0))           # failed_edge_marker = 0
    if door_gate:
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('route_block_door'))
              + b'\xFF\xFF\xFF\xFF')                                  # wedge-door latch = -1
    if layout.has_field('bot_portal_target'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_portal_target')) + le32(0))  # drop pad approach
    if layout.has_field('bot_pad_try'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_pad_try')) + le32(0))
    if layout.has_field('bot_drop_target'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_drop_target')) + le32(0))    # drop flag pursuit
    if layout.has_field('bot_switch_target'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_switch_target')) + le32(0))  # drop switch bump
    if layout.has_field('bot_pile_target'):
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_pile_target')) + le32(0))    # drop pile divert
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_sk_return')) + le32(0))      # dead = carrying nothing
    # fall through to zero-vector return.

    # --- Zero-vector return (panic / NULL char / degenerate normalize) ---
    a.label('s542360_zero')
    a.raw(b'\x8B\x44\x24\x24')                            # esp+0x24 (out_vec)
    a.raw(b'\x85\xC0'); a.jz('s542360_zero_skip_vec')
    a.raw(b'\xC7\x00\x00\x00\x00\x00')                    # out_vec[0] = 0
    a.raw(b'\xC7\x40\x04\x00\x00\x00\x00')                # out_vec[1] = 0
    a.label('s542360_zero_skip_vec')
    a.raw(b'\x8B\x44\x24\x28')                            # esp+0x28 (out_angle)
    a.raw(b'\x85\xC0'); a.jz('s542360_ret')
    a.raw(b'\xC7\x00\x00\x00\x00\x00')                    # *out_angle = 0

    a.label('s542360_ret')
    a.raw(b'\x61')                                        # popad
    a.raw(b'\xC2\x14\x00')                                # ret 0x14


def _emit_normal_fallthrough(a: Asm) -> None:
    """Non-bot controllers: re-run the displaced prologue and resume the
    original ``sub_542360``."""
    a.label('s542360_normal')
    a.raw(ax.S542360_PROLOGUE)
    a.jmp_va(ax.S542360_RESUME)
