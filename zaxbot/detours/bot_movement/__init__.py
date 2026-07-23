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

from ...asm import Asm
from ...layout import ScratchLayout
from . import divert, follow_arrive, follow_entry, follow_final_approach
from . import follow_pursuits, follow_recovery, follow_steer, follow_watchdog
from . import setup, vector_emit
from .follow_ctx import build_follow_ctx


def emit(a: Asm, layout: ScratchLayout) -> None:
    """Assemble ``detour_542360`` as one contiguous body.

    Each stage appends in section order; together they form the single
    instruction stream the engine jumps into. Order is load-bearing (it
    fixes label positions / fall-through), so do not reorder without
    re-establishing the byte-identity baseline."""
    setup._emit_identify_and_setup(a, layout)
    setup._emit_stuck_detection(a, layout)
    divert._emit_reactive_lava_flee(a, layout)
    divert._emit_pickup_divert(a, layout)
    _emit_waypoint_follow(a, layout)
    vector_emit._emit_normalize_and_emit(a, layout)
    vector_emit._emit_wall_slide(a, layout)
    vector_emit._emit_portal_veto(a, layout)
    vector_emit._emit_mine_veto(a, layout)
    vector_emit._emit_plasma_veto(a, layout)
    vector_emit._emit_dead_and_zero_return(a, layout)
    vector_emit._emit_normal_fallthrough(a)


def _emit_waypoint_follow(a: Asm, layout: ScratchLayout) -> None:
    """Pure node-to-node follow: acquire the nearest node, test arrival, run
    the off-graph progress watchdog (retreat/re-acquire), advance along a real
    edge on arrival, and stage ``desired = node - bot`` into dx/dy for the
    normalize stage. No graph / follow disabled -> zero (idle)."""
    c = build_follow_ctx(layout)
    follow_entry.emit(a, layout, c)
    follow_pursuits.emit(a, layout, c)
    follow_recovery.emit(a, layout, c)
    follow_final_approach.emit(a, layout, c)
    follow_watchdog.emit(a, layout, c)
    follow_arrive.emit(a, layout, c)
    follow_steer.emit(a, layout, c)
