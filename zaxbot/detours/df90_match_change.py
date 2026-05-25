"""``detour_df90`` for ``sub_59DF90``.

Captures a2 (``[esp+4]`` at entry) and detects match-change. ``cap_a2`` is a
per-match shared context (``sub_59BD50`` passes the same a2 for every player
in its setup loop), so a CHANGE in a2 between consecutive ``sub_59DF90`` calls
means a new match has started. When that happens we wipe the four bot scratch
arrays (participants/indices/chars/controllers — 64 contiguous dwords at
``scratch+0x180..0x280``) so leftover match-1 pointers don't falsely match
newly-allocated match-2 objects. (This was Bug 1: stale
``bot_controllers_va`` entries made ``detour_542360`` zero the host's
movement vector in subsequent matches.)"""

from .. import addresses as ax
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    cap_a2              = layout.va('cap_a2')
    bot_participants_va = layout.va('bot_participants')

    a.label('detour_df90')
    a.raw(b'\x50')                                # push eax
    a.raw(b'\x8B\x44\x24\x08')                    # mov eax, [esp+8] (incoming a2)
    a.raw(b'\x3B\x05' + le32(cap_a2))             # cmp eax, [cap_a2]
    a.jz('df90_same_match')
    # New match: clear 64 contiguous dwords at bot_participants..bot_controllers.
    a.raw(b'\x57\x51\x50')                        # push edi; push ecx; push eax
    a.raw(b'\xBF' + le32(bot_participants_va))    # mov edi, bot_participants_va
    a.raw(b'\xB9\x40\x00\x00\x00')                # mov ecx, 64
    a.raw(b'\x31\xC0')                            # xor eax, eax
    a.raw(b'\xFC')                                # cld
    a.raw(b'\xF3\xAB')                            # rep stosd
    a.raw(b'\x58\x59\x5F')                        # pop eax; pop ecx; pop edi
    a.label('df90_same_match')
    a.raw(b'\xA3' + le32(cap_a2))                 # mov [cap_a2], eax
    a.raw(b'\x58')                                # pop eax (caller's)
    a.raw(b'\x53')                                # push ebx (displaced prologue)
    a.raw(b'\x8B\x5C\x24\x0C')                    # mov ebx, [esp+0xC]
    a.jmp_va(ax.DF90_RESUME)
