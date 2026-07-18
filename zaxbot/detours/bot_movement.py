"""``detour_542360`` — bot movement-vector synthesis (waypoint follower).

ECX = ``CPlayerWalkingControlAI``. We identify bot controllers via the
controller's ``player_num`` at ``[ecx+0x1C]`` matched against
``bot_indices[]``. Host controllers fall through to the original prologue.

## Why this is a full rewrite

The engine caller ``sub_543B60`` (calls us at ``0x543ced``) consumes our two
outputs very asymmetrically — confirmed by decompiling it:

  - The character's actual movement DIRECTION is ``cur_pos +
    100*(cos(angle), sin(angle))`` fed to ``sub_4303F0``. **Direction comes
    only from the angle output ``[esp+8]``.**
  - The velocity vector ``[esp+4]`` matters only through its MAGNITUDE, which
    selects the idle/walk/run animation tier and the per-frame step. Its
    direction is ignored by the engine.
  - ``sub_4303F0`` is ALL-OR-NOTHING: if the angle points into geometry its
    collision sweep fails and the bot does **not move at all** — there is no
    engine wall-slide.

The previous implementation synthesized the angle from a potential field
(random wander + hazard repulse + pickup attractor) and an edge look-ahead,
then MIRRORED ``sub_542360``'s own "wall block" post-process (zero the vector
and face away when pushing into a wall). For a human that freeze is fine — the
player manually steers parallel to slide. A bot has nobody to steer it, so it
froze against the wall until a 150-frame timeout. That mirrored freeze, plus
the field perturbations and corner-cutting look-ahead constantly aiming the
angle INTO walls, is why bots stuck on walls. No amount of tuning fixed it
because the architecture was wrong.

## The new model

Pure node-to-node graph following with a reactive, engine-independent
wall-slide:

  1. **Steer straight at the current node.** ``desired = node_pos - bot_pos``;
     emit ``velocity = normalize(desired) * BOT_MOVE_SPEED`` (magnitude only,
     keeps the engine out of Idle) and ``angle = atan2(desired)``.
  2. **Arrival + edges.** When ``dsq(bot, node) < WP_REACHED_RADIUS_SQ`` the
     bot advances along a real edge via ``wp_advance`` (a RANDOM connected
     neighbour, preferring ``!= prev``), so it roams the whole graph while
     strictly respecting connections.
  3. **Wall-slide (no freeze).** We do NOT re-emit the engine's freeze. When
     the bot is physically not moving for ``WP_SLIDE_TRIGGER_FRAMES`` (its
     desired angle points into a wall and ``sub_4303F0`` refuses to move it),
     we sweep the emitted ANGLE by ``WP_SLIDE_TURN_STEP`` per ramp step until a
     heading clears the wall and the bot slides along it. The velocity
     magnitude stays ``BOT_MOVE_SPEED`` throughout, so the engine keeps walking
     the bot in the deflected direction. Once moving freely the deflection
     decays back toward straight-at-node. This needs no engine internals and is
     guaranteed to find a clear heading (it sweeps a full half-plane and more).
  4. **Respawn / death.** A (re)spawned bot drops its latch and re-acquires the
     NEAREST node; edges constrain only after that first pick.

For diagnostics, the controller's block vector at ``+0x14/+0x18`` is mirrored
into the (now dormant) ``bot_wander_x/y[slot]`` fields so an ``ai_move`` R-dump
reveals whether the engine populates it near walls — the data needed to later
add a smoother geometric slide (project the heading onto the wall tangent)
on top of this guaranteed-correct angle sweep.

``MOVEMENT_ENABLED`` is a panic switch reverting to the zero-vector behavior.
``WP_FOLLOW_ENABLED``/no graph ⇒ bots idle (the random-wander potential field
was removed). Output convention: ``[esp+4]`` velocity out, ``[esp+8]`` angle
out, ``ret 0x14``.

## Module structure

``emit`` assembles one contiguous detour body, but the source is split into
cohesive ``_emit_*`` stages that append to the same ``Asm`` cursor in section
order. The stages do NOT communicate through Python return values — they
share state through the ``.zaxbot`` scratch fields and the single ``pushad``
frame established by ``_emit_identify_and_setup``. Splitting at the labelled
join points keeps the emitted bytes identical to one flat function (pinned by
the golden-section test) while letting each concern — pickup-divert, waypoint
follow, normalize/emit, wall-slide — be read and reviewed in isolation.

### Dormant-field aliases

Several per-bot scratch fields keep their old (random-wander era) NAMES for
offset stability but are repurposed by this detour. The aliases are resolved
once per stage via ``layout.va('<old-name>')`` with a ``# <new-meaning>``
comment; the canonical map also lives on ``layout.AI_PERBOT_FIELDS``:

  - ``bot_pickup_y_cache`` -> ``wp_best_dsq``   (min dsq-to-node seen so far)
  - ``bot_pickup_x_cache`` -> ``bot_last_char`` (respawn detection)
  - ``bot_pickup_valid``   -> ``failed_edge_marker`` (packed blocked edge)
  - ``bot_flee_ticks``     -> ``slide_turn``    (wall-slide deflection ramp)
  - ``bot_wander_x/y``     -> block-vector diagnostic mirror
  - ``curr_dist_sq``/``cand_tmp`` -> ``dx_accum``/``dy_accum`` (borrowed
    fire/aim scratch, mutually exclusive with this detour)
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..hook.bot_lookup import emit_addr_to_slot, emit_is_bot_controller
from ..layout import ScratchLayout


# --- Wall-slide tuning (asm immediates; the angle step is a runtime knob) ----
# Trigger primarily on LACK OF PROGRESS (wp_try), which catches both freeze and
# slide-along-wall-without-approach. A pure position-delta stuck_count is not
# enough for wall grinding, but it is a useful secondary backstop for the fully
# stationary case seen in R dumps where reacquiring the same nearest node kept
# resetting wp_try before the sweep could finish a full circle.
# The circling that a pure wp_try sweep caused is now bounded by the RETREAT
# below: the sweep only runs for WP_RETREAT_TIMEOUT-WP_SLIDE_TRIGGER frames, then
# the bot backs up to the previous (reachable) node instead of orbiting forever.
WP_SLIDE_TRIGGER_FRAMES = 8
# Heading steps in a full sweep (12 * 30 = 360): a blocked bot tries every
# direction to find one that frees it. slide_turn cycles 0..CAP-1 and wraps.
WP_SLIDE_TURN_CAP = 12
# Advance the sweep one heading step every (MASK+1) frames (3 => 4 frames per
# direction) so each candidate heading is held long enough to actually move.
WP_SLIDE_SWEEP_MASK = 3

# Proactive lava veto: max candidate headings tried in one frame when the
# emitted heading would step into lava. The veto rotates by cfg.LAVA_SWEEP_STEP
# per try until a lava-clear heading is found; 12 * 30deg = a full circle.
LAVA_SWEEP_COUNT = 12


def emit(a: Asm, layout: ScratchLayout) -> None:
    """Assemble ``detour_542360`` as one contiguous body.

    Each ``_emit_*`` stage appends in section order; together they form the
    single instruction stream the engine jumps into. Order is load-bearing
    (it fixes label positions / fall-through), so do not reorder without
    re-establishing the byte-identity baseline."""
    _emit_identify_and_setup(a, layout)
    _emit_stuck_detection(a, layout)
    _emit_reactive_lava_flee(a, layout)
    _emit_pickup_divert(a, layout)
    _emit_waypoint_follow(a, layout)
    _emit_normalize_and_emit(a, layout)
    _emit_wall_slide(a, layout)
    _emit_portal_veto(a, layout)
    _emit_plasma_veto(a, layout)
    _emit_dead_and_zero_return(a, layout)
    _emit_normal_fallthrough(a)


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
        if layout.has_field('bot_portal_target'):
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_portal_target')) + le32(0))
        if layout.has_field('bot_drop_target'):
            # A teleported bot's latched dropped flag is usually a whole arena
            # away now — drop the pursuit (the entry scan re-latches if it is
            # genuinely still nearby at the exit).
            a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_drop_target')) + le32(0))
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


def _emit_waypoint_follow(a: Asm, layout: ScratchLayout) -> None:
    """Pure node-to-node follow: acquire the nearest node, test arrival, run
    the off-graph progress watchdog (retreat/re-acquire), advance along a real
    edge on arrival, and stage ``desired = node - bot`` into dx/dy for the
    normalize stage. No graph / follow disabled -> zero (idle)."""
    bot_pos_va        = layout.va('bot_pos')
    bot_slot_tmp_va   = layout.va('bot_slot_tmp')
    current_wp_va     = layout.va('bot_current_wp')
    prev_wp_va        = layout.va('bot_prev_wp')
    wp_try_va         = layout.va('bot_wp_try')
    wp_best_dsq_va    = layout.va('bot_pickup_y_cache')   # min dsq-to-node
    stuck_count_va    = layout.va('bot_stuck_count')
    failed_edge_va    = layout.va('bot_pickup_valid')     # packed failed-edge marker
    slide_turn_va     = layout.va('bot_flee_ticks')       # wall-slide ramp
    wp_follow_enabled_va    = layout.va('wp_follow_enabled')
    wp_reached_radius_sq_va = layout.va('wp_reached_radius_sq')
    wp_progress_timeout_va  = layout.va('wp_progress_timeout')
    wp_stuck_reached_radius_sq_va = layout.va('wp_relocate_frames')  # repurposed dormant slot
    failed_cur_tmp_va = layout.va('curr_dist_sq')       # timeout spill: failed current node
    prev_tmp_va       = layout.va('cand_tmp')           # timeout spill: previous node
    overlay_vertex_count_va = layout.va('overlay_vertex_count')
    overlay_vertices_va     = layout.va('overlay_vertices')
    wp_scratch_va           = layout.va('wp_scratch')
    edge_follow_enabled_va  = layout.va('wp_edge_follow_enabled')
    edge_lookahead_va       = layout.va('wp_edge_lookahead')
    wp_seg_x_va             = layout.va('wp_seg_x')
    wp_seg_y_va             = layout.va('wp_seg_y')
    wp_tp_va                = layout.va('wp_tp')

    dx_accum_va = layout.va('curr_dist_sq')
    dy_accum_va = layout.va('cand_tmp')

    # CTF final-approach: route_goal_flag (this bot's goal flag idx), its nearest
    # graph node, and the flag base position. Present only on a routing build.
    routing = (cfg.CTF_FLAG_ROUTING_ENABLED
               and layout.has_field('route_goal_flag')
               and layout.has_field('flag_route_node')
               and layout.has_field('flag_table')
               and layout.has_field('flag_routing_active'))
    if routing:
        route_goal_flag_va = layout.va('route_goal_flag')
        flag_route_node_va = layout.va('flag_route_node')
        flag_table_va      = layout.va('flag_table')
        routing_active_va  = layout.va('flag_routing_active')
        route_suspend_va   = layout.va('bot_route_suspend')
        route_block_hits_va = layout.va('route_block_hits')

    # Switch-seek final approach: a bot descending the seek field whose node
    # IS the sought switch's node steers at the switch CENTER to bump it.
    seek_move = (routing
                 and cfg.SWITCH_SEEK_ENABLED
                 and layout.has_field('bot_seek')
                 and layout.has_field('seek_active')
                 and layout.has_field('seek_node')
                 and layout.has_field('switch_table')
                 and layout.has_field('bot_team'))
    if seek_move:
        bot_seek_va     = layout.va('bot_seek')
        seek_active_va  = layout.va('seek_active')
        seek_node_va    = layout.va('seek_node')
        switch_table_va = layout.va('switch_table')
        bot_team_va     = layout.va('bot_team')

    # Door-aware failed-edge handling (detection layer consumer). When the
    # progress watchdog marks a failed edge, door_capture_wedge latches the
    # nearest currently-blocked door to the wedged bot; the fast-retry check
    # below then clears the marker the moment that door reads passable again
    # (periodic grid scan), instead of waiting out the blind
    # WP_ROUTE_BLOCK_RETRY_HITS cadence — the exact residual grind loop seen
    # live on Hydroplant Bouncefest (marker held long after the door opened).
    door_gate = (cfg.DOOR_DETECT_ENABLED
                 and layout.has_field('route_block_door')
                 and layout.has_field('door_blocked')
                 and layout.has_field('door_count'))
    if door_gate:
        route_block_door_va = layout.va('route_block_door')
        door_blocked_va     = layout.va('door_blocked')
        door_count_va       = layout.va('door_count')

    # Closed-door commitment recovery. The door-BLIND commit paths (cold-acquire
    # nearest node, reacquire, retreat) and any next-hop made while a door was
    # open can leave a bot latched onto a target node it must reach ACROSS a
    # now-closed door. Only ctf_next_hop is door-aware, and it re-runs only on
    # ARRIVAL — which never happens when the closed door blocks the last leg —
    # so the bot grinds the door until death/respawn (live-reported edge case;
    # R-snapshots showed a bot on the prev side of closed pillar gates with
    # current_wp on the far node). When this fires we re-route door-aware from
    # the reachable side. Needs the edge list + per-edge door binding.
    door_reroute = (routing and door_gate
                    and layout.has_field('edge_door')
                    and layout.has_field('overlay_edges')
                    and layout.has_field('overlay_edge_count'))
    if door_reroute:
        edge_door_va         = layout.va('edge_door')
        overlay_edges_va     = layout.va('overlay_edges')
        overlay_edge_count_va = layout.va('overlay_edge_count')

    # Portal pad approach + roam wander-entry. bot_portal_target[slot] (pad
    # idx+1) is latched by a routed portal hop (ctf_next_hop via
    # route_portal_hop) or by the roam-time portal_wander_check roll; while
    # latched the follower steers at the PAD CENTER through the same watchdog
    # as the flag final approach. The latch ends via the teleport-jump detect
    # (the pad fired), a progress timeout (pad unreachable/inactive — clears
    # the latch and suspends routing so the next arrivals roam), a stale pad
    # index, or the pad reading inactive. Emitted whenever the portal-routing
    # scratch fields exist; the runtime knobs gate behaviour.
    portal_move = (layout.has_field('bot_portal_target')
                   and layout.has_field('portal_node')
                   and layout.has_field('portal_table')
                   and layout.has_field('route_portal_hop'))
    if portal_move:
        bot_portal_target_va = layout.va('bot_portal_target')
        portal_table_va      = layout.va('portal_table')
        portal_count_va      = layout.va('portal_count')
        route_portal_hop_va  = layout.va('route_portal_hop')
        portal_active_mv_va  = (layout.va('portal_active')
                                if layout.has_field('portal_active') else 0)

    # Dropped-flag pursuit (v2 — two-phase). While a flag is away from its
    # base the periodic grid walk records its dropped world copy's position +
    # nearest graph node (name-matched, see entity_scan.py). A latched bot
    # ROUTES to the drop through the graph (drop_next_hop descends the
    # per-drop drop_dist BFS row at each node arrival — see flag_route.py)
    # and only steers STRAIGHT at the copy within drop_direct_radius_sq or
    # when standing on the drop's own bound node, through the standard
    # watchdog with press-patience. Latching: within drop_pursue_radius_sq
    # opportunistically, or from ANY distance when the drop is this bot's
    # missing GOAL flag (route_missing_goal — attackers whose steal target
    # dropped, carriers whose home flag dropped: the position is known, so
    # the old blind search/wait roam is replaced by a real route). The v1
    # straight-steer-only pursuit was live-diagnosed timing out after one
    # 30-frame watchdog window and cooling down 4 s — the reported "runs at
    # it, then ignores it" loop — and it beelined into walls when the drop
    # sat behind one. Touching a dropped flag is beneficial for EITHER team,
    # so there is no team/carry filter. Deliberately NOT gated on
    # bot_route_suspend for the DIRECT phase (touching a nearby flag is pure
    # upside); the ROUTED next-hop override does respect suspension (the
    # suspension roam exists to unstick deterministic routing).
    drop_move = (routing
                 and cfg.CTF_DROPPED_FLAG_ENABLED
                 and layout.has_field('flag_drop_valid')
                 and layout.has_field('flag_drop_pos')
                 and layout.has_field('flag_drop_node')
                 and layout.has_field('bot_drop_target')
                 and layout.has_field('bot_drop_cd')
                 and layout.has_field('bot_drop_try')
                 and layout.has_field('bot_drop_best')
                 and layout.has_field('drop_pursue_enabled')
                 and layout.has_field('route_missing_goal')
                 and layout.has_field('flag_present')
                 and layout.has_field('flag_count'))
    if drop_move:
        flag_drop_valid_mv_va = layout.va('flag_drop_valid')
        flag_drop_pos_mv_va   = layout.va('flag_drop_pos')
        flag_drop_node_mv_va  = layout.va('flag_drop_node')
        bot_drop_target_va    = layout.va('bot_drop_target')
        bot_drop_cd_va        = layout.va('bot_drop_cd')
        bot_drop_try_va       = layout.va('bot_drop_try')
        bot_drop_best_va      = layout.va('bot_drop_best')
        drop_enabled_va       = layout.va('drop_pursue_enabled')
        drop_radius_va        = layout.va('drop_pursue_radius_sq')
        drop_reached_va       = layout.va('drop_reached_radius_sq')
        drop_direct_va        = layout.va('drop_direct_radius_sq')
        drop_abandon_va       = layout.va('drop_abandon_radius_sq')
        drop_missing_goal_va  = layout.va('route_missing_goal')
        flag_present_mv_va    = layout.va('flag_present')
        flag_count_mv_va      = layout.va('flag_count')

    # === Waypoint following =============================================
    a.raw(b'\x83\x3D' + le32(wp_follow_enabled_va) + b'\x00')
    a.jz('s542360_fallback_zero')
    a.raw(b'\x83\x3D' + le32(overlay_vertex_count_va) + b'\x00')
    a.jz('s542360_fallback_zero')

    if door_gate:
        # Fast retry: marker set + a door latched + that door now passable
        # -> clear the marker (and the ping-pong budget) so the next arrival
        # retries the edge immediately. A stale latch (map changed under it)
        # only resets the latch; the marker keeps its blind-retry cadence.
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
        a.raw(b'\x83\x3C\x8D' + le32(failed_edge_va) + b'\x00')  # marker set?
        a.jz('s542360_door_fc_done')
        a.raw(b'\x8B\x04\x8D' + le32(route_block_door_va))    # eax = latched door idx
        a.raw(b'\x83\xF8\xFF')                                # -1 = none
        a.jz('s542360_door_fc_done')
        a.raw(b'\x3B\x05' + le32(door_count_va))              # stale idx?
        a.jae('s542360_door_fc_stale')
        a.raw(b'\x83\x3C\x85' + le32(door_blocked_va) + b'\x00')  # door still closed?
        a.jnz('s542360_door_fc_done')                         # yes -> keep marker
        a.raw(b'\xC7\x04\x8D' + le32(failed_edge_va) + le32(0))   # door opened: retry edge
        if routing:
            a.raw(b'\xC7\x04\x8D' + le32(route_block_hits_va) + le32(0))
        a.label('s542360_door_fc_stale')
        a.raw(b'\xC7\x04\x8D' + le32(route_block_door_va)
              + b'\xFF\xFF\xFF\xFF')                          # drop the latch
        a.label('s542360_door_fc_done')

    # Door-state reroute trigger. rebuild_open_routes bumps route_epoch each
    # time a door open/close rebuilds the open-field BFS. Routing otherwise
    # only re-evaluates on node ARRIVAL (ctf_next_hop fires at s542360_wp_
    # arrived), so a bot steering across a door that opens mid-edge stays
    # committed to the old, now-suboptimal path until it dies and respawns (a
    # bot pressed against a still-closed door never arrives at a node to
    # re-route). When this bot's stored epoch lags the global, invalidate
    # current_wp so the cold-acquire below re-runs THIS think and ctf_next_hop
    # picks the newly-opened route. Gated on active CTF routing; debounced to
    # at most once per DOOR_ROUTE_REBUILD_COOLDOWN_FRAMES by the rebuild itself.
    if routing and layout.has_field('route_epoch') and layout.has_field('bot_route_epoch'):
        route_epoch_va = layout.va('route_epoch')
        bot_route_epoch_va = layout.va('bot_route_epoch')
        a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00')  # routing active?
        a.jz('s542360_epoch_done')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))             # ecx = slot
        a.raw(b'\xA1' + le32(route_epoch_va))                  # eax = route_epoch
        a.raw(b'\x3B\x04\x8D' + le32(bot_route_epoch_va))      # cmp eax, bot_route_epoch[slot]
        a.jz('s542360_epoch_done')
        a.raw(b'\x89\x04\x8D' + le32(bot_route_epoch_va))      # bot_route_epoch[slot] = epoch
        a.raw(b'\xC7\x04\x8D' + le32(current_wp_va)
              + b'\xFF\xFF\xFF\xFF')                           # invalidate -> cold re-acquire
        a.label('s542360_epoch_done')

    # Ensure current_wp is a valid index, else cold-acquire the nearest node.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))          # eax = current_wp[slot]
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_wp_acquire')
    a.raw(b'\x3B\x05' + le32(overlay_vertex_count_va))    # cmp eax, [vertex_count]
    a.jb('s542360_wp_have_cur')                           # valid -> steer
    # fall through: out of range -> acquire

    a.label('s542360_wp_acquire')
    a.raw(b'\xA1' + le32(bot_pos_va))                     # stage bot pos -> wp_scratch
    a.raw(b'\xA3' + le32(wp_scratch_va))
    a.raw(b'\xA1' + le32(bot_pos_va + 4))
    a.raw(b'\xA3' + le32(wp_scratch_va + 4))
    a.call_lbl('wp_find_nearest')                         # ebx = nearest idx or -1
    a.raw(b'\x83\xFB\xFF')                                # cmp ebx, -1
    a.jz('s542360_fallback_zero')                         # empty graph -> idle
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # reload slot
    a.raw(b'\x89\x1C\x8D' + le32(current_wp_va))          # current_wp[slot] = nearest
    a.raw(b'\xC7\x04\x8D' + le32(prev_wp_va) + b'\xFF\xFF\xFF\xFF')   # prev_wp = -1
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))               # wp_try = 0
    a.raw(b'\xC7\x04\x8D' + le32(failed_edge_va) + le32(0))          # failed_edge_marker = 0
    if door_gate:
        a.raw(b'\xC7\x04\x8D' + le32(route_block_door_va) + b'\xFF\xFF\xFF\xFF')
    # fall through to wp_have_cur

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
    if door_reroute:
        # --- Closed-door commitment recovery -------------------------------
        # If the (prev -> cur) edge we are latched onto is bound to a currently
        # CLOSED door and we are still on the PREV side (nearer prev than cur, so
        # we have not crossed), the last leg is impassable and arrival — the only
        # thing that re-runs the door-aware ctf_next_hop — will never happen. Back
        # the target up to prev and jump into the arrival/advance path so
        # ctf_next_hop re-plans door-aware from the reachable node. Fires only in
        # exactly this stuck state (door open, or already crossed => no-op).
        a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00')  # routing active?
        a.jz('s542360_cdr_done')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
        a.raw(b'\x8B\x3C\x8D' + le32(prev_wp_va))             # edi = prev_wp[slot]
        a.raw(b'\x83\xFF\xFF')                                # cmp edi, -1
        a.jz('s542360_cdr_done')                              # not latched
        a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))          # eax = current_wp[slot]
        a.raw(b'\x39\xC7')                                    # cmp edi, eax (prev == cur?)
        a.jz('s542360_cdr_done')
        a.raw(b'\xA3' + le32(dy_accum_va))                    # spill cur -> dy_accum (cand_tmp)
        a.raw(b'\x31\xF6')                                    # esi = 0 (edge idx)
        a.label('s542360_cdr_scan')
        a.raw(b'\x3B\x35' + le32(overlay_edge_count_va))      # cmp esi, edge_count
        a.jae('s542360_cdr_done')                             # (prev,cur) edge not found
        a.raw(b'\x8B\x04\xB5' + le32(overlay_edges_va))       # eax = edges[esi]
        a.raw(b'\x0F\xB7\xD8')                                # movzx ebx, ax (i)
        a.raw(b'\xC1\xE8\x10')                                # shr eax, 16   (j)
        a.raw(b'\x39\xFB')                                    # cmp ebx, edi (i == prev?)
        a.jnz('s542360_cdr_swap')
        a.raw(b'\x3B\x05' + le32(dy_accum_va))                # cmp eax, cur (j == cur?)
        a.jz('s542360_cdr_found')
        a.jmp('s542360_cdr_next')
        a.label('s542360_cdr_swap')
        a.raw(b'\x3B\x1D' + le32(dy_accum_va))                # cmp ebx, cur (i == cur?)
        a.jnz('s542360_cdr_next')
        a.raw(b'\x39\xF8')                                    # cmp eax, edi (j == prev?)
        a.jz('s542360_cdr_found')
        a.label('s542360_cdr_next')
        a.raw(b'\x46')                                        # inc esi
        a.jmp('s542360_cdr_scan')
        a.label('s542360_cdr_found')
        a.raw(b'\x8B\x14\xB5' + le32(edge_door_va))           # edx = edge_door[esi]
        a.raw(b'\x83\xFA\xFF')                                # cmp edx, -1
        a.jz('s542360_cdr_done')                              # no door on this edge
        a.raw(b'\x3B\x15' + le32(door_count_va))              # cmp edx, door_count
        a.jae('s542360_cdr_done')                             # stale idx
        a.raw(b'\x83\x3C\x95' + le32(door_blocked_va) + b'\x00')  # door blocked?
        a.jz('s542360_cdr_done')                              # open -> normal handling
        # Closed door on the committed edge. dsq(bot,prev) vs dsq(bot,cur);
        # edi = prev idx still live, cur idx in dy_accum.
        a.raw(b'\x8D\x04\xFD' + le32(overlay_vertices_va))    # lea eax, [edi*8 + verts] (prev)
        a.raw(b'\xD9\x00')                                    # fld prev.x
        a.raw(b'\xD8\x25' + le32(bot_pos_va))                 # fsub bot.x
        a.raw(b'\xD8\xC8')                                    # fmul st,st
        a.raw(b'\xD9\x40\x04')                                # fld prev.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))             # fsub bot.y
        a.raw(b'\xD8\xC8')                                    # fmul st,st
        a.raw(b'\xDE\xC1')                                    # faddp -> dsq_prev
        a.raw(b'\xD9\x1D' + le32(wp_seg_x_va))                # fstp wp_seg_x = dsq_prev
        a.raw(b'\xA1' + le32(dy_accum_va))                    # eax = cur idx
        a.raw(b'\x8D\x04\xC5' + le32(overlay_vertices_va))    # lea eax, [eax*8 + verts] (cur)
        a.raw(b'\xD9\x00')                                    # fld cur.x
        a.raw(b'\xD8\x25' + le32(bot_pos_va))                 # fsub bot.x
        a.raw(b'\xD8\xC8')                                    # fmul st,st
        a.raw(b'\xD9\x40\x04')                                # fld cur.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))             # fsub bot.y
        a.raw(b'\xD8\xC8')                                    # fmul st,st
        a.raw(b'\xDE\xC1')                                    # faddp -> dsq_cur (st0)
        a.raw(b'\xD9\x05' + le32(wp_seg_x_va))                # fld dsq_prev (st0=prev, st1=cur)
        a.raw(b'\xDF\xF1')                                    # fcomip st0,st1 (CF=1 if prev<cur); pop
        a.raw(b'\xDD\xD8')                                    # fstp st(0) (drop dsq_cur; EFLAGS kept)
        a.jae('s542360_cdr_done')                             # nearer cur -> arrival handles it
        # Nearer prev: re-plan door-aware from prev by advancing from it.
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(prev_wp_va))             # eax = prev_wp[slot]
        a.raw(b'\x89\x04\x8D' + le32(current_wp_va))          # current_wp[slot] = prev
        a.jmp('s542360_wp_arrived')
        a.label('s542360_cdr_done')
    # --- CTF final approach -------------------------------------------------
    # Once the bot's current node IS the nearest node to its goal flag, the
    # graph can take it no closer — so steer straight at the actual flag base
    # position to physically touch it (grab the enemy flag, or deliver to own
    # base to capture). Without this the bot "arrives" at the node, ctf_next_hop
    # finds no closer neighbour, the random wp_advance fallback bounces it to a
    # neighbour and routing snaps it back -> it circles the node forever and
    # never reaches the flag. ctf_pick_goal recomputes the goal every frame, so
    # the instant the bot grabs the flag the goal flips to home and this branch
    # stops firing (cur != home goal node) -> normal routing resumes.
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
    a.jmp('s542360_wp_arrived')

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
    a.jmp('s542360_wp_arrived')

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
            a.jz('s542360_wp_door_impatient')             # no door wedge -> suspend
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
            a.label('s542360_wp_door_impatient')
            a.raw(b'\xC7\x04\x8D' + le32(bot_door_patience_va) + le32(0))
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
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
    a.jz('s542360_wp_steer')                              # preserve high wp_try + slide sweep
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
    a.jmp('s542360_wp_steer')

    a.label('s542360_wp_arrived')
    # Reached the node: advance to a CONNECTED neighbour (random; prefers !=
    # prev). When not latched (prev == -1) pass cur as prev so the advance
    # latches and any neighbour is acceptable.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x0C\x8D' + le32(current_wp_va))          # ecx = cur
    a.raw(b'\x51')                                        # push cur
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x14\x8D' + le32(prev_wp_va))             # edx = prev
    a.raw(b'\x59')                                        # pop ecx (= cur)
    a.raw(b'\x83\xFA\xFF')                                # cmp edx, -1
    a.jnz('s542360_wp_do_adv')
    a.raw(b'\x89\xCA')                                    # edx = cur (latch)
    a.label('s542360_wp_do_adv')
    # CTF goal-biased routing: in a CTF match, step one hop along the shortest
    # path toward the goal flag base (ctf_next_hop) instead of a random neighbour.
    # -1 (routing inactive / non-CTF / no progress here) => fall back to the
    # random wp_advance, byte-identical to the non-CTF behaviour. ECX=cur,
    # EDX=prev are live here; both are saved across ctf_next_hop so wp_advance
    # gets them back on the fallback path. If the progress watchdog marked this
    # edge as blocked, force the fallback and pass the blocked next-hop as the
    # "previous" node to wp_advance so random fallback also avoids it.
    if cfg.CTF_FLAG_ROUTING_ENABLED:
        a.raw(b'\x51')                                   # push cur (ecx)
        a.raw(b'\x52')                                   # push prev (edx)
        if drop_move:
            # Dropped-flag route override: while this bot's pursuit latch is
            # set and its routing is NOT suspended (the suspension roam
            # exists to unstick deterministic routing — don't override it),
            # descend the per-drop BFS row instead of the goal field. Falls
            # back to ctf_next_hop when the row can't apply (helper returns
            # -1: drop gone, row stale/unbuilt, node unreachable).
            a.raw(b'\xA1' + le32(bot_slot_tmp_va))       # eax = slot
            a.raw(b'\x83\x3C\x85' + le32(bot_drop_target_va) + b'\x00')
            a.jz('s542360_wp_use_cnh')
            a.raw(b'\x83\x3C\x85' + le32(route_suspend_va) + b'\x00')
            a.jnz('s542360_wp_use_cnh')
            a.call_lbl('drop_next_hop')                  # eax = drop hop or -1 (in: ecx=cur)
            a.raw(b'\x83\xF8\xFF')
            a.jnz('s542360_wp_hop_done')                 # got a drop hop
            a.raw(b'\x8B\x4C\x24\x04')                   # ecx = cur (helper clobbered it)
            a.label('s542360_wp_use_cnh')
            a.call_lbl('ctf_next_hop')                   # eax = goal next-hop or -1 (in: ecx=cur)
            a.label('s542360_wp_hop_done')
        else:
            a.call_lbl('ctf_next_hop')                   # eax = goal next-hop or -1 (in: ecx=cur)
        a.raw(b'\x5A')                                   # pop edx (prev)
        a.raw(b'\x59')                                   # pop ecx (cur)
        a.raw(b'\x83\xF8\xFF')                           # cmp eax, -1
        a.jz('s542360_wp_route_fallback')                # no route -> random neighbour
        if portal_move:
            # Routed PORTAL hop: ctf_next_hop parked the winning pad idx+1 in
            # route_portal_hop (and returned cur). Latch the pad approach —
            # the have_cur block takes over from the next think — and keep
            # current_wp on the pad's node so the graph latch stays sane.
            a.raw(b'\x83\x3D' + le32(route_portal_hop_va) + b'\x00')
            a.jz('s542360_wp_no_portal_hop')
            a.raw(b'\x8B\x1D' + le32(bot_slot_tmp_va))   # ebx = slot
            a.raw(b'\x8B\x35' + le32(route_portal_hop_va))  # esi = pad idx+1
            a.raw(b'\x89\x34\x9D' + le32(bot_portal_target_va))  # latch
            a.raw(b'\xC7\x05' + le32(route_portal_hop_va) + le32(0))
            a.raw(b'\xC7\x04\x9D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # fresh watchdog
            a.raw(b'\xC7\x04\x9D' + le32(wp_try_va) + le32(0))
            if layout.has_field('bot_pad_try'):
                a.raw(b'\xC7\x04\x9D' + le32(layout.va('bot_pad_try')) + le32(0))
            a.jmp('s542360_wp_steer')
            a.label('s542360_wp_no_portal_hop')
        a.raw(b'\x8B\x1D' + le32(bot_slot_tmp_va))       # ebx = slot
        a.raw(b'\x8B\x1C\x9D' + le32(failed_edge_va))    # ebx = failed_edge_marker[slot]
        a.raw(b'\x85\xDB')                               # test ebx, ebx
        a.jz('s542360_wp_have_next')
        # candidate marker = unordered(cur, route_next)
        a.raw(b'\x89\xCE')                               # esi = cur
        a.raw(b'\x89\xC7')                               # edi = route_next
        a.raw(b'\x39\xFE')                               # cmp esi, edi
        a.jbe('s542360_wp_route_edge_ordered')
        a.raw(b'\x87\xFE')                               # xchg esi, edi
        a.label('s542360_wp_route_edge_ordered')
        a.raw(b'\x46')                                   # inc esi (min+1)
        a.raw(b'\x47')                                   # inc edi (max+1)
        a.raw(b'\xC1\xE7\x10')                           # shl edi, 16
        a.raw(b'\x09\xFE')                               # or esi, edi
        a.raw(b'\x39\xDE')                               # cmp esi, ebx
        a.jz('s542360_wp_bad_edge_fallback')
        # Clean routed hop while a marker exists: routing is progressing on a
        # different edge, so the ping-pong counter starts over.
        a.raw(b'\x8B\x35' + le32(bot_slot_tmp_va))       # esi = slot
        a.raw(b'\xC7\x04\xB5' + le32(route_block_hits_va) + le32(0))
        a.jmp('s542360_wp_have_next')
        a.label('s542360_wp_bad_edge_fallback')
        # Routing insists on the marked edge (it IS the shortest path). Count
        # the forced fallbacks: on a graph like door nodes with one alternate
        # neighbour, routing bounces the bot right back here every hop — an
        # arrival-level ping-pong that never trips the wedge timeout (live CE:
        # cur flipped 17<->18 with wp_try pinned at 0). After
        # WP_ROUTE_BLOCK_RETRY_HITS forced fallbacks, clear the marker so the
        # next arrival RETRIES the edge: if the way is open now (doors open
        # when their area is awake) the bot just walks through; if it is still
        # blocked the wedge timeout re-marks it and arms the roam suspension.
        a.raw(b'\x8B\x35' + le32(bot_slot_tmp_va))       # esi = slot
        a.raw(b'\xFF\x04\xB5' + le32(route_block_hits_va))  # ++hits[slot]
        a.raw(b'\x83\x3C\xB5' + le32(route_block_hits_va)
              + bytes([cfg.WP_ROUTE_BLOCK_RETRY_HITS]))  # hits >= retry threshold?
        a.jb('s542360_wp_bad_edge_go')
        a.raw(b'\xC7\x04\xB5' + le32(failed_edge_va) + le32(0))       # retry the edge
        a.raw(b'\xC7\x04\xB5' + le32(route_block_hits_va) + le32(0))
        if door_gate:
            a.raw(b'\xC7\x04\xB5' + le32(route_block_door_va) + b'\xFF\xFF\xFF\xFF')
        a.label('s542360_wp_bad_edge_go')
        a.raw(b'\x89\xC2')                               # edx = blocked route_next
        a.call_lbl('wp_advance')                         # fallback excluding the blocked edge
        a.jmp('s542360_wp_have_next')
        a.label('s542360_wp_route_fallback')
        if portal_move:
            # Roam wander-entry (DM matches, goal-less CTF bots): if the
            # just-reached node hosts an active pad, roll
            # portal_wander_chance and occasionally step INTO the teleporter
            # instead of picking a random neighbour. cur/prev survive the
            # helper via the stack (it calls the engine RNG). SKIPPED while
            # this bot's routing is SUSPENDED (a suspension roam is a local
            # unstick — live snapshots caught a suspended carrier bouncing
            # arena-to-arena on this roll) and during the post-teleport
            # cooldown (each pad's exit node IS the return pad's node, so
            # the very next arrival would re-roll the coin — the observed
            # teleport ping-pong).
            a.raw(b'\x8B\x1D' + le32(bot_slot_tmp_va))   # ebx = slot
            if routing:
                a.raw(b'\x83\x3C\x9D' + le32(route_suspend_va) + b'\x00')
                a.jnz('s542360_wp_no_wander')            # suspended -> local roam only
            if layout.has_field('bot_portal_cd'):
                a.raw(b'\x83\x3C\x9D' + le32(layout.va('bot_portal_cd')) + b'\x00')
                a.jnz('s542360_wp_no_wander')            # just teleported -> no re-entry
            a.raw(b'\x51')                               # push cur (ecx)
            a.raw(b'\x52')                               # push prev (edx)
            a.call_lbl('portal_wander_check')            # eax = pad idx+1 or 0 (in: ecx=cur)
            a.raw(b'\x5A')                               # pop edx (prev)
            a.raw(b'\x59')                               # pop ecx (cur)
            a.raw(b'\x85\xC0'); a.jz('s542360_wp_no_wander')
            a.raw(b'\x8B\x1D' + le32(bot_slot_tmp_va))   # ebx = slot
            a.raw(b'\x89\x04\x9D' + le32(bot_portal_target_va))  # latch = pad idx+1
            a.raw(b'\xC7\x04\x9D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
            a.raw(b'\xC7\x04\x9D' + le32(wp_try_va) + le32(0))
            if layout.has_field('bot_pad_try'):
                a.raw(b'\xC7\x04\x9D' + le32(layout.va('bot_pad_try')) + le32(0))
            a.jmp('s542360_wp_steer')
            a.label('s542360_wp_no_wander')
        a.call_lbl('wp_advance')                         # fallback: random/non-prev neighbour
        a.label('s542360_wp_have_next')
    else:
        a.call_lbl('wp_advance')                          # eax = next idx or -1
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x14\x8D' + le32(current_wp_va))          # edx = old cur
    a.raw(b'\x89\x14\x8D' + le32(prev_wp_va))             # prev_wp[slot] = old cur (LATCH)
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_wp_steer')                              # isolated node -> keep cur
    a.raw(b'\x89\x04\x8D' + le32(current_wp_va))          # current_wp[slot] = next
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))               # wp_try = 0
    # Keep failed_edge_marker until respawn/reacquire or another edge failure;
    # otherwise CTF routing immediately reselects the same bad direct edge.
    # fall through to steer toward the (new) current node

    a.label('s542360_wp_steer')
    # Edge-following: when latched + enabled, steer toward a look-ahead point ON
    # the prev->current segment so the bot hugs the connection line (vital on
    # narrow lava corridors) instead of cutting diagonally after any drift. Else
    # (not latched / disabled / degenerate segment) steer straight at the node.
    # The wall-slide post-step still deflects the ANGLE if wedged against geometry.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x83\x3D' + le32(edge_follow_enabled_va) + b'\x00')  # cmp [edge_follow_enabled], 0
    a.jz('s542360_wp_steer_node')
    a.raw(b'\x8B\x04\x8D' + le32(prev_wp_va))             # eax = prev_wp[slot]
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_wp_steer_node')                         # not latched -> node-only
    a.raw(b'\x8D\x34\xC5' + le32(overlay_vertices_va))    # esi = &verts[prev] (P)
    a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))          # eax = cur
    a.raw(b'\x8D\x3C\xC5' + le32(overlay_vertices_va))    # edi = &verts[cur]  (C)
    # seg = C - P
    a.raw(b'\xD9\x07'); a.raw(b'\xD8\x26'); a.raw(b'\xD9\x1D' + le32(wp_seg_x_va))           # seg_x = C.x - P.x
    a.raw(b'\xD9\x47\x04'); a.raw(b'\xD8\x66\x04'); a.raw(b'\xD9\x1D' + le32(wp_seg_y_va))   # seg_y = C.y - P.y
    # seglen2 = seg_x^2 + seg_y^2
    a.raw(b'\xD9\x05' + le32(wp_seg_x_va)); a.raw(b'\xD8\xC8')   # fld seg_x; fmul st,st
    a.raw(b'\xD9\x05' + le32(wp_seg_y_va)); a.raw(b'\xD8\xC8')   # fld seg_y; fmul st,st
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0 = seglen2
    a.raw(b'\xD9\xEE')                                    # fldz (ST0=0, ST1=seglen2)
    a.raw(b'\xDF\xF1')                                    # fcomip st0,st1 (pop 0); CF=1 iff 0<seglen2
    a.jae('s542360_wp_steer_node_pop')                    # 0>=seglen2 -> degenerate (pop seglen2)
    # dot = (B-P).seg   (ST0=seglen2 throughout)
    a.raw(b'\xD9\x05' + le32(bot_pos_va)); a.raw(b'\xD8\x26'); a.raw(b'\xD8\x0D' + le32(wp_seg_x_va))      # (B.x-P.x)*seg_x
    a.raw(b'\xD9\x05' + le32(bot_pos_va + 4)); a.raw(b'\xD8\x66\x04'); a.raw(b'\xD8\x0D' + le32(wp_seg_y_va))  # (B.y-P.y)*seg_y
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0=dot, ST1=seglen2
    a.raw(b'\xDE\xF1')                                    # fdivrp st1,st0 -> ST0 = dot/seglen2 = t
    a.raw(b'\xD8\x05' + le32(edge_lookahead_va))          # fadd lookahead_frac -> ST0 = tp
    # clamp tp to [0, 1]: upper
    a.raw(b'\xD9\xE8')                                    # fld1 (ST0=1, ST1=tp)
    a.raw(b'\xDF\xF1')                                    # fcomip st0,st1 (pop 1); CF=1 iff 1<tp
    a.jae('s542360_wp_tp_no_hi')                          # 1>=tp -> no upper clamp
    a.raw(b'\xDD\xD8'); a.raw(b'\xD9\xE8')                # fstp st0 (drop tp); fld1 (tp=1)
    a.label('s542360_wp_tp_no_hi')
    a.raw(b'\xD9\xEE')                                    # fldz (ST0=0, ST1=tp)
    a.raw(b'\xDF\xF1')                                    # fcomip st0,st1 (pop 0); CF=1 iff 0<tp
    a.jb('s542360_wp_tp_no_lo')                           # 0<tp -> no lower clamp
    a.raw(b'\xDD\xD8'); a.raw(b'\xD9\xEE')                # fstp st0 (drop tp); fldz (tp=0)
    a.label('s542360_wp_tp_no_lo')
    a.raw(b'\xD9\x1D' + le32(wp_tp_va))                   # fstp tp
    # desired = (P + tp*seg) - B
    a.raw(b'\xD9\x05' + le32(wp_tp_va)); a.raw(b'\xD8\x0D' + le32(wp_seg_x_va))    # fld tp; fmul seg_x
    a.raw(b'\xD8\x06'); a.raw(b'\xD8\x25' + le32(bot_pos_va))                      # fadd [esi] (P.x); fsub bot.x
    a.raw(b'\xD9\x1D' + le32(dx_accum_va))                # fstp dx_accum
    a.raw(b'\xD9\x05' + le32(wp_tp_va)); a.raw(b'\xD8\x0D' + le32(wp_seg_y_va))    # fld tp; fmul seg_y
    a.raw(b'\xD8\x46\x04'); a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))              # fadd [esi+4] (P.y); fsub bot.y
    a.raw(b'\xD9\x1D' + le32(dy_accum_va))                # fstp dy_accum
    a.jmp('s542360_emit')

    a.label('s542360_wp_steer_node_pop')
    a.raw(b'\xDD\xD8')                                    # fstp st0 (pop seglen2)
    a.label('s542360_wp_steer_node')
    # Straight-at-node fallback: desired = node - bot.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))          # eax = cur
    a.raw(b'\x8D\x14\xC5' + le32(overlay_vertices_va))    # lea edx, [eax*8 + verts]
    a.raw(b'\xD9\x02')                                    # fld [edx]     node.x
    a.raw(b'\xD8\x25' + le32(bot_pos_va))                 # fsub bot.x
    a.raw(b'\xD9\x1D' + le32(dx_accum_va))                # fstp dx_accum
    a.raw(b'\xD9\x42\x04')                                # fld [edx+4]   node.y
    a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))             # fsub bot.y
    a.raw(b'\xD9\x1D' + le32(dy_accum_va))                # fstp dy_accum
    a.jmp('s542360_emit')

    a.label('s542360_fallback_zero')
    # No graph / follow disabled: emit zero (idle). The random-wander potential
    # field was removed; author a graph for maps where bots should move.
    a.raw(b'\xC7\x05' + le32(dx_accum_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(dy_accum_va) + le32(0))
    # fall through to emit


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
