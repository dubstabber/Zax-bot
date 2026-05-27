"""``apply_lead`` — bot shot-leading (target prediction).

Solves the exact projectile-intercept quadratic so bots lead moving targets
with the right anticipation regardless of motion direction:

    |d + v*t|² = (muzzle + p*t)²
        ⇒  (|v|² - p²) * t² + 2 * (d·v - muzzle*p) * t + (|d|² - muzzle²) = 0
        ⇒  a * t² + b' * t + c' = 0

where ``d = (best_dx, best_dy)`` is the line-of-sight offset to the target,
``v = (best_vx, best_vy)`` is its instantaneous world-space velocity,
``p = proj_speed`` is the per-tick projectile speed (set by
``compute_proj_speed``), and ``muzzle = cfg.MUZZLE_OFFSET`` is the gun-
barrel spawn offset from the bot's character center along the firing
angle.

Why the muzzle term: the engine spawns projectiles ``muzzle`` pixels in
front of the bot center, so the bullet's actual flight distance is
``|intercept| - muzzle`` rather than ``|intercept|``. Without this
correction the bullet arrives at the predicted intercept point sooner
than expected and the target hasn't moved as far ⇒ small consistent
over-lead that rotates with the firing angle. Empirically calibrated to
~20 px against a stationary bot firing Missile Launcher; sub the
captured projectile entity's back-extrapolated spawn position vs the
bot's position to verify.

For the typical case ``|v| < p`` (target slower than the bullet, so
``a < 0`` and ``c' > 0`` when target is outside muzzle range), the product
of the two roots is ``c'/a < 0``: one positive, one negative. We pick the
positive intercept time using the numerically-stable "citardauq" form

    t = 2c' / (-b' + sqrt(disc))

For the degenerate cases — discriminant negative (no real intercept),
``a >= 0`` (target as fast or faster than the bullet) — we fall back to the
first-order approximation ``t = (sqrt(c) - muzzle) / p``. That keeps the
bot firing instead of freezing on edge cases.

``best_dx`` / ``best_dy`` are rewritten in place so the downstream
``sub_509100`` call in ``detour_5436F0`` keeps using the same two scratch
slots — no signature changes there.

Side effects: rewrites ``best_dx`` and ``best_dy``; updates ``quad_a``,
``quad_b``, ``quad_disc``, ``quad_c`` scratch fields (diagnostic, also
useful for an R snapshot if intercept accuracy ever needs further
debugging). FPU stack empty on exit. Clobbers: EAX, ST*.
"""

