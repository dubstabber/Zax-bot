"""``detour_4F5204`` — null-skip inside ``sub_4F5150``'s char-array iteration.

End-game cleanup walks ``mgr+0x290`` and derefs ``[arr[j] + 0x48]`` without
a null check; a null slot crashes the engine. Engine jumps (not calls) here;
we read the slot, skip the loop iter on null, or run the original
``mov ecx, [eax+0x48]`` and jump back into the loop at the original
``cmp ecx, ebx`` site."""

from .. import addresses as ax
from ..asm import Asm
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    a.label('detour_4F5204')
    a.raw(b'\x8B\x04\xBA')                                    # mov eax, [edx+edi*4]
    a.raw(b'\x85\xC0')                                        # test eax, eax
    a.jz('df5204_skip')
    a.raw(b'\x8B\x48\x48')                                    # mov ecx, [eax+0x48]
    a.jmp_va(ax.S4F5204_RESUME_VA)
    a.label('df5204_skip')
    a.jmp_va(ax.S4F5204_SKIP_VA)
