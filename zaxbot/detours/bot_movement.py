"""``detour_542360`` — bot movement-vector synthesis.

ECX = ``CPlayerWalkingControlAI``. We identify bot controllers via the
controller's ``player_num`` at ``[ecx+0x1C]`` matched against
``bot_indices[]``. Host controllers fall through to the original prologue.

For bots the detour synthesizes a 2D direction by accumulating:
  1. **Wander base**: ``(wander_target - bot_pos)``. The target is a random
     point within ±``WANDER_TARGET_RADIUS`` of the bot, re-rolled every
     ``WANDER_TARGET_TIMEOUT_FRAMES`` (or sooner via stuck detection).
  2. **Hazard repulse**: each cached ``CDamageExpandingRadiusAI`` entity
     within ``HAZARD_REPULSION_RADIUS_SQ`` adds a normalized away-vector
     scaled by ``HAZARD_REPULSION_WEIGHT / sqrt(d²)``. Cache is built once
     per match by ``scan_hazards`` from ``detour_df90``.
  3. **Item attractor**: the staggered ``pick_pickup`` scan caches the
     closest visible ``CPickupAI`` within ``ITEM_ATTRACTOR_RADIUS_SQ``;
     when the cache is valid, ``(pickup - bot) * ATTRACTOR_WEIGHT`` is
     added to the accumulator.

The accumulated ``(dx, dy)`` is normalized and scaled by ``BOT_MOVE_SPEED``;
the angle is ``atan2(dy, dx)`` via ``sub_509100``. Engine ``sub_4303F0``
(downstream) handles wall collision and pickup-on-walkover for free, so
this detour only steers.

``MOVEMENT_ENABLED`` is a panic switch reverting to the original
zero-vector behavior (one dword flip in scratch).

Output convention: ``[esp+4]`` is the ``float[2]`` velocity out, ``[esp+8]``
is the ``float*`` angle (radians) out, ``ret 0x14``.
"""

from .. import addresses as ax
from ..asm import Asm, le32
from ..hook.bot_lookup import emit_addr_to_slot, emit_is_bot_controller
from ..layout import ScratchLayout


# Accumulator scratch — we borrow two unused dword fire/aim slots
# (``curr_dist_sq`` and ``cand_tmp``) as ``dx_accum`` / ``dy_accum``.
# pick_target / pick_pickup are mutually exclusive with this detour, so the
# overlap is safe.
def _emit_rng_axis(a, layout, dst_va, base_pos_va):
    """Emit `wander_dst = base_pos + RNG(-R, +R)` for one axis.

    ECX must hold the bot slot on entry; it is preserved across the call.
    The float-to-int radius conversion uses ``curr_dist_sq`` as a temporary
    spill, which is safe because we recompute it later (accumulators are
    initialised after this helper runs).
    """
    curr_dist_sq_va  = layout.va('curr_dist_sq')
    wander_radius_va = layout.va('wander_target_radius')

    # int_R = int(WANDER_TARGET_RADIUS) via fld + fistp.
    a.raw(b'\xD9\x05' + le32(wander_radius_va))           # fld radius
    a.raw(b'\xDB\x1D' + le32(curr_dist_sq_va))            # fistp curr_dist_sq

    a.raw(b'\xFF\x35' + le32(curr_dist_sq_va))            # push +R
    a.raw(b'\x8B\x05' + le32(curr_dist_sq_va))            # eax = +R
    a.raw(b'\xF7\xD8')                                    # neg eax
    a.raw(b'\x50')                                        # push -R
    a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                  # ecx = RNG instance
    a.call_va(ax.RNG_SUB)                                 # eax = rand in [-R, +R]

    a.raw(b'\x89\x05' + le32(curr_dist_sq_va))            # spill rand for fild
    a.raw(b'\xDB\x05' + le32(curr_dist_sq_va))            # fild int -> ST0 = rand_float
    a.raw(b'\xD8\x05' + le32(base_pos_va))                # fadd base_pos (bot.x or bot.y)
    a.raw(b'\x8B\x0D' + le32(layout.va('bot_slot_tmp')))  # reload slot (RNG call clobbered ECX)
    a.raw(b'\xD9\x1C\x8D' + le32(dst_va))                 # fstp dword [dst + slot*4]


