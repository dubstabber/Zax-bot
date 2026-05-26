"""Walking-controller construction detours.

Three detours, emitted in source order:

- ``detour_5AA4E0`` — ``CCameraTrakerAI`` ctor. When ``botmode==1`` return NULL
  so the bot doesn't get a camera tracker (would steal the camera from host).
- ``detour_4FBC50`` — component attach. Adds an early NULL-check on the
  component arg; the engine's ``sub_4FBC50(0)`` callers are buggy (the source's
  OOM/NULL path derefs unconditionally). Fixes both our case and the latent
  engine bug.
- ``detour_542550`` — ``sub_542550`` (controller player_num init). Two paths:
  bot-spawn (botmode==1) captures the freshly-built controller into
  ``bot_controllers_va[active_bot_slot]``; otherwise scrubs stale entries
  matching ECX AND recaptures by player-index match (which restores the
  custom fire/aim hook after a bot's natural respawn — see notes in the
  detour body)."""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..hook.bot_lookup import emit_scan_bot_indices
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    botmode_va         = layout.va('botmode')
    active_bot_slot_va = layout.va('active_bot_slot')
    bot_controllers_va = layout.va('bot_controllers')
    bot_indices_va     = layout.va('bot_indices')
    tmp_idx_va         = layout.va('tmp_idx')
    bot_indices_to_controllers = bot_controllers_va - bot_indices_va

    # --- detour_5AA4E0 -------------------------------------------------------
    a.label('detour_5AA4E0')
    a.raw(b'\x83\x3D' + le32(botmode_va) + b'\x00')      # cmp [botmode], 0
    a.jz('aa4e0_normal')
    a.raw(b'\x31\xC0\xC3')                                # xor eax,eax; ret
    a.label('aa4e0_normal')
    a.raw(b'\x56\x8B\xF1')                                # push esi; mov esi,ecx
    a.call_va(ax.SUB_4FD060_VA)
    a.jmp_va(ax.SAA4E0_RESUME)

    # --- detour_4FBC50 -------------------------------------------------------
    a.label('detour_4FBC50')
    a.raw(b'\x83\x7C\x24\x04\x00')                        # cmp dword [esp+4], 0
    a.jnz('fbc50_normal')
    a.raw(b'\xC2\x04\x00')                                # ret 4  (early exit if NULL)
    a.label('fbc50_normal')
    a.raw(b'\x56')                                        # push esi
    a.raw(b'\x8B\xF1')                                    # mov esi, ecx
    a.raw(b'\x8D\x54\x24\x08')                            # lea edx, [esp+8]
    a.jmp_va(ax.FBC50_RESUME)

    # --- detour_542550 -------------------------------------------------------
    a.label('detour_542550')
    a.raw(b'\x83\x3D' + le32(botmode_va) + b'\x00')       # cmp [botmode], 0
    a.jz('s542550_scrub')
    # Bot-spawn path: capture this controller for active_bot_slot (once).
    a.raw(b'\xA1' + le32(active_bot_slot_va))              # mov eax,[active_bot_slot]
    a.raw(b'\x83\xF8' + bytes([cfg.MAX_BOT_SLOTS]))        # cmp eax, MAX_BOT_SLOTS
    a.jae('s542550_normal')
    a.raw(b'\x83\x3C\x85' + le32(bot_controllers_va) + b'\x00')
    a.jnz('s542550_normal')                                # already captured
    a.raw(b'\x89\x0C\x85' + le32(bot_controllers_va))      # bot_controllers[eax] = ecx
    a.jmp('s542550_normal')

    a.label('s542550_scrub')
    # (a) Scrub stale entries matching ECX.
    a.raw(b'\xB8' + le32(bot_controllers_va))              # mov eax, bot_controllers
    a.raw(b'\xBA' + le32(cfg.MAX_BOT_SLOTS))                # mov edx, MAX_BOT_SLOTS
    a.raw(b'\x39\x08')                                     # cmp [eax], ecx
    a.raw(b'\x75\x06')                                     # jne +6
    a.raw(b'\xC7\x00\x00\x00\x00\x00')                     # mov dword [eax], 0
    a.raw(b'\x83\xC0\x04')                                 # add eax, 4
    a.raw(b'\x4A')                                         # dec edx
    a.raw(b'\x75\xF0')                                     # jne -16
    # (b) Capture by player-index match (post-natural-respawn for bots).
    a.raw(b'\x8B\x54\x24\x04')                             # mov edx, [esp+4] (player idx)
    a.raw(b'\x85\xD2'); a.jz('s542550_normal')             # idx 0 = host
    emit_scan_bot_indices(a, layout,
                          on_no_match='s542550_normal',
                          label_prefix='s542550_recap')
    # EAX = &bot_indices[i]. bot_controllers lives at the same stride one
    # whole array later, so writing through that delta restores the bot's
    # controller binding after a natural respawn.
    a.raw(b'\x89\x88' + le32(bot_indices_to_controllers))  # mov [eax+(controllers-indices)], ecx

    a.label('s542550_normal')
    a.raw(b'\x8B\x44\x24\x04')                            # mov eax, [esp+4]
    a.raw(b'\x56')                                        # push esi
    a.raw(b'\x8B\xF1')                                    # mov esi, ecx
    a.jmp_va(ax.S542550_RESUME)
