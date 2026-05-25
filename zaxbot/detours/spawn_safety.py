"""Defangs for engine crashes that surface during bot / late-join spawn.

Four detours emitted in source order:

- ``detour_4EF900_test`` — char-array slot-set helper inside ``sub_4EF900``;
  the engine derefs whatever junk is at ``[arr+idx*4]`` (sub_4FC200 →
  ValueName lookup). Clamp values < image base to zero so the cleanup call
  is skipped on uninit slots. See [[garbage-slot-crash]] memory.
- ``detour_4FC7C0`` — ``sub_5ABE80`` calls ``sub_4FC7C0`` with
  ``this = char_arr[v2]`` during PC2-join validation; clamp garbage ``this``
  to "return 0" before the deref.
- ``detour_417390`` — same garbage-this pattern, reached via
  ``sub_5ABE80 -> sub_4FC7D0(0) -> sub_417390``.
- ``detour_5AC299`` — wraps ``sub_5AC230``'s ``call [ebx+0x1C4]`` (= sub_59DF90)
  to bump ``mgr+0x294`` (count) after the call returns. The engine relies on
  this for host/bot's natural paths; for late-joining PC2 the bump doesn't
  fire and the following ``sub_4F5D60(idx)`` returns 0 → crash."""

from .. import addresses as ax
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    # --- detour_4EF900_test (5 displaced bytes at S4EF900_TEST_VA) -----------
    a.label('detour_4EF900_test')
    a.raw(b'\x81\xF9\x00\x00\x40\x00')                       # cmp ecx, 0x400000
    a.jae('s4ef900_existing_valid')
    a.raw(b'\xC7\x04\x98\x00\x00\x00\x00')                   # mov [eax+ebx*4], 0
    a.raw(b'\x31\xC9')                                       # xor ecx, ecx
    a.label('s4ef900_existing_valid')
    a.raw(b'\x85\xC9')                                       # test ecx, ecx (original)
    a.jz('s4ef900_skip_call')
    a.raw(b'\x68' + le32(ax.VALUENAME_VA))                   # push offset ValueName
    a.raw(b'\x6A\xFF')                                       # push -1
    a.raw(b'\x56')                                           # push esi (this)
    a.call_va(ax.SUB_4FC200_VA)                              # call sub_4FC200
    a.label('s4ef900_skip_call')
    a.jmp_va(ax.S4EF900_TEST_RESUME)

    # --- detour_4FC7C0 -------------------------------------------------------
    a.label('detour_4FC7C0')
    a.raw(b'\x81\xF9\x00\x00\x40\x00')                       # cmp ecx, 0x400000
    a.jb('s4fc7c0_null')
    a.raw(b'\x8B\x41\x08')                                   # mov eax, [ecx+8]
    a.raw(b'\x85\xC0')                                       # test eax, eax
    a.jmp_va(ax.S4FC7C0_RESUME)
    a.label('s4fc7c0_null')
    a.raw(b'\x31\xC0')                                       # xor eax, eax
    a.raw(b'\xC3')                                           # retn

    # --- detour_417390 -------------------------------------------------------
    a.label('detour_417390')
    a.raw(b'\x81\xF9\x00\x00\x40\x00')                       # cmp ecx, 0x400000
    a.jb('s417390_null')
    a.raw(b'\x8B\x49\x04')                                   # mov ecx, [ecx+4]
    a.raw(b'\x85\xC9')                                       # test ecx, ecx
    a.jmp_va(ax.S417390_RESUME)
    a.label('s417390_null')
    a.raw(b'\x31\xC0')                                       # xor eax, eax
    a.raw(b'\xC2\x04\x00')                                   # retn 4

    # --- detour_5AC299 -------------------------------------------------------
    a.label('detour_5AC299')
    a.raw(b'\xFF\x93\xC4\x01\x00\x00')                       # call [ebx+0x1C4] (= sub_59DF90)
    a.raw(b'\x51\x52\x50')                                   # push ecx; push edx; push eax
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))              # mov eax, [mgr]
    a.raw(b'\x85\xC0'); a.jz('s5ac299_skip')
    a.raw(b'\x8D\x56\x01')                                   # lea edx, [esi+1] (= idx+1)
    a.raw(b'\x39\x90\x94\x02\x00\x00')                       # cmp [eax+0x294], edx
    a.jae('s5ac299_skip')
    a.raw(b'\x89\x90\x94\x02\x00\x00')                       # mov [eax+0x294], edx
    a.label('s5ac299_skip')
    a.raw(b'\x58\x5A\x59')                                   # pop eax; pop edx; pop ecx
    a.raw(b'\xC3')                                           # ret
