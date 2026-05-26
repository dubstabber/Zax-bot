"""``pick_target`` — bot target-selection subroutine.

Walks ``mgr+0x290`` (the world's char array) and picks the closest visible
candidate that passes the team filter, writing the result to a fixed set of
scratch fields. This is the perception half of the bot AI: it answers "who
should I be shooting at right now?" so the fire/aim detour can stay focused
on encoding the resulting aim angle and so future movement / lead-shot work
can reuse the same target without rerunning the scan.

Filtering pipeline (cheapest gates first):
  - NULL slot                         -> skip
  - candidate == self                 -> skip
  - CTF same-team filter              -> skip
  - distance² >= current best         -> skip
  - engine LOS sweep (``sub_491380``) -> skip if blocked
  - new best: record target ptr, d², dx, dy

In CTF, bot teams come from the cache populated at spawn (``bot_team[slot]``);
host team is read live each frame from ``*(host_part+0x14)`` so a mid-match
team switch (F1) takes effect immediately. PC2 humans aren't in any cache —
they're treated as enemies regardless of team.

Inputs (via scratch):
  ``bot_char_tmp`` — firing bot's char ptr (non-NULL by caller).
  ``our_team_tmp`` — firing bot's team id, or ``-1`` to disable team filter.

Outputs (via scratch):
  ``best_target``  — char ptr of selected target (``0`` if none).
  ``best_dist_sq`` — d² of selected target (only meaningful when target set).
  ``best_dx``      — world-space dx (target.x − bot.x) for atan2.
  ``best_dy``      — world-space dy (target.y − bot.y) for atan2.

Returns: ``AL = 1`` if a target was picked, ``0`` otherwise. Clobbers EAX,
ECX, EDX, ST*.
"""

