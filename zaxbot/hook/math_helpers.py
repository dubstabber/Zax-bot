"""Small x87 emit helpers for the in-engine bot AI math.

Right now the bot pipeline only needs 2D distance² for the perception scan,
but the same shape will recur for movement (compare bot ↔ waypoint), salvage
collection (bot ↔ pickup), and leading shots (relative position used for
intercept solving). Putting the dx/dy/d² idiom behind one helper keeps the
float dance — including the SAHF compare-and-pop sequence — in a single
place we can verify and reuse.

The helpers operate on world positions laid out as a pair of consecutive
floats ``[x, y]`` (the engine's ``sub_4FB0A0`` writes this format into
``bot_pos`` / ``cand_pos``).
"""

from ..asm import Asm, le32


def emit_dist_sq_2d(a: Asm,
                    p1_va: int,
                    p2_va: int,
                    *,
                    dx_out_va: int,
                    dy_out_va: int,
                    dist_sq_out_va: int) -> None:
    """Compute ``d² = (p1.x - p2.x)² + (p1.y - p2.y)²`` and stash dx, dy, d².

    After this helper returns, the x87 stack is empty (``faddp`` pops both
    operands; ``fst`` followed by ``fcomp`` in callers is *their* business).
    ``dx`` and ``dy`` are intentionally signed (``p1 - p2``) so callers can
    feed them straight into ``sub_509100`` (atan2-ish) for an aim angle.

    Layout assumption: each position is two consecutive 32-bit floats —
    ``[p_va] = x``, ``[p_va + 4] = y``.
    """
    # ST0 = (p1.x - p2.x)
    a.raw(b'\xD9\x05' + le32(p1_va))                          # fld  [p1.x]
    a.raw(b'\xD8\x25' + le32(p2_va))                          # fsub [p2.x]
    a.raw(b'\xD9\x15' + le32(dx_out_va))                      # fst  [dx_out]
    a.raw(b'\xD8\xC8')                                         # fmul st,st  -> ST0 = dx²
    # ST1 holds dx²; build dy² in ST0.
    a.raw(b'\xD9\x05' + le32(p1_va + 4))                      # fld  [p1.y]
    a.raw(b'\xD8\x25' + le32(p2_va + 4))                      # fsub [p2.y]
    a.raw(b'\xD9\x15' + le32(dy_out_va))                      # fst  [dy_out]
    a.raw(b'\xD8\xC8')                                         # fmul st,st  -> ST0 = dy²
    a.raw(b'\xDE\xC1')                                         # faddp        -> ST0 = d²
    a.raw(b'\xD9\x15' + le32(dist_sq_out_va))                 # fst  [d²_out]


def emit_fcomp_jae(a: Asm, threshold_va: int, target_label: str) -> None:
    """``if ST0 >= [threshold]: jmp target_label`` then pop ST0.

    Uses the engine's standard ``fcomp m32 / fnstsw / sahf / jae`` sequence.
    Callers typically pair this with ``emit_dist_sq_2d`` to gate "this
    candidate is farther than my current best, skip it" without leaving
    anything on the x87 stack.
    """
    a.raw(b'\xD8\x1D' + le32(threshold_va))                   # FCOMP m32 (compare, pop)
    a.raw(b'\xDF\xE0')                                         # fnstsw ax
    a.raw(b'\x9E')                                             # sahf
    a.jae(target_label)
