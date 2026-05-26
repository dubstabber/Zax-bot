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
from ..asm import Asm
from ..hook.bot_lookup import emit_is_bot_controller
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    a.label('detour_542360')
    emit_is_bot_controller(a, layout,
                           on_not_bot='s542360_normal',
                           label_prefix='s542360')
    # EAX = &bot_indices[slot] on bot match — movement detour doesn't need the
    # slot itself, just the bot/host distinction, so we drop EAX.
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
