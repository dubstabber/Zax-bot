"""``detour_5436F0`` — bot input -> fire/aim angle.

For bot controllers we synthesize fire/aim toward the nearest visible enemy:

  - Walk all 16 slots of ``mgr+0x290`` (the char array).
  - Skip NULL and the bot itself.
  - In CTF (``menu_mode == 1``) only, skip same-team candidates. Teams are
    read from a scratch cache populated at spawn time (``bot_team[16]`` for
    bots, ``host_team`` for the local player). The cache exists because
    the engine's ``sub_5BA820`` helper triggers a worldmgr sync that walks
    the char array and is unsafe to call in a per-frame hot path (it trips
    on ``sub_4FC200`` for any non-NULL garbage slot — the same engine bug
    described in [[garbage-slot-crash]]).
  - Cheapest gates first: distance² against ``FIRE_RANGE_SQ`` before the
    engine LOS sweep at ``sub_491380``.
  - Track the closest survivor and aim/fire at it. No fallback to the host
    when nothing passes.

Notes:
  - PC2 humans aren't in any cache, so they're treated as enemies in CTF
    too (still shot regardless of team). Host and other bots are filtered
    correctly — that's the user's primary complaint resolved.

Entry: ECX = controller, ``[esp+4]`` = char, ``[esp+8]`` = float* out_angle.
Return: AL = 1 to let ``sub_543830`` continue into Primary fire, 0 to block.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    bot_indices_va     = layout.va('bot_indices')
    bot_team_va        = layout.va('bot_team')
    host_team_va       = layout.va('host_team')
    host_part_va       = layout.va('host_part')
    bot_pos_va         = layout.va('bot_pos')
    bot_dx_va          = layout.va('bot_dx')
    bot_dy_va          = layout.va('bot_dy')
    fire_range_sq_va   = layout.va('fire_range_sq')
    menu_mode_va       = layout.va('menu_mode')
    cand_pos_va        = layout.va('cand_pos')
    cand_tmp_va        = layout.va('cand_tmp')
    curr_dist_sq_va    = layout.va('curr_dist_sq')
    best_target_va     = layout.va('best_target')
    best_dist_sq_va    = layout.va('best_dist_sq')
    best_dx_va         = layout.va('best_dx')
    best_dy_va         = layout.va('best_dy')
    bot_slot_tmp_va    = layout.va('bot_slot_tmp')
    cand_idx_va        = layout.va('cand_idx')
    our_team_tmp_va    = layout.va('our_team_tmp')
    bot_char_tmp_va    = layout.va('bot_char_tmp')

    a.label('detour_5436F0')

    # --- Controller player_num gate. The walking controller stores its
    # player_num at [ecx+0x1C] (set by sub_542550). Match that against
    # bot_indices[]; this is stable across respawns because participant
    # indices don't shift mid-match, whereas the per-respawn controller
    # *pointer* can drift out of bot_controllers[] after enough heap churn.
    a.raw(b'\x8B\x51\x1C')                                # mov edx, [ecx+0x1C]
    a.raw(b'\x85\xD2')                                    # test edx, edx
    a.jz('s5436f0_normal')                                # player_num 0 = host
    a.raw(b'\xB8' + le32(bot_indices_va))                 # mov eax, bot_indices
    a.label('s5436f0_scan')
    a.raw(b'\x3B\x10')                                    # cmp edx, [eax]
    a.jz('s5436f0_bot')
    a.raw(b'\x83\xC0\x04')                                # add eax, 4
    a.raw(b'\x3D' + le32(bot_indices_va + 4 * cfg.MAX_BOT_SLOTS))  # cmp eax, end
    a.jb('s5436f0_scan')
    a.jmp('s5436f0_normal')

    a.label('s5436f0_bot')
    # eax = &bot_indices[i] -> slot index via (eax - bot_indices) / 4.
    a.raw(b'\x2D' + le32(bot_indices_va))                 # sub eax, bot_indices
    a.raw(b'\xC1\xE8\x02')                                # shr eax, 2
    a.raw(b'\xA3' + le32(bot_slot_tmp_va))                # mov [bot_slot_tmp], eax

    # --- Validate args.
    a.raw(b'\x8B\x44\x24\x04')                            # mov eax, [esp+4] (bot char)
    a.raw(b'\x85\xC0'); a.jz('s5436f0_no_fire')
    a.raw(b'\xA3' + le32(bot_char_tmp_va))                # mov [bot_char_tmp], eax
    a.raw(b'\x8B\x54\x24\x08')                            # mov edx, [esp+8] (out_angle)
    a.raw(b'\x85\xD2'); a.jz('s5436f0_no_fire')

    # --- Bot's own position into bot_pos.
    a.raw(b'\x68' + le32(bot_pos_va))                     # push bot_pos
    a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))            # mov ecx, [bot_char_tmp]
    a.call_va(ax.SUB_4FB0A0_VA)                           # __thiscall, ret 4

    # --- Cache bot's team (CTF only); -1 sentinel disables filter.
    a.raw(b'\xC7\x05' + le32(our_team_tmp_va) + le32(0xFFFFFFFF))
    a.raw(b'\x83\x3D' + le32(menu_mode_va) + b'\x01')     # cmp [menu_mode], 1 (CTF)
    a.jnz('s5436f0_team_done')
    a.raw(b'\x8B\x15' + le32(bot_slot_tmp_va))            # mov edx, [bot_slot_tmp]
    a.raw(b'\x8B\x04\x95' + le32(bot_team_va))            # mov eax, [bot_team+edx*4]
    a.raw(b'\xA3' + le32(our_team_tmp_va))                # mov [our_team_tmp], eax
    a.label('s5436f0_team_done')

    # --- Init loop state.
    a.raw(b'\xC7\x05' + le32(best_target_va) + le32(0))
    a.raw(b'\xA1' + le32(fire_range_sq_va))
    a.raw(b'\xA3' + le32(best_dist_sq_va))
    a.raw(b'\xC7\x05' + le32(cand_idx_va) + le32(0))

    # --- Candidate scan.
    a.label('s5436f0_scan_top')
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))           # mov eax, [mgr]
    a.raw(b'\x85\xC0'); a.jz('s5436f0_after_loop')
    a.raw(b'\x8B\x80\x90\x02\x00\x00')                    # mov eax, [eax+0x290]
    a.raw(b'\x85\xC0'); a.jz('s5436f0_after_loop')
    a.raw(b'\x8B\x0D' + le32(cand_idx_va))                # mov ecx, [cand_idx]
    a.raw(b'\x8B\x0C\x88')                                # mov ecx, [eax+ecx*4]
    a.raw(b'\x85\xC9'); a.jz('s5436f0_next')              # NULL slot
    a.raw(b'\x3B\x0D' + le32(bot_char_tmp_va))            # cmp ecx, [bot_char_tmp]
    a.jz('s5436f0_next')                                  # skip self
    a.raw(b'\x89\x0D' + le32(cand_tmp_va))                # mov [cand_tmp], ecx

    # --- CTF teammate filter (no engine call; uses scratch caches only).
    a.raw(b'\x83\x3D' + le32(our_team_tmp_va) + b'\xFF')  # cmp [our_team_tmp], -1
    a.jz('s5436f0_skip_team_check')
    # Determine candidate's team:
    #   cand_idx == 0          -> host_team
    #   bot_indices[i]==cand_idx -> bot_team[i]
    #   else                    -> unknown (don't filter)
    # Load cand_idx once; reused as the loop key for the bot_indices scan.
    # Using bot_indices instead of bot_chars survives natural respawns:
    # detour_542550 re-captures the controller on respawn, but bot_chars[]
    # still points at the previous (freed) char.
    a.raw(b'\x8B\x15' + le32(cand_idx_va))                # mov edx, [cand_idx]
    a.raw(b'\x85\xD2')                                    # test edx, edx
    a.jnz('s5436f0_cand_team_bot_scan')
    # Host team — refresh live from `*(host_part+0x14)` so a mid-match team
    # switch (e.g. CTF blue→red via the F1 menu) takes effect immediately
    # without needing to re-spawn bots. Fall back to the cached sentinel if
    # host_part isn't captured yet.
    a.raw(b'\xA1' + le32(host_part_va))                   # mov eax, [host_part]
    a.raw(b'\x85\xC0')                                    # test eax, eax
    a.jz('s5436f0_host_team_fallback')
    a.raw(b'\x8B\x40\x14')                                # mov eax, [host_part+0x14]
    a.raw(b'\xA3' + le32(host_team_va))                   # mov [host_team], eax (cache for next time)
    a.jmp('s5436f0_cand_team_check')
    a.label('s5436f0_host_team_fallback')
    a.raw(b'\xA1' + le32(host_team_va))                   # mov eax, [host_team]
    a.jmp('s5436f0_cand_team_check')

    a.label('s5436f0_cand_team_bot_scan')
    a.raw(b'\xB8' + le32(bot_indices_va))                 # mov eax, bot_indices
    a.label('s5436f0_bot_scan_loop')
    a.raw(b'\x3B\x10')                                    # cmp edx, [eax]
    a.jz('s5436f0_bot_scan_hit')
    a.raw(b'\x83\xC0\x04')                                # add eax, 4
    a.raw(b'\x3D' + le32(bot_indices_va + 4 * cfg.MAX_BOT_SLOTS))  # cmp eax, bot_indices_end
    a.jb('s5436f0_bot_scan_loop')
    # No match: candidate is unknown (likely PC2 human). Skip team check.
    a.jmp('s5436f0_skip_team_check')

    a.label('s5436f0_bot_scan_hit')
    # eax = &bot_indices[i]; convert to &bot_team[i] = bot_team + (eax-bot_indices).
    a.raw(b'\x2D' + le32(bot_indices_va))                 # sub eax, bot_indices
    a.raw(b'\x8B\x80' + le32(bot_team_va))                # mov eax, [eax+bot_team]
    # fallthrough to s5436f0_cand_team_check with eax = cand team

    a.label('s5436f0_cand_team_check')
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s5436f0_skip_team_check')                       # unknown -> don't filter
    a.raw(b'\x3B\x05' + le32(our_team_tmp_va))            # cmp eax, [our_team_tmp]
    a.jz('s5436f0_next')                                  # same team -> skip
    a.label('s5436f0_skip_team_check')

    # --- Distance gate. ECX is still cand on every path reaching here
    # (the team-filter side branches only touch EAX/EDX).
    a.raw(b'\x68' + le32(cand_pos_va))                    # push &cand_pos
    a.call_va(ax.SUB_4FB0A0_VA)                           # __thiscall, ret 4

    a.raw(b'\xD9\x05' + le32(cand_pos_va))                # fld [cand.x]
    a.raw(b'\xD8\x25' + le32(bot_pos_va))                 # fsub [bot.x]
    a.raw(b'\xD9\x15' + le32(bot_dx_va))                  # fst [bot_dx]
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xD9\x05' + le32(cand_pos_va + 4))            # fld [cand.y]
    a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))             # fsub [bot.y]
    a.raw(b'\xD9\x15' + le32(bot_dy_va))                  # fst [bot_dy]
    a.raw(b'\xD8\xC8')                                    # fmul st,st
    a.raw(b'\xDE\xC1')                                    # faddp           -> ST0 = d²
    a.raw(b'\xD9\x15' + le32(curr_dist_sq_va))            # fst [curr_dist_sq]
    a.raw(b'\xD8\x1D' + le32(best_dist_sq_va))            # FCOMP m32 (compare and pop)
    a.raw(b'\xDF\xE0')                                    # fnstsw ax
    a.raw(b'\x9E')                                        # sahf
    a.jae('s5436f0_next')                                 # d² >= best -> skip

    # --- LOS gate: sub_491380(this=bot, target=cand, 0, NULL, 2, NULL).
    a.raw(b'\x6A\x00')                                    # push 0   (a6)
    a.raw(b'\x6A\x02')                                    # push 2   (a5)
    a.raw(b'\x6A\x00')                                    # push 0   (a4)
    a.raw(b'\x6A\x00')                                    # push 0   (a3)
    a.raw(b'\xFF\x35' + le32(cand_tmp_va))                # push [cand_tmp] (a2)
    a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))            # mov ecx, [bot_char_tmp]
    a.call_va(ax.SUB_491380_VA)                           # __thiscall, ret 14h
    a.raw(b'\x84\xC0'); a.jz('s5436f0_next')

    # --- New best — record target, distance², and aim deltas.
    a.raw(b'\xA1' + le32(cand_tmp_va))
    a.raw(b'\xA3' + le32(best_target_va))
    a.raw(b'\xA1' + le32(curr_dist_sq_va))
    a.raw(b'\xA3' + le32(best_dist_sq_va))
    a.raw(b'\xA1' + le32(bot_dx_va))
    a.raw(b'\xA3' + le32(best_dx_va))
    a.raw(b'\xA1' + le32(bot_dy_va))
    a.raw(b'\xA3' + le32(best_dy_va))

    a.label('s5436f0_next')
    a.raw(b'\xFF\x05' + le32(cand_idx_va))                # inc dword [cand_idx]
    a.raw(b'\x83\x3D' + le32(cand_idx_va) + b'\x10')      # cmp [cand_idx], 16
    a.jb('s5436f0_scan_top')

    a.label('s5436f0_after_loop')
    a.raw(b'\x83\x3D' + le32(best_target_va) + b'\x00')   # cmp [best_target], 0
    a.jz('s5436f0_no_fire')

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
