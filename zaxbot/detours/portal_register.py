"""``detour_4C11A0`` — teleport-portal self-registration (runtime detection).

``sub_4C11A0`` is the engine's relocate/teleport EXECUTOR: every
``CRelocateAction`` and ``CTeleportAction`` funnels its move through here (both
classes override their "execute" vtable slot with ``sub_5A5A60``, which runs the
warp then tail-calls ``sub_4C1060`` -> ``sub_4C11A0``). So detouring this one
site observes every teleport on every map, regardless of how it was triggered —
a touch trigger, a level script/event, or a CONDITIONAL portal that only starts
firing once a map objective activates it. That last case is exactly what the
static ``Data.dat`` parse in ``world_scan.py`` cannot see, and why this detour
exists: it learns active teleporters by watching them fire.

## What we read

At ``sub_4C11A0`` entry (``__thiscall``):

* ``ecx`` = the action object. ``[ecx]`` is its primary vtable (single
  inheritance, never this-adjusted): ``CSwitchMapAction`` 0x6032C4,
  ``CRelocateAction`` 0x603338, or ``CTeleportAction`` 0x6033B0. We filter to
  ``CTeleportAction`` — the genuine warp teleporter (Warp Behavior +
  Teleporter.wav) — so plain ``$return``/non-warp relocations aren't logged.
* ``[esp+4]`` = ``a2`` = the entity being teleported, STILL at its source
  position (the relocate itself happens later in the body via ``sub_4F4AC0``).
  ``sub_4FB0A0(entity, &out)`` therefore yields the portal pad's world coords.

## Stack discipline

A ``pushad`` / ``pushfd`` pair at entry preserves every register and flag for
the engine code we resume into. After the work we ``popfd`` / ``popad`` to
restore the exact entry state, then re-execute the displaced 7-byte prologue
(``mov eax,[esp+8]; sub esp,0xC``) and jump to RESUME — so ``eax``/``esp`` match
what ``0x4C11A7`` expects. ``a2`` is read at ``[esp+0x28]`` (the entry ``[esp+4]``
plus the 0x24 the ``pushad``/``pushfd`` pushed).

## Output

Appends the deduped ``(x, y)`` pad to ``portal_table`` and bumps
``portal_count`` — the same table the overlay draws and ``load_portals`` seeds
from static map data on match change. Unlike pickups (per-frame rebuilt), the
portal table ACCUMULATES across the match: a pad seen once stays known, so a new
pad within ``PORTAL_DEDUP_RADIUS_SQ`` of any existing entry is skipped.
"""

