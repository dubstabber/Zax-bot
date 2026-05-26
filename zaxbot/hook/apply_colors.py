"""``apply_bot_colors`` — write a bot's chosen color pair to its appearance.

Mirrors the engine's own ``sub_5ABE80`` (server-side handler for
``CClientOptionsToServer``):

  1. If ``sub_4FC7C0(char) > 0``: target = ``sub_4FC7D0(char, 0)``.
     Otherwise target = char itself.
  2. ``app = sub_418790(class=*(APPEARANCE_CLASS_VA), target)``.
     Returns NULL when the entity has no appearance component.
  3. Write ``color1`` and ``color2`` as floats into ``app + 0xC`` / ``+0x18``.
  4. Invoke the active gametype's ``vtable[+0x9C]`` so CTF can preempt
     ``color1`` with the team palette (DM/SK install a nullsub there).

Pulled out of ``spawn.py`` so the post-spawn coloring step is one
``call apply_bot_colors`` instead of 55 lines inline. The pre-spawn pcfg
write that paints SK's collector still lives in ``spawn.py`` (it has to
happen before ``sub_59DF90`` and reads/writes different fields).

Inputs (via scratch):
  ``botchar``         — bot's char ptr (non-NULL by caller; helper rechecks).
  ``botp``            — bot's participant (passed to the gametype callback).
  ``picked_name_idx`` — selects the (color1, color2) row from ``bot_colors``.

Side effects: writes two floats into the bot's appearance component.
Clobbers: EAX, ECX, EDX, ESI, ST*.
"""

from .. import addresses as ax
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    botchar_va         = layout.va('botchar')
    botp_va            = layout.va('botp')
    bot_colors_va      = layout.va('bot_colors')
    picked_name_idx_va = layout.va('picked_name_idx')

    a.label('apply_bot_colors')

    # Validate bot char.
    a.raw(b'\xA1' + le32(botchar_va))                         # eax = bot char
    a.raw(b'\x85\xC0'); a.jz('apply_colors_done')

    # Walk to the appearance-bearing entity. Bots normally take the child
    # branch (sub_4FC7C0 > 0), but fall back to the char itself so the
    # helper is safe to call on any entity.
    a.raw(b'\x8B\xC8')                                        # mov ecx, eax (this = char)
    a.call_va(ax.SUB_4FC7C0_VA)                                # eax = child count
    a.raw(b'\x85\xC0'); a.jz('apply_colors_no_child')
    a.raw(b'\x6A\x00')                                         # push 0 (child idx)
    a.raw(b'\x8B\x0D' + le32(botchar_va))                      # ecx = bot char
    a.call_va(ax.SUB_4FC7D0_VA)                                # eax = child entity
    a.raw(b'\x85\xC0'); a.jz('apply_colors_done')
    a.raw(b'\x89\xC6')                                         # mov esi, eax (target = child)
    a.jmp('apply_colors_have_target')
    a.label('apply_colors_no_child')
    a.raw(b'\x8B\x35' + le32(botchar_va))                      # mov esi, [botchar]
    a.label('apply_colors_have_target')

    # Resolve appearance component; NULL means no color slots to write.
    a.raw(b'\x56')                                             # push esi (target)
    a.raw(b'\x8B\x0D' + le32(ax.APPEARANCE_CLASS_VA))          # ecx = appearance class
    a.call_va(ax.SUB_418790_VA)                                # eax = appearance* (retn 4)
    a.raw(b'\x85\xC0'); a.jz('apply_colors_done')

    # Pack the (color1, color2) ints from BOT_COLORS into floats at +0xC/+0x18.
    a.raw(b'\x8B\x15' + le32(picked_name_idx_va))              # edx = picked idx
    a.raw(b'\xC1\xE2\x03')                                     # shl edx, 3 (2 dwords/entry)
    a.raw(b'\x81\xC2' + le32(bot_colors_va))                   # add edx, bot_colors
    a.raw(b'\xFF\x32')                                         # push [edx]    color1 int
    a.raw(b'\xDB\x04\x24')                                     # fild dword [esp]
    a.raw(b'\xD9\x58' + bytes([ax.APPEARANCE_COLOR1_OFF]))     # fstp [eax+0xC]
    a.raw(b'\x83\xC4\x04')
    a.raw(b'\xFF\x72\x04')                                     # push [edx+4]  color2 int
    a.raw(b'\xDB\x04\x24')                                     # fild dword [esp]
    a.raw(b'\xD9\x58' + bytes([ax.APPEARANCE_COLOR2_OFF]))     # fstp [eax+0x18]
    a.raw(b'\x83\xC4\x04')

    # Hand control to the gametype's color1 override (CTF only — DM/SK use
    # nullsub_3 there). Mirrors the engine's behavior in sub_5ABE80.
    a.raw(b'\x89\xC6')                                         # mov esi, eax (save appearance)
    a.raw(b'\x8B\x0D' + le32(ax.MANAGER_GLOBAL_VA))            # ecx = mgr
    a.call_va(ax.SUB_59FF90_VA)                                # eax = active gametype
    a.raw(b'\x85\xC0'); a.jz('apply_colors_done')
    a.raw(b'\x8D\x56' + bytes([ax.APPEARANCE_COLOR1_OFF]))     # lea edx, [esi+0xC] (&color1)
    a.raw(b'\x52')                                             # push edx (a3 = &color1)
    a.raw(b'\xFF\x35' + le32(botp_va))                         # push [botp] (a2 = participant)
    a.raw(b'\x8B\x10')                                         # mov edx, [eax] (vtable)
    a.raw(b'\x8B\xC8')                                         # mov ecx, eax (this = gametype)
    a.raw(b'\xFF\x92' + le32(ax.GAMETYPE_COLOR1_VTBL_OFF))     # call [vtable+0x9C]

    a.label('apply_colors_done')
    a.raw(b'\xC3')                                             # ret
