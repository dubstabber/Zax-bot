"""``apply_lead`` — bot shot-leading (target prediction).

Replaces the perception scan's straight-line aim deltas with a first-order
intercept solve so bots lead moving targets:

    t           = sqrt(best_dist_sq) / proj_speed
    best_dx    += best_vx * t
    best_dy    += best_vy * t

``best_dx`` / ``best_dy`` are rewritten in place so the downstream
``sub_509100`` call in ``detour_5436F0`` keeps using the same two scratch
slots — no signature changes there. Inputs come from ``pick_target``:
``best_dist_sq`` is the d² of the chosen target, ``best_vx`` / ``best_vy``
are the target's instantaneous world-space velocity captured during the
"new best" branch of the perception loop, and ``proj_speed`` is the build-
time constant from ``cfg.PROJECTILE_SPEED``.

LOS is intentionally still checked against the target's current position
(in ``pick_target``); only the aim angle uses the predicted intercept.
Matches the common shooter-bot semantic of "I can see them, so I'll shoot
toward where they're going."

The math is first-order: target acceleration and bot motion are ignored,
projectile flight time is approximated as ``distance / speed`` rather than
solving the true quadratic intercept. Cheap and good enough for the close-
to-mid ranges this bot fires at. A second-order solver would slot in here
without disturbing callers.

Isolation also matters for the next milestone: per-shot randomization
between "predict" and "shoot straight" becomes a single conditional that
either calls this helper or skips it.

Side effects: rewrites ``best_dx`` and ``best_dy``. FPU stack empty on exit.
Clobbers: ST*.
"""

from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    best_dx_va       = layout.va('best_dx')
    best_dy_va       = layout.va('best_dy')
    best_dist_sq_va  = layout.va('best_dist_sq')
    best_vx_va       = layout.va('best_vx')
    best_vy_va       = layout.va('best_vy')
    proj_speed_va    = layout.va('proj_speed')

    a.label('apply_lead')

    # ST0 = t = sqrt(d²) / projectile_speed
    a.raw(b'\xD9\x05' + le32(best_dist_sq_va))   # fld dword [best_dist_sq]
    a.raw(b'\xD9\xFA')                           # fsqrt
    a.raw(b'\xD8\x35' + le32(proj_speed_va))     # fdiv dword [proj_speed]

    # best_dx += vx * t
    a.raw(b'\xD9\x05' + le32(best_vx_va))        # fld dword [best_vx]   ST0=vx, ST1=t
    a.raw(b'\xD8\xC9')                           # fmul st(0), st(1)     ST0=vx*t, ST1=t
    a.raw(b'\xD8\x05' + le32(best_dx_va))        # fadd dword [best_dx]  ST0=best_dx+vx*t
    a.raw(b'\xD9\x1D' + le32(best_dx_va))        # fstp dword [best_dx]  ST0=t

    # best_dy += vy * t
    a.raw(b'\xD9\x05' + le32(best_vy_va))        # fld dword [best_vy]   ST0=vy, ST1=t
    a.raw(b'\xDE\xC9')                           # fmulp st(1), st(0)    ST0=vy*t
    a.raw(b'\xD8\x05' + le32(best_dy_va))        # fadd dword [best_dy]  ST0=best_dy+vy*t
    a.raw(b'\xD9\x1D' + le32(best_dy_va))        # fstp dword [best_dy]  stack empty

    a.raw(b'\xC3')                               # ret