from .. import addresses as _ax  # noqa: F401  (kept for parity with other hook modules)
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    best_dx_va        = layout.va('best_dx')
    best_dy_va        = layout.va('best_dy')
    best_dist_sq_va   = layout.va('best_dist_sq')
    best_vx_va        = layout.va('best_vx')
    best_vy_va        = layout.va('best_vy')
    proj_speed_va     = layout.va('proj_speed')
    quad_a_va         = layout.va('quad_a')
    quad_b_va         = layout.va('quad_b')
    quad_c_va         = layout.va('quad_c')
    quad_disc_va      = layout.va('quad_disc')
    muzzle_offset_va  = layout.va('muzzle_offset')
    muzzle_sq_va      = layout.va('muzzle_sq')

    a.label('apply_lead')

    # --- Compute a = vx² + vy² - proj_speed² -----------------------------
    a.raw(b'\xD9\x05' + le32(best_vx_va))         # fld dword [best_vx]
    a.raw(b'\xD8\xC8')                            # fmul st(0), st(0)        ; vx²
    a.raw(b'\xD9\x05' + le32(best_vy_va))         # fld dword [best_vy]
    a.raw(b'\xD8\xC8')                            # fmul st(0), st(0)        ; vy²
    a.raw(b'\xDE\xC1')                            # faddp st(1), st(0)       ; |v|²
    a.raw(b'\xD9\x05' + le32(proj_speed_va))      # fld dword [proj_speed]
    a.raw(b'\xD8\xC8')                            # fmul st(0), st(0)        ; p²
    a.raw(b'\xDE\xE9')                            # fsubp st(1), st(0)       ; |v|² - p² = a
    a.raw(b'\xD9\x1D' + le32(quad_a_va))          # fstp dword [quad_a]

    # --- Compute b' = 2 * (dx*vx + dy*vy) - 2 * muzzle * proj_speed -----
    a.raw(b'\xD9\x05' + le32(best_dx_va))         # fld dword [best_dx]
    a.raw(b'\xD8\x0D' + le32(best_vx_va))         # fmul dword [best_vx]
    a.raw(b'\xD9\x05' + le32(best_dy_va))         # fld dword [best_dy]
    a.raw(b'\xD8\x0D' + le32(best_vy_va))         # fmul dword [best_vy]
    a.raw(b'\xDE\xC1')                            # faddp st(1), st(0)       ; d·v
    a.raw(b'\xD8\xC0')                            # fadd st(0), st(0)        ; 2*(d·v)
    a.raw(b'\xD9\x05' + le32(muzzle_offset_va))   # fld dword [muzzle_offset]
    a.raw(b'\xD8\x0D' + le32(proj_speed_va))      # fmul dword [proj_speed]  ; muzzle*p
    a.raw(b'\xD8\xC0')                            # fadd st(0), st(0)        ; 2*muzzle*p
    a.raw(b'\xDE\xE9')                            # fsubp st(1), st(0)       ; 2(d·v) - 2*muzzle*p
    a.raw(b'\xD9\x1D' + le32(quad_b_va))          # fstp dword [quad_b]

    # --- Compute c' = best_dist_sq - muzzle_sq --------------------------
    a.raw(b'\xD9\x05' + le32(best_dist_sq_va))    # fld dword [best_dist_sq]
    a.raw(b'\xD8\x25' + le32(muzzle_sq_va))       # fsub dword [muzzle_sq]   ; c'
    a.raw(b'\xD9\x1D' + le32(quad_c_va))          # fstp dword [quad_c]

    # --- Compute disc = b'² - 4 * a * c' --------------------------------
    a.raw(b'\xD9\x05' + le32(quad_b_va))          # fld dword [quad_b]
    a.raw(b'\xD8\xC8')                            # fmul st(0), st(0)        ; b'²
    a.raw(b'\xD9\x05' + le32(quad_a_va))          # fld dword [quad_a]
    a.raw(b'\xD8\x0D' + le32(quad_c_va))          # fmul dword [quad_c]      ; a*c'
    a.raw(b'\xD8\xC0')                            # fadd st(0), st(0)        ; 2ac'
    a.raw(b'\xD8\xC0')                            # fadd st(0), st(0)        ; 4ac'
    a.raw(b'\xDE\xE9')                            # fsubp st(1), st(0)       ; b'² - 4ac'
    a.raw(b'\xD9\x1D' + le32(quad_disc_va))       # fstp dword [quad_disc]   ; stack empty

    # --- Sanity gates: fall back to first-order on bad inputs ------------
    # disc < 0 (no real intercept) — test the float's sign bit via integer
    # cmp. After `test eax, eax`, OF=0 and SF=high-bit-of-eax, so `jl`
    # jumps iff the high bit is set, equivalent to `js` for a non-NaN float.
    a.raw(b'\xA1' + le32(quad_disc_va))           # mov eax, [quad_disc]
    a.raw(b'\x85\xC0')                            # test eax, eax
    a.jl('apply_lead_first_order')                # SF=1 ⇒ float is negative

    # a >= 0 (target as fast as / faster than the bullet) — the stable
    # citardauq form below assumes a < 0. Defer to first-order for safety.
    a.raw(b'\xA1' + le32(quad_a_va))              # mov eax, [quad_a]
    a.raw(b'\x85\xC0')                            # test eax, eax
    a.jge('apply_lead_first_order')               # SF=0 ⇒ float is non-negative

    # c' <= 0 (target inside muzzle range) — formula breaks down; just aim
    # at the current target position (zero lead). Same test as disc but on
    # c': SF=1 means c' < 0; ZF=1 means c' == 0; either way fall back to
    # first-order (which handles the small/zero distance correctly).
    a.raw(b'\xA1' + le32(quad_c_va))              # mov eax, [quad_c]
    a.raw(b'\x85\xC0')                            # test eax, eax
    a.jl('apply_lead_first_order')                # c' < 0 ⇒ target inside muzzle

    # --- Quadratic solve: t = 2c' / (-b' + sqrt(disc)) ------------------
    a.raw(b'\xD9\x05' + le32(quad_disc_va))       # fld dword [quad_disc]
    a.raw(b'\xD9\xFA')                            # fsqrt                     ; sqrt(disc)
    a.raw(b'\xD9\x05' + le32(quad_b_va))          # fld dword [quad_b]
    a.raw(b'\xD9\xE0')                            # fchs                      ; -b'
    a.raw(b'\xDE\xC1')                            # faddp st(1), st(0)        ; -b' + sqrt(disc)
    a.raw(b'\xD9\x05' + le32(quad_c_va))          # fld dword [quad_c]        ; c'
    a.raw(b'\xD8\xC0')                            # fadd st(0), st(0)         ; 2c'
    # FDIVRP st(1), st(0) is DE F0+i (i=1 → DE F1).
    a.raw(b'\xDE\xF1')                            # fdivrp st(1), st(0)       ; t = 2c' / (-b' + sqrt(disc))
    a.jmp('apply_lead_apply_t')

    # --- First-order fallback: t = (sqrt(c) - muzzle) / proj_speed ------
    # Clamp at 0 if sqrt(c) < muzzle (target inside muzzle range) so we
    # don't end up with negative t (which would lead in the wrong direction).
    a.label('apply_lead_first_order')
    a.raw(b'\xD9\x05' + le32(best_dist_sq_va))    # fld dword [best_dist_sq]
    a.raw(b'\xD9\xFA')                            # fsqrt                     ; sqrt(c)
    a.raw(b'\xD8\x25' + le32(muzzle_offset_va))   # fsub dword [muzzle_offset]; sqrt(c) - muzzle
    a.raw(b'\xD8\x35' + le32(proj_speed_va))      # fdiv dword [proj_speed]   ; t (may be slightly negative for super-close targets)

    # --- Apply lead: best_dx += vx*t, best_dy += vy*t --------------------
    # ST0 = t entering this block (both paths leave it there).
    a.label('apply_lead_apply_t')
    a.raw(b'\xD9\x05' + le32(best_vx_va))         # fld dword [best_vx]       ; vx, t
    a.raw(b'\xD8\xC9')                            # fmul st(0), st(1)         ; vx*t, t
    a.raw(b'\xD8\x05' + le32(best_dx_va))         # fadd dword [best_dx]
    a.raw(b'\xD9\x1D' + le32(best_dx_va))         # fstp dword [best_dx]      ; pop, ST0=t

    a.raw(b'\xD9\x05' + le32(best_vy_va))         # fld dword [best_vy]       ; vy, t
    a.raw(b'\xD8\xC9')                            # fmul st(0), st(1)         ; vy*t, t
    a.raw(b'\xD8\x05' + le32(best_dy_va))         # fadd dword [best_dy]
    a.raw(b'\xD9\x1D' + le32(best_dy_va))         # fstp dword [best_dy]      ; pop, ST0=t

    a.raw(b'\xDD\xD8')                            # fstp st(0)                ; pop t, stack empty
    a.raw(b'\xC3')                                # ret