import struct

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    # Passthrough when this build has no portal table (defensive — the
    # production build always allocates it; mirrors load_portals' guard).
    if not (
        layout.has_field('portal_count')
        and layout.has_field('portal_table')
        and layout.has_field('portal_reg_tmp')
    ):
        a.label('detour_4C11A0')
        a.raw(ax.S4C11A0_PROLOGUE)                            # re-exec displaced prologue
        a.jmp_va(ax.S4C11A0_RESUME)
        return

    count_va  = layout.va('portal_count')
    table_va  = layout.va('portal_table')
    tmp_va    = layout.va('portal_reg_tmp')
    table_max = cfg.PORTAL_TABLE_MAX

    a.label('detour_4C11A0')
    # Preserve full entry state; ecx (action) / edx / ebx / esi / edi / ebp all
    # survive, and a2 stays readable off the saved stack frame.
    a.raw(b'\x60')                                            # pushad
    a.raw(b'\x9C')                                            # pushfd

    # Filter: action vtable == CTeleportAction. ecx is unchanged by pushad/pushfd.
    a.raw(b'\x85\xC9'); a.jz('pt_restore')                    # test ecx, ecx
    a.raw(b'\x81\xF9\x00\x00\x40\x00'); a.jb('pt_restore')    # cmp ecx, 0x00400000
    a.raw(b'\x81\xF9\x00\x00\x00\x70'); a.jae('pt_restore')   # cmp ecx, 0x70000000
    a.raw(b'\x8B\x01')                                        # mov eax, [ecx] (action vtable)
    a.raw(b'\x3D' + le32(ax.VT_TELEPORT_ACTION_VA))           # cmp eax, CTeleportAction vtable
    a.jnz('pt_restore')

    # entity = a2 = [esp+0x28] (entry [esp+4] + 0x24 for pushad(0x20)+pushfd(0x4)).
    a.raw(b'\x8B\x5C\x24\x28')                                # mov ebx, [esp+0x28]
    a.raw(b'\x85\xDB'); a.jz('pt_restore')                    # entity NULL?
    a.raw(b'\x81\xFB\x00\x00\x40\x00'); a.jb('pt_restore')    # cmp ebx, 0x00400000
    a.raw(b'\x81\xFB\x00\x00\x00\x70'); a.jae('pt_restore')   # cmp ebx, 0x70000000

    # Read the teleported entity's SOURCE position into portal_reg_tmp.
    # sub_4FB0A0(ecx=entity, &out) — __thiscall, ret 4 (pops &out). EBX is
    # callee-saved so the entity survives the call.
    a.raw(b'\x68' + le32(tmp_va))                             # push &portal_reg_tmp
    a.raw(b'\x89\xD9')                                        # mov ecx, ebx (this = entity)
    a.call_va(ax.SUB_4FB0A0_VA)

    # Dedup: if the new pad is within PORTAL_DEDUP_RADIUS_SQ of any existing
    # entry (static or runtime), it's the same pad — skip. EDI=count, ESI=idx.
    a.raw(b'\x8B\x3D' + le32(count_va))                       # edi = portal_count
    a.raw(b'\x31\xF6')                                        # esi = 0
    a.label('pt_dedup_loop')
    a.raw(b'\x39\xFE'); a.jae('pt_append')                    # esi >= count -> not a dup
    # d2 = (table[esi].x - tmp.x)^2 + (table[esi].y - tmp.y)^2
    a.raw(b'\xD9\x04\xF5' + le32(table_va))                   # fld  [table + esi*8]
    a.raw(b'\xD8\x25' + le32(tmp_va))                         # fsub [tmp.x]
    a.raw(b'\xD8\xC8')                                        # fmul st, st  -> dx^2
    a.raw(b'\xD9\x04\xF5' + le32(table_va + 4))               # fld  [table + esi*8 + 4]
    a.raw(b'\xD8\x25' + le32(tmp_va + 4))                     # fsub [tmp.y]
    a.raw(b'\xD8\xC8')                                        # fmul st, st  -> dy^2
    a.raw(b'\xDE\xC1')                                        # faddp        -> d2
    a.raw(b'\xD8\x1D'); a.imm32_lbl('portal_dedup_sq')        # fcomp [dedup_sq] (pops st0)
    a.raw(b'\xDF\xE0\x9E')                                    # fnstsw ax; sahf
    a.jb('pt_restore')                                        # d2 < dedup -> duplicate, skip
    a.raw(b'\x46')                                            # inc esi
    a.jmp('pt_dedup_loop')

    a.label('pt_append')
    a.raw(b'\xA1' + le32(count_va))                           # eax = portal_count
    a.raw(b'\x3D' + le32(table_max))                          # cmp eax, PORTAL_TABLE_MAX
    a.jae('pt_restore')                                       # table full
    a.raw(b'\x8D\x14\xC5' + le32(table_va))                   # lea edx, [eax*8 + portal_table]
    a.raw(b'\x8B\x0D' + le32(tmp_va))                         # ecx = x bits
    a.raw(b'\x89\x0A')                                        # [edx]   = x
    a.raw(b'\x8B\x0D' + le32(tmp_va + 4))                     # ecx = y bits
    a.raw(b'\x89\x4A\x04')                                    # [edx+4] = y
    a.raw(b'\xFF\x05' + le32(count_va))                       # ++portal_count

    a.label('pt_restore')
    a.raw(b'\x9D')                                            # popfd
    a.raw(b'\x61')                                            # popad
    a.raw(ax.S4C11A0_PROLOGUE)                                # re-exec mov eax,[esp+8]; sub esp,0xC
    a.jmp_va(ax.S4C11A0_RESUME)                               # resume at 0x4C11A7 (push esi)

    # Unreachable constant pool (control never falls past the jmp above).
    a.label('portal_dedup_sq')
    a.raw(struct.pack('<f', cfg.PORTAL_DEDUP_RADIUS_SQ))
