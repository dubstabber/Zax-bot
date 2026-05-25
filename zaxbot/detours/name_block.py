"""Sub_480800 name-block detours for synthetic bot ids.

Three detours emitted in source order:

- ``detour_name_query1`` — replaces ``sub_480800``'s first
  ``call [edx+0x24]`` (DP size-query) via a JMP patch. Synthetic ids return
  fake S_OK; real ids re-emit the call and replay the displaced error-handler.
  JMP (not call) is critical here: a call would leave a stale ret_addr on the
  stack and shift the DP method's ``this`` for the real-id path.
- ``detour_name_query2`` — same idea for the data-fill call. Synthetic ids
  pick a random ASCII bot name via ``sub_55C4E0(RNG, 0, NUM-1)`` and write the
  wide-char pointer into ``buf+8`` so ``WideCharToMultiByte`` produces a real
  name in ``MultiByteStr``.
- ``detour_name_block_skip`` — at ``0x480889``, skip the entire ~260B
  name-block for synthetic ids. ESI holds the current entry's player id;
  real ids run the original block."""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    bot_names_va = layout.va('bot_names')

    # --- detour_name_query1 --------------------------------------------------
    a.label('detour_name_query1')
    a.raw(b'\x8B\x44\x24\x04')                               # mov eax, [esp+4] (player_id)
    a.raw(b'\x3D' + le32(cfg.SYNTHETIC_ID_LO))
    a.jb('dq1_real')
    a.raw(b'\x3D' + le32(cfg.SYNTHETIC_ID_HI))
    a.jae('dq1_real')
    # Synthetic: write 24 to *pdsize, return S_OK, pop 4 args, resume.
    a.raw(b'\x8B\x54\x24\x0C')                               # mov edx, [esp+0xC] (pdsize_ptr)
    a.raw(b'\xC7\x02\x18\x00\x00\x00')                       # mov [edx], 24
    a.raw(b'\x31\xC0')                                       # xor eax, eax
    a.raw(b'\x83\xC4\x10')                                   # add esp, 0x10
    a.jmp_va(ax.S480800_DPQ1_END_VA)
    a.label('dq1_real')
    a.raw(b'\x8B\x14\x24')                                   # mov edx, [esp+0] (ppv)
    a.raw(b'\x8B\x12')                                       # mov edx, [edx] (vtable)
    a.raw(b'\xFF\x52\x24')                                   # call [edx+0x24]
    a.raw(b'\x3D\x00\x81\x15\x80')                           # cmp eax, 0x80158100
    a.jz('dq1_done')
    a.raw(b'\x85\xC0')                                       # test eax, eax
    a.jge('dq1_done')
    a.raw(b'\x89\xC1')                                       # mov ecx, eax
    a.call_va(ax.SUB_47F350_VA)
    a.label('dq1_done')
    a.jmp_va(ax.S480800_DPQ1_END_VA)

    # --- detour_name_query2 --------------------------------------------------
    a.label('detour_name_query2')
    a.raw(b'\x8B\x44\x24\x04')                               # mov eax, [esp+4] (player_id)
    a.raw(b'\x3D' + le32(cfg.SYNTHETIC_ID_LO))
    a.jb('dq2_real')
    a.raw(b'\x3D' + le32(cfg.SYNTHETIC_ID_HI))
    a.jae('dq2_real')
    # Synthetic: pick a random name index and write name_va into buf+8.
    a.raw(b'\x6A' + bytes([cfg.NUM_BOT_NAMES - 1]))          # push (NUM-1)
    a.raw(b'\x6A\x00')                                       # push 0
    a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                     # mov ecx, RNG
    a.call_va(ax.RNG_SUB)                                    # eax = random idx
    a.raw(b'\xC1\xE0\x05')                                   # shl eax, 5 (NAME_SLOT_SIZE = 32)
    a.raw(b'\x05' + le32(bot_names_va))                      # add eax, bot_names_va
    a.raw(b'\x8B\x54\x24\x08')                               # mov edx, [esp+8] (buf)
    a.raw(b'\x89\x42\x08')                                   # mov [edx+8], eax (name ptr)
    a.raw(b'\x31\xC0')                                       # xor eax, eax (S_OK)
    a.raw(b'\x83\xC4\x10')                                   # add esp, 0x10
    a.jmp_va(ax.S480800_DPQ2_END_VA)
    a.label('dq2_real')
    a.raw(b'\x8B\x14\x24')                                   # mov edx, [esp+0] (ppv)
    a.raw(b'\x8B\x12')
    a.raw(b'\xFF\x52\x24')                                   # call [edx+0x24]
    a.raw(b'\x85\xC0')                                       # test eax, eax
    a.jge('dq2_done')
    a.raw(b'\x89\xC1')
    a.call_va(ax.SUB_47F350_VA)
    a.label('dq2_done')
    a.jmp_va(ax.S480800_DPQ2_END_VA)

    # --- detour_name_block_skip ---------------------------------------------
    a.label('detour_name_block_skip')
    a.raw(b'\x81\xFE' + le32(cfg.SYNTHETIC_ID_LO))            # cmp esi, SYNTHETIC_ID_LO
    a.jb('nb_normal')
    a.raw(b'\x81\xFE' + le32(cfg.SYNTHETIC_ID_HI))            # cmp esi, SYNTHETIC_ID_HI
    a.jae('nb_normal')
    a.raw(b'\x83\xC4\x04')                                    # add esp, 4 (consume the pushed arg)
    a.raw(b'\x8B\x7C\x24\x1C')                                # mov edi, [esp+0x1C] (queue cursor)
    a.jmp_va(ax.S480800_NAMEBLK_END_VA)
    a.label('nb_normal')
    a.raw(b'\xA1\x2C\xDC\x6B\x00')                            # mov eax, ppv (displaced 5 bytes)
    a.jmp_va(ax.S480800_NAMEBLK_AFTER_VA)
