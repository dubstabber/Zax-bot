"""CTF capture score guard (last-resort backstop).

``CGiveTeamAPointAction::execute`` awards the capture point at a CTF base. The
detour suppresses the award while the scoring team's own flag is away — first
by the event-driven ``flag_present[]`` (kept in lockstep with the map script's
checker activation by ``detours/flag_events.py``), then by a live inventory
sweep that catches a carried flag even if an event was somehow missed.

With the checker force-tick correctly gated on ``flag_present[]`` the capture
chain can no longer fire while the flag is away, so this guard should never
trigger; it stays as defence in depth because a wrongful chain also consumes
the carried flag and respawns it at its base, which the score guard cannot
undo — only the point itself is protected here.

HISTORY: an earlier companion guard wrapped ``CUseInventoryItemAction::execute``
(``sub_5B3100``) to stop the flag-consume step of the capture chain too. It was
REMOVED: the shared drop-on-death canned script ("Does player have a flag")
consumes the dying carrier's flag through the very same action, so the guard
could not tell a capture consume from a drop consume and wrongly blocked flag
drops whenever both flags were out — one of the state-corruption sources behind
bot-match CTF anomalies. Do not reinstate it at that site.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


LOW_PTR = 0x00100000
HIGH_PTR = 0x70000000
MAX_SCORE_SCAN_CHARS = 32


def _emit_state_reset(a: Asm, block_va: int, team_va: int, target_def_va: int,
                      gid_va: int, inv_va: int) -> None:
    # Default allow. The scratch values are deliberately diagnostic-friendly:
    # team/target_def stay -1 unless the current action is a CTF capture path.
    a.raw(b'\xC7\x05' + le32(block_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(team_va) + le32(0xFFFFFFFF))
    a.raw(b'\xC7\x05' + le32(target_def_va) + le32(0xFFFFFFFF))
    a.raw(b'\xC7\x05' + le32(gid_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(inv_va) + le32(0))


def _emit_resolve_blue_flag(a: Asm) -> None:
    a.raw(b'\x6A\xFF')                                       # push -1
    a.raw(b'\x68' + le32(ax.BLUE_FLAG_STR_VA))               # push "Blue Flag"
    a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))           # item-definition registry
    a.call_va(ax.SUB_523DF0_VA)


def _emit_resolve_red_flag(a: Asm) -> None:
    a.raw(b'\x6A\xFF')                                       # push -1
    a.raw(b'\x68' + le32(ax.RED_FLAG_STR_VA))                # push "Red Flag"
    a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))           # item-definition registry
    a.call_va(ax.SUB_523DF0_VA)


def _emit_fallback(a: Asm) -> None:
    a.label('detour_5A9960')
    a.raw(ax.S5A9960_PROLOGUE)
    a.jmp_va(ax.S5A9960_RESUME)


def emit(a: Asm, layout: ScratchLayout) -> None:
    needed = (
        'ctf_score_block', 'ctf_score_team', 'ctf_score_target_def',
        'ctf_score_gid', 'ctf_score_inv',
    )
    if not all(layout.has_field(f) for f in needed):
        _emit_fallback(a)
        return

    block_va = layout.va('ctf_score_block')
    team_va = layout.va('ctf_score_team')
    target_def_va = layout.va('ctf_score_target_def')
    gid_va = layout.va('ctf_score_gid')
    inv_va = layout.va('ctf_score_inv')

    flag_present_enabled = (
        layout.has_field('flag_count')
        and layout.has_field('flag_team')
        and layout.has_field('flag_present')
    )
    flag_count_va = layout.va('flag_count') if flag_present_enabled else 0
    flag_team_va = layout.va('flag_team') if flag_present_enabled else 0
    flag_present_va = layout.va('flag_present') if flag_present_enabled else 0

    # ------------------------------------------------------------------
    # CGiveTeamAPointAction::execute. This remains the hard fallback in case
    # a score action is reached without the normal flag-use action.
    # ------------------------------------------------------------------
    a.label('detour_5A9960')
    a.raw(b'\x60')                                            # pushad
    _emit_state_reset(a, block_va, team_va, target_def_va, gid_va, inv_va)

    # Validate action pointer and team field. Detour site guarantees this is a
    # CGiveTeamAPointAction, but the range checks keep the first deref boring.
    a.raw(b'\x85\xC9'); a.jz('csg_score_done')               # action NULL?
    a.raw(b'\x81\xF9' + le32(ax.IMAGE_BASE)); a.jb('csg_score_done')
    a.raw(b'\x81\xF9' + le32(HIGH_PTR)); a.jae('csg_score_done')
    a.raw(b'\x8B\x41\x08')                                   # eax = action->Team Number
    a.raw(b'\x83\xF8\x01')                                   # only teams 0/1 are CTF
    a.ja('csg_score_done')
    a.raw(b'\xA3' + le32(team_va))                           # ctf_score_team = eax

    # Resolve score recipient's OWN flag definition id:
    #   team 0 Blue -> "Blue Flag"; team 1 Red -> "Red Flag".
    a.raw(b'\x85\xC0')                                       # team == 0?
    a.jz('csg_score_resolve_blue')
    _emit_resolve_red_flag(a)
    a.jmp('csg_score_store_target_def')
    a.label('csg_score_resolve_blue')
    _emit_resolve_blue_flag(a)

    a.label('csg_score_store_target_def')
    a.raw(b'\xA3' + le32(target_def_va))                     # target flag def id
    a.raw(b'\x83\xF8\xFF'); a.jz('csg_score_done')           # lookup failed
    a.call_lbl('csg_check_current_team')

    a.label('csg_score_done')
    a.raw(b'\x61')                                           # popad
    a.raw(b'\x83\x3D' + le32(block_va) + b'\x00')            # block?
    a.jnz('csg_score_block')

    # Allow: replay displaced prologue and continue at the original call.
    a.raw(ax.S5A9960_PROLOGUE)
    a.jmp_va(ax.S5A9960_RESUME)

    a.label('csg_score_block')
    a.raw(b'\xB0\x01')                                       # mov al, 1
    a.raw(b'\xC2\x10\x00')                                   # ret 0x10

    # ------------------------------------------------------------------
    # Shared check. Inputs:
    #   ctf_score_team       = scoring team (0 Blue, 1 Red)
    #   ctf_score_target_def = scoring team's own flag item-definition id
    # Output:
    #   ctf_score_block      = 1 if the capture must be suppressed
    # Clobbers registers. Callers wrap this in pushad/popad.
    # ------------------------------------------------------------------
    a.label('csg_check_current_team')
    a.raw(b'\xC7\x05' + le32(block_va) + le32(0))
    a.raw(b'\x83\x3D' + le32(team_va) + b'\x01')             # team 0/1 only
    a.ja('csg_check_done')
    a.raw(b'\x83\x3D' + le32(target_def_va) + b'\xFF')
    a.jz('csg_check_done')

    # flag_present[] is event-driven (the checker activate/deactivate apply
    # detours in flag_events.py), so it is the authoritative home/away answer
    # with no scan staleness. The inventory sweep below stays as a fallback
    # for a hypothetical missed event: a carried flag always implies "away".
    if flag_present_enabled:
        a.raw(b'\x8B\x1D' + le32(flag_count_va))             # ebx = flag_count
        a.raw(b'\x83\xFB' + bytes([cfg.FLAG_TABLE_MAX]))
        a.jbe('csg_fp_count_ok')
        a.raw(b'\xBB' + le32(cfg.FLAG_TABLE_MAX))            # cap count
        a.label('csg_fp_count_ok')
        a.raw(b'\x31\xF6')                                   # esi = i
        a.label('csg_fp_loop')
        a.raw(b'\x39\xDE'); a.jae('csg_fp_done')             # i >= count
        a.raw(b'\x8B\x04\xB5' + le32(flag_team_va))          # eax = flag_team[i]
        a.raw(b'\x3B\x05' + le32(team_va))                   # cmp eax, team
        a.jnz('csg_fp_next')
        a.raw(b'\x83\x3C\xB5' + le32(flag_present_va) + b'\x00')
        a.jnz('csg_fp_done')                                 # own flag is home
        a.raw(b'\xC7\x05' + le32(block_va) + le32(1))
        a.jmp('csg_check_done')
        a.label('csg_fp_next')
        a.raw(b'\x46')                                       # ++i
        a.jmp('csg_fp_loop')
        a.label('csg_fp_done')

    # Resolve Multiplayer Flag group id if the scoreboard has not warmed it.
    a.raw(b'\xA1' + le32(ax.MULTIPLAYER_FLAG_GID_VA))         # eax = cached gid
    a.raw(b'\x85\xC0'); a.jnz('csg_have_gid')
    a.raw(b'\x6A\xFF')                                       # push -1
    a.raw(b'\x68' + le32(ax.MULTIPLAYER_FLAG_STR_VA))        # push "Multiplayer Flag"
    a.raw(b'\xB9' + le32(ax.SLOT_NAME_REGISTRY_VA))          # registry
    a.call_va(ax.SUB_591FC0_VA)
    a.raw(b'\xA3' + le32(ax.MULTIPLAYER_FLAG_GID_VA))
    a.raw(b'\x80\x0D' + le32(ax.MULTIPLAYER_FLAG_GID_READY_VA) + b'\x01')
    a.label('csg_have_gid')
    a.raw(b'\xA3' + le32(gid_va))                            # ctf_score_gid = eax
    a.raw(b'\x85\xC0'); a.jz('csg_check_done')
    a.raw(b'\x83\xF8\xFF'); a.jz('csg_check_done')

    # Character array: mgr+0x290, count mgr+0x294.
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))              # eax = mgr
    a.raw(b'\x85\xC0'); a.jz('csg_check_done')
    a.raw(b'\x3D' + le32(ax.IMAGE_BASE)); a.jb('csg_check_done')
    a.raw(b'\x3D' + le32(HIGH_PTR)); a.jae('csg_check_done')
    a.raw(b'\x8B\x98\x94\x02\x00\x00')                       # ebx = [mgr+0x294]
    a.raw(b'\x8B\xB8\x90\x02\x00\x00')                       # edi = [mgr+0x290]
    a.raw(b'\x85\xFF'); a.jz('csg_check_done')
    a.raw(b'\x81\xFF' + le32(LOW_PTR)); a.jb('csg_check_done')
    a.raw(b'\x81\xFF' + le32(HIGH_PTR)); a.jae('csg_check_done')
    a.raw(b'\x83\xFB' + bytes([MAX_SCORE_SCAN_CHARS]))
    a.jbe('csg_count_ok')
    a.raw(b'\xBB' + le32(MAX_SCORE_SCAN_CHARS))
    a.label('csg_count_ok')
    a.raw(b'\x31\xF6')                                       # esi = char index

    a.label('csg_char_loop')
    a.raw(b'\x39\xDE'); a.jae('csg_check_done')              # i >= count
    a.raw(b'\x8B\x0C\xB7')                                   # ecx = [edi + esi*4]
    a.raw(b'\x85\xC9'); a.jz('csg_char_next')
    a.raw(b'\x81\xF9' + le32(LOW_PTR)); a.jb('csg_char_next')
    a.raw(b'\x81\xF9' + le32(HIGH_PTR)); a.jae('csg_char_next')

    # inv = sub_4267E0(char)
    a.call_va(ax.SUB_4267E0_VA)
    a.raw(b'\x85\xC0'); a.jz('csg_char_next')
    a.raw(b'\x3D' + le32(LOW_PTR)); a.jb('csg_char_next')
    a.raw(b'\x3D' + le32(HIGH_PTR)); a.jae('csg_char_next')
    a.raw(b'\xA3' + le32(inv_va))                            # ctf_score_inv = inv

    # item_id = sub_425290(inv, Multiplayer Flag gid)
    a.raw(b'\xFF\x35' + le32(gid_va))                        # push gid
    a.raw(b'\x8B\x0D' + le32(inv_va))                        # ecx = inv
    a.call_va(ax.SUB_425290_VA)
    a.raw(b'\x83\xF8\xFF'); a.jz('csg_char_next')

    # item = inv.vtable[+0x68](inv, item_id)
    a.raw(b'\x50')                                           # push item_id
    a.raw(b'\x8B\x0D' + le32(inv_va))                        # ecx = inv
    a.raw(b'\x8B\x11')                                       # edx = [inv] (vtable)
    a.raw(b'\xFF\x52' + bytes([ax.INVENTORY_GET_WEAPON_OFF]))
    a.raw(b'\x85\xC0'); a.jz('csg_char_next')
    a.raw(b'\x3D' + le32(LOW_PTR)); a.jb('csg_char_next')
    a.raw(b'\x3D' + le32(HIGH_PTR)); a.jae('csg_char_next')

    # CInventoryItem stores its item-definition id at +8. Compare the raw
    # item+8 id to the Red/Blue flag id resolved above.
    a.raw(b'\x8B\x40\x08')                                   # eax = item->definition id
    a.raw(b'\x3B\x05' + le32(target_def_va))                 # cmp eax, target_def
    a.jnz('csg_char_next')
    a.raw(b'\xC7\x05' + le32(block_va) + le32(1))
    a.jmp('csg_check_done')

    a.label('csg_char_next')
    a.raw(b'\x46')                                           # ++i
    a.jmp('csg_char_loop')

    a.label('csg_check_done')
    a.raw(b'\xC3')                                           # ret
