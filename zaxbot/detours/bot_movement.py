"""``detour_542360`` — bot movement-vector synthesis.

ECX = ``CPlayerWalkingControlAI``. We identify bot controllers via the
controller's ``player_num`` field at ``[ecx+0x1C]`` (set by sub_542550),
matched against ``bot_indices[]``. This is stable across respawns: the
per-respawn controller pointer drifts and would eventually leave a stale
``bot_controllers[]`` table, but the participant index ``bot_indices[i]``
holds is fixed for the lifetime of the match. For bot controllers we
return a zero movement vector while leaving the controller's active flag
intact; this allows the caller to run its idle-animation path instead of
bypassing controller animation. Host controllers fall through to the
original prologue + resume."""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    bot_indices_va = layout.va('bot_indices')

    a.label('detour_542360')
    a.raw(b'\x8B\x51\x1C')                                # mov edx, [ecx+0x1C] (player_num)
    a.raw(b'\x85\xD2')                                    # test edx, edx
    a.jz('s542360_normal')                                # player_num 0 = host
    a.raw(b'\xB8' + le32(bot_indices_va))                 # mov eax, bot_indices
    a.label('s542360_scan')
    a.raw(b'\x3B\x10')                                    # cmp edx, [eax]
    a.jz('s542360_bot')
    a.raw(b'\x83\xC0\x04')                                # add eax, 4
    a.raw(b'\x3D' + le32(bot_indices_va + 4 * cfg.MAX_BOT_SLOTS))  # cmp eax, end
    a.jb('s542360_scan')
    a.jmp('s542360_normal')
    a.label('s542360_bot')
    a.raw(b'\x8B\x44\x24\x04')                            # mov eax, [esp+4] (float[2] out_vec)
    a.raw(b'\x85\xC0'); a.jz('s542360_skip_vec')
    a.raw(b'\xC7\x00\x00\x00\x00\x00')                    # out_vec[0] = 0.0
    a.raw(b'\xC7\x40\x04\x00\x00\x00\x00')                # out_vec[1] = 0.0
    a.label('s542360_skip_vec')
    a.raw(b'\x8B\x44\x24\x08')                            # mov eax, [esp+8] (float* out_angle)
    a.raw(b'\x85\xC0'); a.jz('s542360_ret')
    a.raw(b'\xC7\x00\x00\x00\x00\x00')                    # *out_angle = 0.0
    a.label('s542360_ret')
    a.raw(b'\xC2\x14\x00')                                # ret 0x14
    a.label('s542360_normal')
    a.raw(ax.S542360_PROLOGUE)
    a.jmp_va(ax.S542360_RESUME)
