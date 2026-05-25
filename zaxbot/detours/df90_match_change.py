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
    bot_team_va         = layout.va('bot_team')
    host_team_va        = layout.va('host_team')

    a.label('detour_df90')
    a.raw(b'\x50')                                # push eax
    a.raw(b'\x8B\x44\x24\x08')                    # mov eax, [esp+8] (incoming a2)
    a.raw(b'\x3B\x05' + le32(cap_a2))             # cmp eax, [cap_a2]
    a.jz('df90_same_match')
    # New match: clear 64 contiguous dwords at bot_participants..bot_controllers,
    # then init the team-cache tables to -1 (sentinel "unknown team").
    a.raw(b'\x57\x51\x50')                        # push edi; push ecx; push eax
    a.raw(b'\xBF' + le32(bot_participants_va))    # mov edi, bot_participants_va
    a.raw(b'\xB9\x40\x00\x00\x00')                # mov ecx, 64
    a.raw(b'\x31\xC0')                            # xor eax, eax
    a.raw(b'\xFC')                                # cld
    a.raw(b'\xF3\xAB')                            # rep stosd
    a.raw(b'\xBF' + le32(bot_team_va))            # mov edi, bot_team_va
    a.raw(b'\xB9\x11\x00\x00\x00')                # mov ecx, 17  (16 bot_team + 1 host_team)
    a.raw(b'\x83\xC8\xFF')                        # or eax, -1
    a.raw(b'\xF3\xAB')                            # rep stosd (writes bot_team[16] + host_team)
    # Once we've pre-grown mgr+0x290 to 16 entries (match 1 onwards), the
    # buffer outlives the match: slots populated by match N can still hold
    # freed char pointers at the start of match N+1. Per-frame fire/aim
    # iterates the array and calls sub_4FB0A0 on every non-NULL slot, so any
    # stale pointer crashes via its (now invalid) vtable. Clear all 16 slots
    # to NULL on match change. Only the engine's sub_59DF90 (which we're the
    # prologue of) refills them, so this is consistent with the engine's own
    # "match started" state.
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))   # mov eax, [mgr]
    a.raw(b'\x85\xC0'); a.jz('df90_skip_mgr_clear')
    a.raw(b'\x83\xB8\x98\x02\x00\x00\x10')        # cmp [eax+0x298], 16
    a.jb('df90_skip_mgr_clear')                   # capacity not pre-grown yet
    a.raw(b'\x8B\xB8\x90\x02\x00\x00')            # mov edi, [eax+0x290]
    a.raw(b'\x85\xFF'); a.jz('df90_skip_mgr_clear')
    a.raw(b'\xB9\x10\x00\x00\x00')                # mov ecx, 16
    a.raw(b'\x31\xC0')                            # xor eax, eax
    a.raw(b'\xF3\xAB')                            # rep stosd
    a.label('df90_skip_mgr_clear')
    a.raw(b'\x58\x59\x5F')                        # pop eax; pop ecx; pop edi
    a.label('df90_same_match')
    a.raw(b'\xA3' + le32(cap_a2))                 # mov [cap_a2], eax
    a.raw(b'\x58')                                # pop eax (caller's)
    a.raw(b'\x53')                                # push ebx (displaced prologue)
    a.raw(b'\x8B\x5C\x24\x0C')                    # mov ebx, [esp+0xC]
    a.jmp_va(ax.DF90_RESUME)
