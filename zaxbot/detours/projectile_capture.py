"""``detour_491A40`` — capture the most recent CEntityProjectile pointer.

The engine's projectile constructor at ``sub_491A40`` allocates a 0x114-byte
``CEntityProjectile``, zeros its velocity/acceleration fields, sets its
vtable, and returns the pointer in EAX. We patch its 5-byte success epilogue
(``mov eax, esi; pop edi; pop esi; ret``) with a JMP into our hook, which
records ESI (= the projectile pointer) into ``scratch.last_proj_va`` and
bumps ``scratch.proj_count``, then re-emits the same epilogue so callers see
identical behavior.

Why ESI: at the patch site, the function has done ``mov esi, eax`` right
after the allocator returned, and ESI hasn't been touched since. Capturing
ESI gives us the projectile pointer before the function's epilogue clobbers
EAX (with the same value, but we want the source-of-truth that won't change
across the patch redirect).

Why this exit and not the function entry: the engine's fire path calls this
ctor, then sets the projectile's velocity at ``+0xE8/+0xEC`` immediately
after. By capturing the pointer here we have plenty of time before the next
projectile is spawned (or the next R-press) to read the live velocity — the
projectile's friction is zero so velocity stays constant in flight.

Failure path (alloc returned NULL) exits at ``0x491AEF`` via a separate
``pop edi; xor eax,eax; pop esi; ret`` sequence — our patch site doesn't
touch it, so we only capture successful constructions.
"""

from .. import addresses as ax
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    last_proj_va_va = layout.va('last_proj_va')
    proj_count_va   = layout.va('proj_count')

    a.label('detour_491A40')
    a.raw(b'\x89\x35' + le32(last_proj_va_va))  # mov [last_proj_va], esi
    a.raw(b'\xFF\x05' + le32(proj_count_va))    # inc dword [proj_count]
    # Re-emit the displaced 5-byte epilogue. RET goes directly back to the
    # ctor's caller (we never return to 0x491AEF — that's the failure path).
    a.raw(ax.S491A40_EPILOGUE_ORIG)             # mov eax,esi; pop edi; pop esi; ret