def emit(a: Asm, layout: ScratchLayout) -> None:
    bot_chars_va             = layout.va('bot_chars')
    bot_pos_va               = layout.va('bot_pos')
    bot_slot_tmp_va          = layout.va('bot_slot_tmp')
    bot_char_tmp_va          = layout.va('bot_char_tmp')

    wander_x_va              = layout.va('bot_wander_x')
    wander_y_va              = layout.va('bot_wander_y')
    wander_ticks_va          = layout.va('bot_wander_ticks')
    last_x_va                = layout.va('bot_last_x')
    last_y_va                = layout.va('bot_last_y')
    stuck_count_va           = layout.va('bot_stuck_count')
    last_item_scan_va        = layout.va('bot_last_item_scan')
    pickup_x_cache_va        = layout.va('bot_pickup_x_cache')
    pickup_y_cache_va        = layout.va('bot_pickup_y_cache')
    pickup_valid_va          = layout.va('bot_pickup_valid')

    frame_counter_va         = layout.va('frame_counter')
    hazard_count_va          = layout.va('hazard_count')
    hazard_table_va          = layout.va('hazard_table')

    movement_enabled_va      = layout.va('movement_enabled')
    wander_timeout_va        = layout.va('wander_target_timeout')
    stuck_threshold_va       = layout.va('stuck_frames_threshold')
    stuck_delta_sq_va        = layout.va('stuck_delta_sq')
    item_weight_va           = layout.va('item_attractor_weight')
    item_scan_interval_va    = layout.va('item_scan_interval')
    hazard_radius_va         = layout.va('hazard_repulsion_radius_sq')
    hazard_weight_va         = layout.va('hazard_repulsion_weight')
    bot_move_speed_va        = layout.va('bot_move_speed')

    # Borrowed accumulators for (dx, dy) — see module docstring note.
    dx_accum_va = layout.va('curr_dist_sq')
    dy_accum_va = layout.va('cand_tmp')

    a.label('detour_542360')
    emit_is_bot_controller(a, layout,
                           on_not_bot='s542360_normal',
                           label_prefix='s542360')

    emit_addr_to_slot(a, layout)                          # eax = slot
    a.raw(b'\xA3' + le32(bot_slot_tmp_va))                # save slot
    # The original sub_542360 prologue saves EBX/EBP. Our hazard loop uses
    # EBX/ESI/EDI, so we MUST preserve callee-saved regs or the engine's
    # caller (sub_543B60 at 0x543CF2) crashes reading [EBX+0x9C] after we
    # return. pushad covers all 8 GPRs; downstream `[esp+4]`/`[esp+8]`
    # arg reads bump to `[esp+36]`/`[esp+40]` to account for the 32 saved
    # bytes. popad runs before the single `ret 0x14` epilogue.
    a.raw(b'\x60')                                        # pushad

    a.raw(b'\xFF\x05' + le32(frame_counter_va))           # ++frame_counter

    # Panic switch.
    a.raw(b'\x83\x3D' + le32(movement_enabled_va) + b'\x00')
    a.jz('s542360_zero')

    # Live char fetch: read from mgr+0x290[bot_indices[slot]] rather than
    # trusting our bot_chars[] cache. When the bot dies (e.g. on lava), the
    # engine clears mgr+0x290 but our cache still holds the stale pointer —
    # calling sub_4FB0A0 on freed-then-reused memory crashes with EIP=0
    # because the wrong vtable's slot at the offset sub_4FB0A0 calls is NULL.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x14\x8D' + le32(layout.va('bot_indices')))  # edx = bot_indices[slot]
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))           # eax = [mgr]
    a.raw(b'\x85\xC0'); a.jz('s542360_zero')              # mgr NULL
    a.raw(b'\x8B\x80\x90\x02\x00\x00')                    # eax = [mgr + 0x290] (char array)
    a.raw(b'\x85\xC0'); a.jz('s542360_zero')              # array NULL
    a.raw(b'\x8B\x14\x90')                                # edx = [eax + edx*4] (chars[idx])
    a.raw(b'\x85\xD2'); a.jz('s542360_zero')              # char NULL → bot dead this frame
    a.raw(b'\x89\x15' + le32(bot_char_tmp_va))

    # Read bot position.
    a.raw(b'\x68' + le32(bot_pos_va))                     # push &bot_pos
    a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))            # ecx = bot char
    a.call_va(ax.SUB_4FB0A0_VA)                           # __thiscall, ret 4

    # --- Save motion delta (pos - last_pos) for the reactive damage logic.
    # We capture it BEFORE the stuck detection refresh overwrites last_x/y.
    # best_dx / best_dy are pick_target/pick_pickup's per-call scratch and
    # are dead between the position read and the hazard repulse loop, so we
    # safely reuse them here for the (motion_dx, motion_dy) carry value.
    motion_dx_va = layout.va('best_dx')
    motion_dy_va = layout.va('best_dy')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xD9\x05' + le32(bot_pos_va))                 # fld pos.x
    a.raw(b'\xD8\x24\x8D' + le32(last_x_va))              # fsub last_x[slot]
    a.raw(b'\xD9\x1D' + le32(motion_dx_va))               # fstp motion_dx
    a.raw(b'\xD9\x05' + le32(bot_pos_va + 4))             # fld pos.y
    a.raw(b'\xD8\x24\x8D' + le32(last_y_va))              # fsub last_y[slot]
    a.raw(b'\xD9\x1D' + le32(motion_dy_va))               # fstp motion_dy

    # --- Stuck detection: d² between current and last position ----------
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xD9\x05' + le32(bot_pos_va))                 # fld pos.x
    a.raw(b'\xD8\x24\x8D' + le32(last_x_va))              # fsub last_x[slot]
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xD9\x05' + le32(bot_pos_va + 4))             # fld pos.y
    a.raw(b'\xD8\x24\x8D' + le32(last_y_va))              # fsub last_y[slot]
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0 = d²
    a.raw(b'\xD8\x1D' + le32(stuck_delta_sq_va))          # FCOMP threshold (pops)
    a.raw(b'\xDF\xE0')                                    # fnstsw ax
    a.raw(b'\x9E')                                        # sahf
    a.jae('s542360_not_stuck')                            # d² >= threshold -> not stuck
    a.raw(b'\xFF\x04\x8D' + le32(stuck_count_va))         # ++stuck_count[slot]
    a.jmp('s542360_stuck_done')
    a.label('s542360_not_stuck')
    a.raw(b'\xC7\x04\x8D' + le32(stuck_count_va) + le32(0))
    a.label('s542360_stuck_done')
    # Refresh last position.
    a.raw(b'\xA1' + le32(bot_pos_va))
    a.raw(b'\x89\x04\x8D' + le32(last_x_va))
    a.raw(b'\xA1' + le32(bot_pos_va + 4))
    a.raw(b'\x89\x04\x8D' + le32(last_y_va))

    # --- Reactive hazard avoidance ------------------------------------
    # Read accumulated damage off the bot char ([char + 0x7C]). If it
    # increased since last frame the bot is taking damage from SOMETHING
    # (lava, fire, projectile, …) so we bias the wander target to be the
    # OPPOSITE direction of the bot's recent motion (motion_dx/dy stashed
    # above) and commit to it for HAZARD_FLEE_FRAMES. The commit prevents
    # the wander timer from re-rolling the bot back onto the same hazard.
    #
    # last_damage is updated unconditionally each frame so when the bot
    # dies and respawns (cur_damage resets to 0) the tracker follows the
    # drop and the next damage tick correctly retriggers a flee.
    last_damage_va    = layout.va('bot_last_damage')
    flee_ticks_va     = layout.va('bot_flee_ticks')
    flee_frames_va    = layout.va('hazard_flee_frames')
    a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))            # ecx = bot char
    a.raw(b'\xD9\x41\x7C')                                # fld dword [ecx+0x7C] — cur_damage
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xD8\x14\x8D' + le32(last_damage_va))         # fcom [last_damage+slot*4]
    a.raw(b'\xDF\xE0')                                    # fnstsw ax
    a.raw(b'\x9E')                                        # sahf
    a.raw(b'\xD9\x1C\x8D' + le32(last_damage_va))         # fstp [last_damage+slot*4]
    a.jbe('s542360_no_damage')                            # current <= last → no damage

    # Damage taken — but only enter a NEW flee if we're not already
    # committed to one. While flee_ticks > 0 we just stay the course.
    a.raw(b'\x8B\x04\x8D' + le32(flee_ticks_va))          # eax = flee_ticks[slot]
    a.raw(b'\x85\xC0'); a.jnz('s542360_no_damage')        # already fleeing — skip

    # Compute |motion|². If essentially zero (bot was stationary), fall
    # back to a forced random retarget — biasing zero is undefined.
    a.raw(b'\xD9\x05' + le32(motion_dx_va))               # fld motion_dx
    a.raw(b'\xD8\xC8')                                    # fmul (dx²)
    a.raw(b'\xD9\x05' + le32(motion_dy_va))               # fld motion_dy
    a.raw(b'\xD8\xC8')                                    # fmul (dy²)
    a.raw(b'\xDE\xC1')                                    # faddp -> |motion|²
    a.raw(b'\xD9\xEE')                                    # fldz   (ST0=0, ST1=|m|²)
    a.raw(b'\xDA\xE9')                                    # fucompp (pop both, set flags)
    a.raw(b'\xDF\xE0')                                    # fnstsw ax
    a.raw(b'\x9E')                                        # sahf
    a.jae('s542360_flee_motionless')                      # 0 >= |m|² → stationary

    # Normal flee: wander = pos - (motion / |motion|) * WANDER_RADIUS.
    a.raw(b'\xD9\x05' + le32(motion_dx_va))               # fld dx
    a.raw(b'\xD8\xC8')
    a.raw(b'\xD9\x05' + le32(motion_dy_va))               # fld dy
    a.raw(b'\xD8\xC8')
    a.raw(b'\xDE\xC1')                                    # ST0 = |m|²
    a.raw(b'\xD9\xFA')                                    # fsqrt -> |m|
    a.raw(b'\xD9\x05' + le32(layout.va('wander_target_radius')))  # fld R
    a.raw(b'\xDE\xF1')                                    # fdivrp st(1), st  ->  ST0 = R/|m|
    # x axis
    a.raw(b'\xD9\x05' + le32(motion_dx_va))               # ST0 = dx, ST1 = scale
    a.raw(b'\xD8\xC9')                                    # fmul st, st(1)    -> ST0 = dx*scale
    a.raw(b'\xD9\xE0')                                    # fchs              -> ST0 = -dx*scale
    a.raw(b'\xD8\x05' + le32(bot_pos_va))                 # fadd pos.x        -> pos.x - dx*scale
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xD9\x1C\x8D' + le32(wander_x_va))            # fstp wander_x[slot]
    # y axis
    a.raw(b'\xD9\x05' + le32(motion_dy_va))               # ST0 = dy, ST1 = scale
    a.raw(b'\xD8\xC9')                                    # fmul st, st(1)
    a.raw(b'\xD9\xE0')                                    # fchs
    a.raw(b'\xD8\x05' + le32(bot_pos_va + 4))             # fadd pos.y
    a.raw(b'\xD9\x1C\x8D' + le32(wander_y_va))            # fstp wander_y[slot]
    a.raw(b'\xDD\xD8')                                    # fstp st(0)        (drop leftover scale)
    a.jmp('s542360_flee_commit')

    a.label('s542360_flee_motionless')
    # Stationary fallback — let the next random retarget pick a fresh
    # direction. flee_ticks stays 0 so a subsequent damage tick can still
    # try the biased path once the bot starts moving.
    a.raw(b'\xC7\x04\x8D' + le32(wander_ticks_va) + le32(0))
    a.raw(b'\xC7\x04\x8D' + le32(stuck_count_va) + le32(0))
    a.jmp('s542360_no_damage')

    a.label('s542360_flee_commit')
    # Commit the flee target: lock wander_ticks for the same duration so
    # the regular timer-based retarget can't pick a random direction back
    # onto the hazard; clear stuck_count so a fresh-flee bot isn't
    # immediately tagged stuck against the lava edge.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xA1' + le32(flee_frames_va))                 # eax = HAZARD_FLEE_FRAMES
    a.raw(b'\x89\x04\x8D' + le32(flee_ticks_va))
    a.raw(b'\x89\x04\x8D' + le32(wander_ticks_va))
    a.raw(b'\xC7\x04\x8D' + le32(stuck_count_va) + le32(0))

    a.label('s542360_no_damage')

    # Tick flee_ticks down one notch per frame (regardless of damage).
    # When it hits 0 the next damage event can start a new flee.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\x8D' + le32(flee_ticks_va))          # eax = flee_ticks
    a.raw(b'\x85\xC0'); a.jz('s542360_flee_done')
    a.raw(b'\x48')                                        # dec eax
    a.raw(b'\x89\x04\x8D' + le32(flee_ticks_va))
    a.label('s542360_flee_done')

    # --- Retarget if stuck OR timer expired ----------------------------
    a.raw(b'\x8B\x04\x8D' + le32(stuck_count_va))         # eax = stuck_count[slot]
    a.raw(b'\x3B\x05' + le32(stuck_threshold_va))
    a.jae('s542360_retarget')
    a.raw(b'\x8B\x04\x8D' + le32(wander_ticks_va))        # eax = wander_ticks[slot]
    a.raw(b'\x85\xC0'); a.jnz('s542360_tick_down')

    a.label('s542360_retarget')
    _emit_rng_axis(a, layout, wander_x_va, bot_pos_va)
    _emit_rng_axis(a, layout, wander_y_va, bot_pos_va + 4)
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x05' + le32(wander_timeout_va))          # eax = timeout
    a.raw(b'\x89\x04\x8D' + le32(wander_ticks_va))        # wander_ticks[slot] = timeout
    a.raw(b'\xC7\x04\x8D' + le32(stuck_count_va) + le32(0))
    a.jmp('s542360_target_done')

    a.label('s542360_tick_down')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot (in case clobbered)
    a.raw(b'\xFF\x0C\x8D' + le32(wander_ticks_va))        # --wander_ticks[slot]

    a.label('s542360_target_done')

    # --- Base accumulator: (dx, dy) = (wander - bot) -------------------
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xD9\x04\x8D' + le32(wander_x_va))            # fld wander_x[slot]
    a.raw(b'\xD8\x25' + le32(bot_pos_va))                 # fsub bot.x
    a.raw(b'\xD9\x1D' + le32(dx_accum_va))                # fstp dx_accum
    a.raw(b'\xD9\x04\x8D' + le32(wander_y_va))            # fld wander_y[slot]
    a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))             # fsub bot.y
    a.raw(b'\xD9\x1D' + le32(dy_accum_va))                # fstp dy_accum

    # --- Hazard repulse ---------------------------------------------
    # For each cached hazard within HAZARD_REPULSION_RADIUS_SQ:
    #   delta = bot_pos - hazard_pos
    #   scale = weight / sqrt(d²)
    #   dx_accum += delta.x * scale
    #   dy_accum += delta.y * scale
    a.raw(b'\x8B\x1D' + le32(hazard_count_va))            # ebx = hazard_count
    a.raw(b'\x85\xDB'); a.jz('s542360_no_hazards')
    a.raw(b'\x31\xFF')                                    # edi = idx = 0
    a.label('s542360_haz_loop')
    # esi = &hazard_table[idx] = base + idx*12.
    a.raw(b'\x8D\x34\x7F')                                # lea esi, [edi + edi*2]  (idx*3)
    a.raw(b'\xC1\xE6\x02')                                # shl esi, 2              (idx*12)
    a.raw(b'\x81\xC6' + le32(hazard_table_va))            # add esi, hazard_table

    # Compute d² = (bot.x - haz.x)² + (bot.y - haz.y)². Leave ST0 = d².
    a.raw(b'\xD9\x05' + le32(bot_pos_va))                 # fld bot.x
    a.raw(b'\xD8\x26')                                    # fsub [esi]  haz.x
    a.raw(b'\xD8\xC8')                                    # fmul st,st (dx²)
    a.raw(b'\xD9\x05' + le32(bot_pos_va + 4))             # fld bot.y
    a.raw(b'\xD8\x66\x04')                                # fsub [esi+4] haz.y
    a.raw(b'\xD8\xC8')                                    # fmul st,st (dy²)
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0 = d²
    # if d² >= HAZARD_REPULSION_RADIUS_SQ -> skip (popping via FCOMP).
    a.raw(b'\xD8\x1D' + le32(hazard_radius_va))           # FCOMP radius (pop)
    a.raw(b'\xDF\xE0')
    a.raw(b'\x9E')
    a.jae('s542360_haz_next')
    # In-range. Compute scale = weight / sqrt(d²). Recompute d² since FCOMP popped.
    a.raw(b'\xD9\x05' + le32(bot_pos_va))                 # fld bot.x
    a.raw(b'\xD8\x26')                                    # fsub haz.x  ST0 = dx
    a.raw(b'\xD9\x1D' + le32(layout.va('best_dx')))       # fstp best_dx (scratch dx tmp)
    a.raw(b'\xD9\x05' + le32(bot_pos_va + 4))             # fld bot.y
    a.raw(b'\xD8\x66\x04')                                # fsub haz.y  ST0 = dy
    a.raw(b'\xD9\x1D' + le32(layout.va('best_dy')))       # fstp best_dy (scratch dy tmp)
    # d² = dx² + dy²
    a.raw(b'\xD9\x05' + le32(layout.va('best_dx')))       # fld dx
    a.raw(b'\xD8\xC8')                                    # dx²
    a.raw(b'\xD9\x05' + le32(layout.va('best_dy')))       # fld dy
    a.raw(b'\xD8\xC8')                                    # dy²
    a.raw(b'\xDE\xC1')                                    # faddp -> d²
    a.raw(b'\xD9\xFA')                                    # fsqrt -> |d|        (ST0=|d|, ST1=...nope, ST0=|d|)
    a.raw(b'\xD9\x05' + le32(hazard_weight_va))           # fld weight          (ST0=w, ST1=|d|)
    # fdivrp st(1), st: ST(1) := ST(0)/ST(1) = w/|d|, pop. Result ST0 = w/|d|.
    # (fdivp would give |d|/w, which is the wrong direction — closer hazard
    # would push LESS, and far hazards would push more.)
    a.raw(b'\xDE\xF1')                                    # fdivrp st(1), st
    # Apply scale to dx, dy and add into accumulators.
    a.raw(b'\xD9\x05' + le32(layout.va('best_dx')))       # fld dx
    a.raw(b'\xD8\xC9')                                    # fmul st, st(1)  -> ST0=dx*scale
    a.raw(b'\xD8\x05' + le32(dx_accum_va))                # fadd dx_accum
    a.raw(b'\xD9\x1D' + le32(dx_accum_va))                # fstp dx_accum
    a.raw(b'\xD9\x05' + le32(layout.va('best_dy')))       # fld dy
    a.raw(b'\xD8\xC9')                                    # fmul st, st(1)
    a.raw(b'\xD8\x05' + le32(dy_accum_va))                # fadd dy_accum
    a.raw(b'\xD9\x1D' + le32(dy_accum_va))                # fstp dy_accum
    a.raw(b'\xDD\xD8')                                    # fstp st(0)  (drop leftover scale)

    a.label('s542360_haz_next')
    a.raw(b'\x47')                                        # inc edi
    a.raw(b'\x39\xDF')                                    # cmp edi, ebx
    a.jb('s542360_haz_loop')
    a.label('s542360_no_hazards')

    # --- Item attractor: refresh cache on stagger, blend if valid -------
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x05' + le32(frame_counter_va))           # eax = frame_counter
    a.raw(b'\x2B\x04\x8D' + le32(last_item_scan_va))      # sub eax, last_item_scan[slot]
    a.raw(b'\x3B\x05' + le32(item_scan_interval_va))      # cmp eax, interval
    a.jb('s542360_skip_scan')
    a.call_lbl('pick_pickup')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # reload slot
    a.raw(b'\x8B\x05' + le32(frame_counter_va))
    a.raw(b'\x89\x04\x8D' + le32(last_item_scan_va))
    a.label('s542360_skip_scan')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x83\x3C\x8D' + le32(pickup_valid_va) + b'\x00')  # cmp valid[slot], 0
    a.jz('s542360_no_pickup')
    # dx_accum += WEIGHT * (pickup_x[slot] - bot.x)
    a.raw(b'\xD9\x04\x8D' + le32(pickup_x_cache_va))      # fld pickup.x[slot]
    a.raw(b'\xD8\x25' + le32(bot_pos_va))                 # fsub bot.x
    a.raw(b'\xD8\x0D' + le32(item_weight_va))             # fmul weight
    a.raw(b'\xD8\x05' + le32(dx_accum_va))                # fadd dx_accum
    a.raw(b'\xD9\x1D' + le32(dx_accum_va))                # fstp dx_accum
    # dy_accum += WEIGHT * (pickup_y[slot] - bot.y)
    a.raw(b'\xD9\x04\x8D' + le32(pickup_y_cache_va))      # fld pickup.y[slot]
    a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))             # fsub bot.y
    a.raw(b'\xD8\x0D' + le32(item_weight_va))             # fmul weight
    a.raw(b'\xD8\x05' + le32(dy_accum_va))                # fadd dy_accum
    a.raw(b'\xD9\x1D' + le32(dy_accum_va))                # fstp dy_accum
    a.label('s542360_no_pickup')

    # --- Normalize and scale by BOT_MOVE_SPEED ----------------------
    # len² = dx² + dy²; if len² == 0, emit zero.
    a.raw(b'\xD9\x05' + le32(dx_accum_va))                # fld dx
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xD9\x05' + le32(dy_accum_va))                # fld dy
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0 = len²
    # Compare len² against zero. ST0 = len², we want to branch if len² == 0.
    a.raw(b'\xD9\xEE')                                    # fldz             ST0=0, ST1=len²
    a.raw(b'\xDF\xF1')                                    # fcomip st, st(1) compare 0 vs len², pop 0
    a.raw(b'\x9E')                                        # sahf  (fcomip already set EFLAGS but cheap)
    a.raw(b'\xDD\xD8')                                    # fstp st(0)       drop the leftover len² so stack is clean
    # JZ if len² == 0 (zero -> degenerate). Using JNB (len² <= 0) is safe
    # because len² is always >= 0; equality is the only degenerate case.
    a.raw(b'\xD9\x05' + le32(dx_accum_va))                # fld dx
    a.raw(b'\xD8\xC8')                                    # dx²
    a.raw(b'\xD9\x05' + le32(dy_accum_va))                # fld dy
    a.raw(b'\xD8\xC8')                                    # dy²
    a.raw(b'\xDE\xC1')                                    # faddp -> len²
    a.raw(b'\xD9\xEE')                                    # fldz
    a.raw(b'\xDA\xE9')                                    # fucompp (pop both, set EFLAGS)
    a.raw(b'\xDF\xE0')
    a.raw(b'\x9E')
    # After comparing 0 and len² (in that stack order), C3=Z, C0=P, C2=U.
    # JAE (CF=0) means 0 >= len² -> degenerate (len² is 0).
    a.jae('s542360_zero')
    # Compute scale = speed / sqrt(len²).
    a.raw(b'\xD9\x05' + le32(dx_accum_va))
    a.raw(b'\xD8\xC8')
    a.raw(b'\xD9\x05' + le32(dy_accum_va))
    a.raw(b'\xD8\xC8')
    a.raw(b'\xDE\xC1')                                    # ST0 = len²
    a.raw(b'\xD9\xFA')                                    # fsqrt -> |len|      (ST0=|len|)
    a.raw(b'\xD9\x05' + le32(bot_move_speed_va))          # fld speed           (ST0=speed, ST1=|len|)
    # fdivrp st(1), st: ST(1) := ST(0)/ST(1) = speed/|len|, pop.  Result ST0
    # = scale.  Multiplying (dx, dy) by this gives an output magnitude of
    # `speed`.  (fdivp here was the original bug — it computed |len|/speed,
    # making the bot teleport off the map at ~|len|² per frame and crash the
    # engine's collision lookup in sub_4303F0.)
    a.raw(b'\xDE\xF1')                                    # fdivrp st(1), st
    # Apply scale to dx, dy and store back.
    a.raw(b'\xD9\x05' + le32(dx_accum_va))                # fld dx
    a.raw(b'\xD8\xC9')                                    # fmul st, st(1)
    a.raw(b'\xD9\x1D' + le32(dx_accum_va))                # fstp dx (now vx)
    a.raw(b'\xD9\x05' + le32(dy_accum_va))                # fld dy
    a.raw(b'\xD8\xC9')                                    # fmul st, st(1)
    a.raw(b'\xD9\x1D' + le32(dy_accum_va))                # fstp dy (now vy)
    a.raw(b'\xDD\xD8')                                    # fstp st(0)  (drop scale)

    # --- Emit velocity to [esp+4] and angle to [esp+8] ----------------
    # NOTE: args are now at [esp+0x24]/[esp+0x28] because pushad above
    # added 32 bytes (0x20) of saved registers between ESP and the return
    # frame.
    a.raw(b'\x8B\x44\x24\x24')                            # eax = out_vec  (esp+36)
    a.raw(b'\x85\xC0'); a.jz('s542360_skip_vec_out')
    a.raw(b'\x8B\x0D' + le32(dx_accum_va))                # ecx = vx bits
    a.raw(b'\x89\x08')                                    # *out_vec     = vx
    a.raw(b'\x8B\x0D' + le32(dy_accum_va))                # ecx = vy bits
    a.raw(b'\x89\x48\x04')                                # *(out_vec+4) = vy
    a.label('s542360_skip_vec_out')
    a.raw(b'\x8B\x44\x24\x28')                            # eax = out_angle (esp+40)
    a.raw(b'\x85\xC0'); a.jz('s542360_ret')
    a.raw(b'\xFF\x35' + le32(dy_accum_va))                # push dy (a1 → atan2(dy, dx))
    a.raw(b'\xFF\x35' + le32(dx_accum_va))                # push dx (a2)
    a.call_va(ax.SUB_509100)                              # __stdcall, st0 = angle, pops 8
    a.raw(b'\x8B\x44\x24\x28')                            # reload out_angle (esp+40)
    a.raw(b'\xD9\x18')                                    # fstp dword [eax]
    a.jmp('s542360_ret')

    # --- Zero-vector return (panic / NULL char / degenerate normalize) ---
    a.label('s542360_zero')
    a.raw(b'\x8B\x44\x24\x24')                            # esp+36
    a.raw(b'\x85\xC0'); a.jz('s542360_zero_skip_vec')
    a.raw(b'\xC7\x00\x00\x00\x00\x00')                    # out_vec[0] = 0
    a.raw(b'\xC7\x40\x04\x00\x00\x00\x00')                # out_vec[1] = 0
    a.label('s542360_zero_skip_vec')
    a.raw(b'\x8B\x44\x24\x28')                            # esp+40
    a.raw(b'\x85\xC0'); a.jz('s542360_ret')
    a.raw(b'\xC7\x00\x00\x00\x00\x00')                    # *out_angle = 0

    a.label('s542360_ret')
    a.raw(b'\x61')                                        # popad — restore EBX/EBP/ESI/EDI etc.
    a.raw(b'\xC2\x14\x00')                                # ret 0x14

    a.label('s542360_normal')
    a.raw(ax.S542360_PROLOGUE)
    a.jmp_va(ax.S542360_RESUME)
