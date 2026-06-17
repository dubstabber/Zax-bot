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
  - ``bot_flee_ticks``     -> ``slide_turn``    (wall-slide deflection ramp)
  - ``bot_wander_x/y``     -> block-vector diagnostic mirror
  - ``curr_dist_sq``/``cand_tmp`` -> ``dx_accum``/``dy_accum`` (borrowed
    fire/aim scratch, mutually exclusive with this detour)
"""

from .. import addresses as ax
from ..asm import Asm, le32
from ..hook.bot_lookup import emit_addr_to_slot, emit_is_bot_controller
from ..layout import ScratchLayout


# --- Wall-slide tuning (asm immediates; the angle step is a runtime knob) ----
# Trigger on LACK OF PROGRESS (wp_try), the ONLY signal that actually works:
#  - stuck_count (per-frame move < STUCK_DELTA_SQ) FAILS — a bot pinned against
#    a wall BOUNCES >0.5px/frame (net displacement ~0) so stuck_count never
#    climbs (confirmed: frozen position, stuck_count==0).
#  - the engine does NOT populate the controller block vector for bots
#    (confirmed: BLK==(0,0) while pinned), so a geometric wall-slide is out.
# wp_try climbs whenever the bot fails to get strictly closer to its node,
# catching freeze, bounce, AND slide-along-wall. ~8 frames before the sweep.
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
    a.raw(b'\xC7\x04\x8D' + le32(slide_turn_va) + le32(0))             # slide_turn = 0
    a.raw(b'\xC7\x04\x8D' + le32(pickup_div_active_va) + le32(0))      # drop any pickup divert
    a.raw(b'\xC7\x04\x8D' + le32(pickup_cd_va) + le32(0))             # clear divert cooldown
    a.raw(b'\xC7\x04\x8D' + le32(bot_last_damage_va) + le32(0))        # reset cur_damage tracker
    a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_wander_ticks')) + le32(0))  # reset lava-flee countdown
    a.raw(b'\x89\x14\x8D' + le32(bot_last_char_va))       # bot_last_char[slot] = edx
    a.label('s542360_char_same')

    # Read bot position into bot_pos.
    a.raw(b'\x68' + le32(bot_pos_va))                     # push &bot_pos
    a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))            # ecx = bot char
    a.call_va(ax.SUB_4FB0A0_VA)                           # __thiscall, ret 4


def _emit_stuck_detection(a: Asm, layout: ScratchLayout) -> None:
    """d² between current and last position. Drives the wall-slide ramp (a
    wedged bot makes no progress so this climbs) and the pickup-divert
    wall-wedge abandon."""
    bot_pos_va       = layout.va('bot_pos')
    bot_slot_tmp_va  = layout.va('bot_slot_tmp')
    last_x_va        = layout.va('bot_last_x')
    last_y_va        = layout.va('bot_last_y')
    stuck_count_va   = layout.va('bot_stuck_count')
    stuck_delta_sq_va = layout.va('stuck_delta_sq')

    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xD9\x05' + le32(bot_pos_va))                 # fld pos.x
    a.raw(b'\xD8\x24\x8D' + le32(last_x_va))              # fsub last_x[slot]
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xD9\x05' + le32(bot_pos_va + 4))             # fld pos.y
    a.raw(b'\xD8\x24\x8D' + le32(last_y_va))              # fsub last_y[slot]
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0 = d²
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
    slide_turn_va     = layout.va('bot_flee_ticks')       # wall-slide ramp
    wp_follow_enabled_va    = layout.va('wp_follow_enabled')
    wp_reached_radius_sq_va = layout.va('wp_reached_radius_sq')
    wp_progress_timeout_va  = layout.va('wp_progress_timeout')
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

    # === Waypoint following =============================================
    a.raw(b'\x83\x3D' + le32(wp_follow_enabled_va) + b'\x00')
    a.jz('s542360_fallback_zero')
    a.raw(b'\x83\x3D' + le32(overlay_vertex_count_va) + b'\x00')
    a.jz('s542360_fallback_zero')

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
    # fall through to wp_have_cur

    a.label('s542360_wp_have_cur')
    # Arrival test: dsq(bot, vertices[cur]) < wp_reached_radius_sq ?
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
    a.jb('s542360_wp_progress')                           # radius < dsq -> not arrived
    a.raw(b'\xDD\xD8')                                    # fstp st(0)  (arrived: pop dsq)
    a.jmp('s542360_wp_arrived')

    # --- Progress-toward-target watchdog (off-graph pin safety net). ST0=dsq.
    a.label('s542360_wp_progress')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot (lea clobbered it)
    a.raw(b'\xD9\x04\x8D' + le32(wp_best_dsq_va))         # fld best_dsq[slot] (ST0=best, ST1=dsq)
    a.raw(b'\xDF\xF1')                                    # fcomip st0,st1 (best:dsq, pop best)
    a.jbe('s542360_wp_no_progress')                       # best <= dsq -> no improvement
    a.raw(b'\xD9\x1C\x8D' + le32(wp_best_dsq_va))         # fstp best_dsq[slot] = dsq
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))    # wp_try = 0
    a.jmp('s542360_wp_steer')

    a.label('s542360_wp_no_progress')
    a.raw(b'\xDD\xD8')                                    # fstp st(0)  (drop dsq)
    a.raw(b'\xFF\x04\x8D' + le32(wp_try_va))              # ++wp_try[slot]
    a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))              # eax = wp_try
    a.raw(b'\x3B\x05' + le32(wp_progress_timeout_va))     # cmp eax, [progress_timeout]
    a.jb('s542360_wp_steer')                              # under budget -> keep steering
    # fall through: wedged off-graph too long -> re-acquire nearest

    a.label('s542360_wp_reacquire')
    # Couldn't reach cur for WP_PROGRESS_TIMEOUT frames (a wall blocks the
    # straight edge, or the node is otherwise unreachable from here). If LATCHED,
    # RETREAT to the previous node — the bot just came from it, so it IS
    # reachable — and on arriving there it advances to a DIFFERENT neighbour
    # (wp_advance excludes prev), routing AROUND the unreachable node instead of
    # orbiting it forever. If NOT latched, re-acquire the nearest node.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\x8D' + le32(prev_wp_va))             # eax = prev_wp[slot]
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_wp_reacq_nearest')                      # not latched -> reacquire nearest
    # Latched: swap cur <-> prev (eax = prev).
    a.raw(b'\x8B\x14\x8D' + le32(current_wp_va))          # edx = old cur
    a.raw(b'\x89\x04\x8D' + le32(current_wp_va))          # current_wp[slot] = prev
    a.raw(b'\x89\x14\x8D' + le32(prev_wp_va))             # prev_wp[slot]    = old cur
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))               # wp_try = 0
    a.raw(b'\xC7\x04\x8D' + le32(slide_turn_va) + le32(0))           # slide_turn = 0
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
    a.raw(b'\x89\x1C\x8D' + le32(current_wp_va))          # current_wp[slot] = nearest
    a.raw(b'\xC7\x04\x8D' + le32(prev_wp_va) + b'\xFF\xFF\xFF\xFF')   # prev_wp = -1
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))               # wp_try = 0
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
    a.call_lbl('wp_advance')                              # eax = next idx or -1
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x14\x8D' + le32(current_wp_va))          # edx = old cur
    a.raw(b'\x89\x14\x8D' + le32(prev_wp_va))             # prev_wp[slot] = old cur (LATCH)
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_wp_steer')                              # isolated node -> keep cur
    a.raw(b'\x89\x04\x8D' + le32(current_wp_va))          # current_wp[slot] = next
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))               # wp_try = 0
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

    # Update the deflection from LACK OF PROGRESS (wp_try). While the bot fails
    # to get closer to its node, cycle the heading one step every (SWEEP_MASK+1)
    # frames to try to find a way around the wall. While progressing (wp_try
    # below the trigger) clear the deflection so the bot steers straight at its
    # node. Infinite circling is prevented by the RETREAT in the follow block
    # (wp_try >= WP_RETREAT_TIMEOUT backs the bot up to the previous node).
    a.raw(b'\x8B\x04\x8D' + le32(wp_try_va))              # eax = wp_try[slot]
    a.raw(b'\x83\xF8' + bytes([WP_SLIDE_TRIGGER_FRAMES]))  # cmp eax, TRIGGER
    a.jb('s542360_ws_reset')                             # progressing -> straight
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
    a.raw(b'\x85\xC0'); a.jz('s542360_plasma_veto')       # no deflection -> lava veto
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
    a.jmp('s542360_plasma_veto')


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
    slide_turn_va   = layout.va('bot_flee_ticks')         # wall-slide ramp

    # --- Bot dead this frame (char slot NULL). Reset nav so it cold-acquires
    # on respawn, then emit zero (no live char this frame).
    a.label('s542360_wp_mark_dead')
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\xC7\x04\x8D' + le32(current_wp_va) + b'\xFF\xFF\xFF\xFF')  # current_wp = -1
    a.raw(b'\xC7\x04\x8D' + le32(prev_wp_va) + b'\xFF\xFF\xFF\xFF')     # prev_wp = -1
    a.raw(b'\xC7\x04\x8D' + le32(slide_turn_va) + le32(0))             # slide_turn = 0
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))    # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))                # wp_try = 0
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
