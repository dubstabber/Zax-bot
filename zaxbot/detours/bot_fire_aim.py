"""``detour_5436F0`` — bot input -> fire/aim angle.

Sub_5436F0 is the input-to-aim gate. The original reads global action bits
and writes an angle derived from host input/cursor; for bot controllers we
synthesize "fire" when the host is in range and aim at the host. Host
controllers take the original path unchanged.

Entry: ECX = controller, ``[esp+4]`` = char, ``[esp+8]`` = float* out_angle.
Return: AL = 1 to let ``sub_543830`` continue into Primary fire, 0 to block.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    bot_controllers_va = layout.va('bot_controllers')
    hostchar_va        = layout.va('hostchar')
    bot_pos_va         = layout.va('bot_pos')
    host_pos_va        = layout.va('host_pos')
    bot_dx_va          = layout.va('bot_dx')
    bot_dy_va          = layout.va('bot_dy')
    fire_range_sq_va   = layout.va('fire_range_sq')

    a.label('detour_5436F0')
    a.raw(b'\xB8' + le32(bot_controllers_va))             # mov eax, bot_controllers
    a.raw(b'\xBA' + le32(cfg.MAX_BOT_SLOTS))              # mov edx, MAX_BOT_SLOTS
    a.label('s5436f0_scan')
    a.raw(b'\x3B\x08')                                    # cmp ecx, [eax]
    a.jz('s5436f0_bot')
    a.raw(b'\x83\xC0\x04')                                # add eax, 4
    a.raw(b'\x4A')                                        # dec edx
    a.jnz('s5436f0_scan')
    a.jmp('s5436f0_normal')

    a.label('s5436f0_bot')
    a.raw(b'\x8B\x44\x24\x04')                            # mov eax, [esp+4]  (bot char)
    a.raw(b'\x85\xC0'); a.jz('s5436f0_no_fire')
    a.raw(b'\x8B\x54\x24\x08')                            # mov edx, [esp+8]  (out_angle)
    a.raw(b'\x85\xD2'); a.jz('s5436f0_no_fire')
    a.raw(b'\x8B\x15' + le32(ax.MANAGER_GLOBAL_VA))       # mov edx, [mgr]
    a.raw(b'\x85\xD2'); a.jz('s5436f0_no_fire')
    a.raw(b'\x8B\x92\x90\x02\x00\x00')                    # mov edx, [edx+0x290] (char array)
    a.raw(b'\x85\xD2'); a.jz('s5436f0_no_fire')
    a.raw(b'\x8B\x12')                                    # mov edx, [edx] (host char)
    a.raw(b'\x85\xD2'); a.jz('s5436f0_no_fire')
    a.raw(b'\x89\x15' + le32(hostchar_va))                # mov [hostchar], edx

    # Read live entity positions via the same helper used by engine AI code.
    a.raw(b'\x68' + le32(bot_pos_va))
    a.raw(b'\x89\xC1')                                    # mov ecx, eax (bot char)
    a.call_va(ax.SUB_4FB0A0_VA)
    a.raw(b'\x8B\x0D' + le32(hostchar_va))                # mov ecx, [hostchar]
    a.raw(b'\x68' + le32(host_pos_va))
    a.call_va(ax.SUB_4FB0A0_VA)

    # dx = host.x - bot.x; dy = host.y - bot.y; compare d^2 to fire_range_sq.
    a.raw(b'\xD9\x05' + le32(host_pos_va))
    a.raw(b'\xD8\x25' + le32(bot_pos_va))
    a.raw(b'\xD9\x15' + le32(bot_dx_va))
    a.raw(b'\xD8\xC8')                                    # fmul st(0), st(0)
    a.raw(b'\xD9\x05' + le32(host_pos_va + 4))
    a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))
    a.raw(b'\xD9\x15' + le32(bot_dy_va))
    a.raw(b'\xD8\xC8')                                    # fmul st(0), st(0)
    a.raw(b'\xDE\xC1')                                    # faddp st(1), st(0)
    a.raw(b'\xD8\x1D' + le32(fire_range_sq_va))
    a.raw(b'\xDF\xE0')                                    # fnstsw ax
    a.raw(b'\x9E')                                        # sahf
    a.ja('s5436f0_no_fire')                               # d^2 > threshold

    # Engine line-of-sight gate: only fire if no wall/door is between us and
    # the host. sub_491380 is __thiscall(src_char, tgt_char, 0, NULL, 2, NULL)
    # used by monster AI; returns al=1 when the swept trace ends at the target.
    a.raw(b'\x8B\x44\x24\x04')                            # mov eax, [esp+4]  (bot char)
    a.raw(b'\x8B\x15' + le32(hostchar_va))                # mov edx, [hostchar]
    a.raw(b'\x6A\x00')                                    # push 0      (a6: src offset = NULL)
    a.raw(b'\x6A\x02')                                    # push 2      (a5: flag mask, per monster AI)
    a.raw(b'\x6A\x00')                                    # push 0      (a4: tgt offset = NULL)
    a.raw(b'\x6A\x00')                                    # push 0      (a3)
    a.raw(b'\x52')                                        # push edx    (a2: host char)
    a.raw(b'\x8B\xC8')                                    # mov ecx, eax (this = bot char)
    a.call_va(ax.SUB_491380_VA)                           # ret 14h
    a.raw(b'\x84\xC0')                                    # test al, al
    a.jz('s5436f0_no_fire')                               # 0 -> blocked

    a.raw(b'\xFF\x35' + le32(bot_dx_va))                  # push [bot_dx]
    a.raw(b'\xFF\x35' + le32(bot_dy_va))                  # push [bot_dy]
    a.call_va(ax.SUB_509100)
    a.raw(b'\x8B\x44\x24\x08')                            # mov eax, [esp+8] (out_angle)
    a.raw(b'\xD9\x18')                                    # fstp dword [eax]
    a.raw(b'\xB0\x01')                                    # mov al, 1
    a.raw(b'\xC2\x08\x00')                                # ret 8

    a.label('s5436f0_no_fire')
    a.raw(b'\x31\xC0')                                    # xor eax, eax
    a.raw(b'\xC2\x08\x00')                                # ret 8

    a.label('s5436f0_normal')
    a.raw(ax.S5436F0_PROLOGUE)
    a.jmp_va(ax.S5436F0_RESUME)
