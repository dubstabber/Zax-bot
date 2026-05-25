"""``detour_dp`` for ``sub_480BD0`` (DP poll).

Captures the DP manager (ECX) and the poll context register (EDI). The latter
is needed because ``sub_480800`` saves EDI into its local ``var_1C`` and later
derefs ``var_1C+0x24`` in the random-name init (``sub_4DF2B0``). When we
invoke ``sub_480800`` from the keyboard hook with EDI=0 it faults; capturing
the live value here lets us replay the engine's context."""

from .. import addresses as ax
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    cap_dp_edi_va = layout.va('cap_dp_edi')
    cap_dpmgr     = layout.va('cap_dpmgr')

    a.label('detour_dp')
    a.raw(b'\x89\x3D' + le32(cap_dp_edi_va))      # mov [cap_dp_edi], edi
    a.raw(b'\x89\x0D' + le32(cap_dpmgr))          # mov [cap_dpmgr], ecx
    a.raw(b'\x51\x56\x8B\xF1\x6A\x00')             # displaced 6 bytes
    a.jmp_va(ax.POLL_RESUME)
