"""``detour_5436F0`` — bot input -> fire/aim angle.

The detour itself is now a thin glue layer:

  1. Identify the firing controller via ``emit_is_bot_controller``; non-bots
     fall through to the original prologue + resume.
  2. Validate the engine's per-call args (bot char ptr at ``[esp+4]``,
     out-angle float ptr at ``[esp+8]``).
  3. Resolve the firing bot's team (CTF only; sentinel ``-1`` disables the
     filter for DM/SK) so ``pick_target`` can short-circuit teammates.
  4. Call ``pick_target`` (see ``bot_perception``) to walk candidates and
     pick the closest visible enemy.
  5. If a target was picked, convert the cached (dx, dy) into a world angle
     via ``sub_509100`` and write it through the out-angle pointer, then
     return ``AL = 1`` so ``sub_543830`` continues into Primary fire.
     Otherwise return ``AL = 0`` to block firing this frame.

All target-selection logic lives in ``bot_perception.py`` — keep this file
focused on the engine ABI (stack frame, return convention, prologue resume).
Future leading-shot / movement code should also consume ``pick_target``'s
cached output rather than rerunning the scan.

Entry: ECX = controller, ``[esp+4]`` = char, ``[esp+8]`` = float* out_angle.
Return: AL = 1 to fire, 0 to skip.
"""

from .. import addresses as ax
from ..asm import Asm, le32
from ..hook.bot_lookup import emit_addr_to_slot, emit_is_bot_controller
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    bot_team_va        = layout.va('bot_team')
    best_dx_va         = layout.va('best_dx')
    best_dy_va         = layout.va('best_dy')
    menu_mode_va       = layout.va('menu_mode')
    bot_slot_tmp_va    = layout.va('bot_slot_tmp')
    our_team_tmp_va    = layout.va('our_team_tmp')
    bot_char_tmp_va    = layout.va('bot_char_tmp')

    a.label('detour_5436F0')

    # --- (1) Bot identification. emit_is_bot_controller leaves EAX pointing
    # into bot_indices on bot match (and clobbers EDX); host falls through.
    emit_is_bot_controller(a, layout,
                           on_not_bot='s5436f0_normal',
                           label_prefix='s5436f0')
    emit_addr_to_slot(a, layout)                          # eax = slot
    a.raw(b'\xA3' + le32(bot_slot_tmp_va))                # mov [bot_slot_tmp], eax

    # --- (2) Validate per-call args.
    a.raw(b'\x8B\x44\x24\x04')                            # mov eax, [esp+4] (bot char)
    a.raw(b'\x85\xC0'); a.jz('s5436f0_no_fire')
    a.raw(b'\xA3' + le32(bot_char_tmp_va))                # mov [bot_char_tmp], eax
    a.raw(b'\x8B\x54\x24\x08')                            # mov edx, [esp+8] (out_angle)
    a.raw(b'\x85\xD2'); a.jz('s5436f0_no_fire')

    # --- (3) Team filter setup. -1 sentinel = no filter (DM/SK).
    a.raw(b'\xC7\x05' + le32(our_team_tmp_va) + le32(0xFFFFFFFF))
    a.raw(b'\x83\x3D' + le32(menu_mode_va) + b'\x01')     # cmp [menu_mode], 1 (CTF)
    a.jnz('s5436f0_team_done')
    a.raw(b'\x8B\x15' + le32(bot_slot_tmp_va))            # mov edx, [bot_slot_tmp]
    a.raw(b'\x8B\x04\x95' + le32(bot_team_va))            # mov eax, [bot_team + edx*4]
    a.raw(b'\xA3' + le32(our_team_tmp_va))                # mov [our_team_tmp], eax
    a.label('s5436f0_team_done')

    # --- (4) Run the perception scan.
    a.call_lbl('pick_target')                             # AL = 1 if target picked
    a.raw(b'\x84\xC0')                                    # test al, al
    a.jz('s5436f0_no_fire')

    # --- (5) Lead the target: rewrite best_dx/best_dy with the predicted
    # intercept point. apply_lead reads best_dist_sq + best_vx/vy + proj_speed
    # from scratch (all populated by pick_target / the build-time init), so
    # the existing sub_509100 call below keeps working unchanged.
    a.call_lbl('apply_lead')

    # --- (6) Convert (dx, dy) -> angle and write to out.
    a.raw(b'\xFF\x35' + le32(best_dx_va))                 # push [best_dx]
    a.raw(b'\xFF\x35' + le32(best_dy_va))                 # push [best_dy]
    a.call_va(ax.SUB_509100)                              # __stdcall, st0 = angle
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