from .. import addresses as ax
from ..asm import Asm, le32
from ..hook.bot_lookup import emit_scan_bot_indices
from ..hook.math_helpers import emit_dist_sq_2d, emit_fcomp_jae
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    bot_team_va        = layout.va('bot_team')
    bot_indices_va     = layout.va('bot_indices')
    host_part_va       = layout.va('host_part')
    bot_pos_va         = layout.va('bot_pos')
    bot_dx_va          = layout.va('bot_dx')
    bot_dy_va          = layout.va('bot_dy')
    fire_range_sq_va   = layout.va('fire_range_sq')
    cand_pos_va        = layout.va('cand_pos')
    cand_tmp_va        = layout.va('cand_tmp')
    curr_dist_sq_va    = layout.va('curr_dist_sq')
    best_target_va     = layout.va('best_target')
    best_dist_sq_va    = layout.va('best_dist_sq')
    best_dx_va         = layout.va('best_dx')
    best_dy_va         = layout.va('best_dy')
    cand_idx_va        = layout.va('cand_idx')
    our_team_tmp_va    = layout.va('our_team_tmp')
    bot_char_tmp_va    = layout.va('bot_char_tmp')

    a.label('pick_target')

    # --- Cache the firing bot's world position once per call.
    a.raw(b'\x68' + le32(bot_pos_va))                     # push &bot_pos
    a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))            # mov ecx, [bot_char_tmp]
    a.call_va(ax.SUB_4FB0A0_VA)                           # __thiscall, ret 4

    # --- Init loop state: no target yet, threshold = fire range².
    a.raw(b'\xC7\x05' + le32(best_target_va) + le32(0))
    a.raw(b'\xA1' + le32(fire_range_sq_va))
    a.raw(b'\xA3' + le32(best_dist_sq_va))
    a.raw(b'\xC7\x05' + le32(cand_idx_va) + le32(0))

    a.label('pick_scan_top')
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))           # mov eax, [mgr]
    a.raw(b'\x85\xC0'); a.jz('pick_after_loop')
    a.raw(b'\x8B\x80\x90\x02\x00\x00')                    # mov eax, [mgr+0x290]
    a.raw(b'\x85\xC0'); a.jz('pick_after_loop')
    a.raw(b'\x8B\x0D' + le32(cand_idx_va))                # mov ecx, [cand_idx]
    a.raw(b'\x8B\x0C\x88')                                # mov ecx, [eax+ecx*4]
    a.raw(b'\x85\xC9'); a.jz('pick_next')                 # NULL slot
    a.raw(b'\x3B\x0D' + le32(bot_char_tmp_va))            # cmp ecx, [bot_char_tmp]
    a.jz('pick_next')                                     # skip self
    a.raw(b'\x89\x0D' + le32(cand_tmp_va))                # mov [cand_tmp], ecx

    # --- CTF teammate filter. Skipped entirely when our_team_tmp == -1
    # (set by the caller for DM/SK).
    a.raw(b'\x83\x3D' + le32(our_team_tmp_va) + b'\xFF')  # cmp [our_team_tmp], -1
    a.jz('pick_skip_team_check')
    # Determine candidate's team id:
    #   cand_idx == 0            -> *(host_part+0x14)  (live)
    #   bot_indices[i]==cand_idx -> bot_team[i]        (cached at spawn)
    #   else                      -> unknown (PC2 human) — fall through, shoot
    a.raw(b'\x8B\x15' + le32(cand_idx_va))                # mov edx, [cand_idx]
    a.raw(b'\x85\xD2')                                    # test edx, edx
    a.jnz('pick_cand_team_bot_scan')
    # Host team. host_part is 0 until the first bot spawn captures it; treat
    # that as "unknown" so the team filter is bypassed.
    a.raw(b'\xA1' + le32(host_part_va))                   # mov eax, [host_part]
    a.raw(b'\x85\xC0')
    a.jz('pick_skip_team_check')
    a.raw(b'\x8B\x40\x14')                                # mov eax, [host_part+0x14]
    a.jmp('pick_cand_team_check')

    a.label('pick_cand_team_bot_scan')
    emit_scan_bot_indices(a, layout,
                          on_no_match='pick_skip_team_check',
                          label_prefix='pick_cand')
    # EAX = &bot_indices[i]; bot_team lives at the same stride one whole
    # array offset later, so [EAX + (bot_team - bot_indices)] is bot_team[i].
    a.raw(b'\x2D' + le32(bot_indices_va))                 # sub eax, bot_indices
    a.raw(b'\x8B\x80' + le32(bot_team_va))                # mov eax, [eax+bot_team]

    a.label('pick_cand_team_check')
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('pick_skip_team_check')                          # unknown -> don't filter
    a.raw(b'\x3B\x05' + le32(our_team_tmp_va))            # cmp eax, [our_team_tmp]
    a.jz('pick_next')                                     # same team -> skip
    a.label('pick_skip_team_check')

    # --- Distance² gate. ECX still holds the candidate char on every path
    # reaching here (the team-filter side branches only touch EAX/EDX).
    a.raw(b'\x68' + le32(cand_pos_va))                    # push &cand_pos
    a.call_va(ax.SUB_4FB0A0_VA)                           # __thiscall, ret 4
    emit_dist_sq_2d(a, cand_pos_va, bot_pos_va,
                    dx_out_va=bot_dx_va,
                    dy_out_va=bot_dy_va,
                    dist_sq_out_va=curr_dist_sq_va)
    emit_fcomp_jae(a, best_dist_sq_va, 'pick_next')       # d² >= best -> skip

    # --- LOS gate: sub_491380(this=bot, target=cand, 0, NULL, 2, NULL).
    a.raw(b'\x6A\x00')                                    # push 0   (a6)
    a.raw(b'\x6A\x02')                                    # push 2   (a5)
    a.raw(b'\x6A\x00')                                    # push 0   (a4)
    a.raw(b'\x6A\x00')                                    # push 0   (a3)
    a.raw(b'\xFF\x35' + le32(cand_tmp_va))                # push [cand_tmp] (a2)
    a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))            # mov ecx, [bot_char_tmp]
    a.call_va(ax.SUB_491380_VA)                           # __thiscall, ret 14h
    a.raw(b'\x84\xC0'); a.jz('pick_next')

    # --- New best — record target, distance², and aim deltas.
    a.raw(b'\xA1' + le32(cand_tmp_va))
    a.raw(b'\xA3' + le32(best_target_va))
    a.raw(b'\xA1' + le32(curr_dist_sq_va))
    a.raw(b'\xA3' + le32(best_dist_sq_va))
    a.raw(b'\xA1' + le32(bot_dx_va))
    a.raw(b'\xA3' + le32(best_dx_va))
    a.raw(b'\xA1' + le32(bot_dy_va))
    a.raw(b'\xA3' + le32(best_dy_va))

    a.label('pick_next')
    a.raw(b'\xFF\x05' + le32(cand_idx_va))                # inc dword [cand_idx]
    a.raw(b'\x83\x3D' + le32(cand_idx_va) + b'\x10')      # cmp [cand_idx], 16
    a.jb('pick_scan_top')

    a.label('pick_after_loop')
    # AL = (best_target != 0)
    a.raw(b'\x31\xC0')                                    # xor eax, eax
    a.raw(b'\x83\x3D' + le32(best_target_va) + b'\x00')   # cmp [best_target], 0
    a.raw(b'\x0F\x95\xC0')                                # setne al
    a.raw(b'\xC3')                                        # ret
