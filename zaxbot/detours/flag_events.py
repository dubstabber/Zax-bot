"""``detour_4C29F0`` / ``detour_4C2D60`` — event-driven CTF flag-home tracking.

Vanilla Zax enforces "your own flag must be home to capture" through the map
script, not engine state: every CTF map authors a hidden ``Red Checker`` /
``Blue Checker`` touch trigger exactly on the flag spawn anchor, and the shared
canned scripts drive its activation:

* steal   ("Picked up a Flag", enemy branch)   -> ``CDeactivateAction`` checker
* return  ("Picked up a Flag", own-team branch) -> ``CActivateAction`` checker
* capture ("Returned a Flag", success branch)   -> ``CActivateAction`` on the
  OTHER team's checker (its flag was just recreated at its spawn)

A deactivated checker is never ticked by the engine, so its capture Enter
Action simply cannot fire while the team's flag is away or dropped. That makes
the checker (de)activation events the exact, complete transition set for
``flag_present[]`` — there is no engine-side auto-return (the exe contains no
reference to the spawn-point names), so a dropped flag lies on the ground until
a script event moves it.

Both action executes funnel through the generic by-name target resolver
(``sub_41AED0``), which invokes the class's PER-ENTITY apply (vtable slot 27)
once per resolved entity:

* ``sub_4C29F0`` — CActivateAction apply: sets entity Active bit (+0x1C, 0x800000)
* ``sub_4C2D60`` — CDeactivateAction apply: clears it

Each apply is reachable only through its own vtable and receives the RESOLVED
target entity at ``[esp+0x10]`` (``ret 0x10``). The detours match that entity's
raw ``+0x4C/+0x50`` position against the ``flag_table`` anchors — the checker is
the only (de)activation target sitting on a flag anchor — and write
``flag_present[i] = 1`` (activate) or ``0`` (deactivate). This replaced the old
scan-derived presence heuristics (anchor entity pair count, carried-inventory
subtraction, dropped-item grid match): the world flag is a plain renamed
``CEntityAnimated`` with no inventory identity, so those scans never saw a
DROPPED flag and left ``flag_present`` stuck at 1 — which let the far-base
force-tick re-arm a script-deactivated checker and hand out captures the
vanilla rules forbid.

Cost: the applies fire on script events only (never per frame); the body is a
bounded float-compare loop over ``flag_count`` (<= FLAG_TABLE_MAX) entries.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def _emit_passthrough(a: Asm) -> None:
    a.label('detour_4C29F0')
    a.raw(ax.S4C29F0_PROLOGUE)
    a.jmp_va(ax.S4C29F0_RESUME)
    a.label('detour_4C2D60')
    a.raw(ax.S4C2D60_PROLOGUE)
    a.jmp_va(ax.S4C2D60_RESUME)


def emit(a: Asm, layout: ScratchLayout) -> None:
    needed = (
        'flag_count', 'flag_table', 'flag_present', 'flag_evt_present',
        'flag_entity_match_radius_sq',
    )
    if not all(layout.has_field(f) for f in needed):
        _emit_passthrough(a)
        return

    flag_count_va   = layout.va('flag_count')
    flag_table_va   = layout.va('flag_table')
    flag_present_va = layout.va('flag_present')
    evt_present_va  = layout.va('flag_evt_present')
    radius_va       = layout.va('flag_entity_match_radius_sq')

    # ------------------------------------------------------------------
    # CActivateAction per-entity apply: the target entity is being armed.
    # If it sits on a flag anchor it is that base's checker -> flag home.
    # ------------------------------------------------------------------
    a.label('detour_4C29F0')
    a.raw(b'\x60')                                           # pushad
    a.raw(b'\x8B\x7C\x24\x30')                               # edi = [esp+0x30] (entity)
    a.raw(b'\xC7\x05' + le32(evt_present_va) + le32(1))      # flag_evt_present = 1
    a.call_lbl('flag_event_update')
    a.raw(b'\x61')                                           # popad
    a.raw(ax.S4C29F0_PROLOGUE)
    a.jmp_va(ax.S4C29F0_RESUME)

    # ------------------------------------------------------------------
    # CDeactivateAction per-entity apply: checker disarmed -> flag away.
    # The displaced prologue's `test ecx, ecx` feeds the jz at RESUME, and
    # a jmp does not modify flags, so replay-then-jump preserves semantics.
    # ------------------------------------------------------------------
    a.label('detour_4C2D60')
    a.raw(b'\x60')                                           # pushad
    a.raw(b'\x8B\x7C\x24\x30')                               # edi = [esp+0x30] (entity)
    a.raw(b'\xC7\x05' + le32(evt_present_va) + le32(0))      # flag_evt_present = 0
    a.call_lbl('flag_event_update')
    a.raw(b'\x61')                                           # popad
    a.raw(ax.S4C2D60_PROLOGUE)
    a.jmp_va(ax.S4C2D60_RESUME)

    # ------------------------------------------------------------------
    # flag_event_update: EDI = (de)activated entity. For every flag_table
    # anchor within flag_entity_match_radius_sq of the entity's raw
    # +0x4C/+0x50 position, flag_present[i] = flag_evt_present. Runs inside
    # the callers' pushad frame; clobbers EAX/EBX/ESI and x87 top.
    # ------------------------------------------------------------------
    a.label('flag_event_update')
    a.raw(b'\x85\xFF'); a.jz('feu_ret')                      # NULL entity
    a.raw(b'\x81\xFF\x00\x00\x40\x00'); a.jb('feu_ret')      # below image/heap range
    a.raw(b'\x81\xFF\x00\x00\x00\x70'); a.jae('feu_ret')     # above heap range
    a.raw(b'\x8B\x1D' + le32(flag_count_va))                 # ebx = flag_count
    a.raw(b'\x83\xFB' + bytes([cfg.FLAG_TABLE_MAX]))
    a.jbe('feu_cnt_ok')
    a.raw(b'\xBB' + le32(cfg.FLAG_TABLE_MAX))                # cap corrupt count
    a.label('feu_cnt_ok')
    a.raw(b'\x31\xF6')                                       # esi = 0 (flag i)

    a.label('feu_loop')
    a.raw(b'\x39\xDE'); a.jae('feu_ret')                     # i >= count
    # d2 = (flag[i].x - ent.raw_x)^2 + (flag[i].y - ent.raw_y)^2. Raw entity
    # coords, matching the anchor cache in scan_portal_active: the checker is
    # authored exactly on the spawn anchor.
    a.raw(b'\xD9\x04\xF5' + le32(flag_table_va))             # fld [flag_table + esi*8]
    a.raw(b'\xD8\x67\x4C')                                   # fsub [edi+0x4C]
    a.raw(b'\xD8\xC8')                                       # fmul st,st
    a.raw(b'\xD9\x04\xF5' + le32(flag_table_va + 4))         # fld [flag_table + esi*8 + 4]
    a.raw(b'\xD8\x67\x50')                                   # fsub [edi+0x50]
    a.raw(b'\xD8\xC8')                                       # fmul st,st
    a.raw(b'\xDE\xC1')                                       # faddp -> st0 = d2
    a.raw(b'\xD8\x1D' + le32(radius_va))                     # fcomp [match_radius] (pops)
    a.raw(b'\xDF\xE0\x9E')                                   # fnstsw ax; sahf
    a.ja('feu_next')                                         # d2 > radius -> not this anchor
    a.raw(b'\xA1' + le32(evt_present_va))                    # eax = 0/1
    a.raw(b'\x89\x04\xB5' + le32(flag_present_va))           # flag_present[i] = eax
    a.label('feu_next')
    a.raw(b'\x46')                                           # ++i
    a.jmp('feu_loop')

    a.label('feu_ret')
    a.raw(b'\xC3')
