"""``scan_entities`` — the general live-world-entity enumerator.

The long-standing blocker for object detection in this patch was that there is
no flat list of world entities: ``mgr+0x290`` is players, ``mgr+0x2BC`` is the
LAYER list (count ``mgr+0x2C0`` == 1 in MP), and the engine's spatial queries
are masked. Real entities (triggers, switches, doors, flags, collectors, pads,
pickups, hazards) live one level down, inside each layer's **spatial grid**.

Decompiling the engine's own by-name finder ``sub_57A7E0`` revealed the walk
(see ``zaxbot/addresses.py`` "World entity enumeration" and the
``world-entity-enumeration`` memory):

```
mgr   = [MANAGER_GLOBAL_VA]
layer = [[mgr + 0x2BC]]                 # active CLayer (vtbl 0x5F8BAC)
rows  = [layer+0x60]; cols = [layer+0x64]; cells = [layer+0x68]
for idx in 0 .. rows*cols:              # each 16-byte cell record
    cell = cells + 16*idx
    for k in 0 .. [cell+8]:             # entities in this cell
        ent = [[cell+4] + 4*k]
        ...                             # classify + read state
```

An entity that straddles several cells is visited from each, so we de-duplicate
with the engine's own protocol: bump the global visit counter
``ENTITY_VISIT_COUNTER_VA``, stamp each entity's ``+0x2C`` with it, and skip any
entity already stamped ``>=`` the current id. This is exactly what the engine
does during a name lookup, and it's safe because the engine always bumps to a
fresh higher id before its own next lookup, so our stamps never confuse it.

``scan_entities`` reads ``scan_class_desc`` (0 = collect every entity, else a
class descriptor matched with ``sub_416790``) and writes ``(ptr, x, y, flags)``
records into ``scan_table``, bounded by ``SCAN_ENTITIES_MAX``. Position comes
from ``sub_4FB0A0``; ``flags`` is ``entity+0x1C`` (the ``Active`` bit is
``ENTITY_ACTIVE_BIT``). All loop state lives in scratch so the helper calls
(which clobber EAX/ECX/EDX) can't disturb it. The walk is bounded
(``rows*cols`` cells, 256 entities/cell, both capped) and is invoked on match
change, not per frame.

``scan_diag`` is the validation entry point: it scans with ``class_desc = 0``
(every entity) so ``scan_count`` / ``scan_table`` can be inspected to confirm
the enumerator finds the map's entities — including the teleporter pads, whose
``Active`` bit then drives portal usability.
"""

import struct

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


MAX_CHARACTER_SCAN = 32


def emit(a: Asm, layout: ScratchLayout) -> None:
    if not layout.has_field('scan_table'):
        # Layout built without the scanner fields — emit inert stubs so the
        # labels resolve but nothing runs.
        a.label('scan_entities'); a.raw(b'\xC3')
        a.label('scan_diag');     a.raw(b'\xC3')
        return

    class_desc_va = layout.va('scan_class_desc')
    count_va      = layout.va('scan_count')
    visit_va      = layout.va('scan_visit_id')
    ncells_va     = layout.va('scan_ncells')
    cells_va      = layout.va('scan_cells')
    cellidx_va    = layout.va('scan_cellidx')
    list_va       = layout.va('scan_list')
    cnt_va        = layout.va('scan_cnt')
    k_va          = layout.va('scan_k')
    cur_va        = layout.va('scan_cur_ent')
    pos_va        = layout.va('scan_tmp_pos')
    table_va      = layout.va('scan_table')
    scan_max      = cfg.SCAN_ENTITIES_MAX

    # =====================================================================
    # scan_entities: walk the layer's spatial grid, dedup, classify against
    # scan_class_desc (0 = all), collect (ptr, x, y, flags) into scan_table.
    # No args; reads scan_class_desc; writes scan_table / scan_count.
    # =====================================================================
    a.label('scan_entities')
    a.raw(b'\x60')                                          # pushad
    a.raw(b'\xC7\x05' + le32(count_va) + le32(0))           # scan_count = 0

    # mgr = [MANAGER_GLOBAL_VA]
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))             # eax = [mgr]
    a.raw(b'\x85\xC0'); a.jz('se_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('se_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('se_done')
    # layer_arr = [mgr + 0x2BC]
    a.raw(b'\x8B\x80' + le32(ax.MGR_LAYER_ARRAY_OFF))       # mov eax, [eax+0x2BC]
    a.raw(b'\x85\xC0'); a.jz('se_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('se_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('se_done')
    # layer = [layer_arr]
    a.raw(b'\x8B\x00')                                      # mov eax, [eax]
    a.raw(b'\x85\xC0'); a.jz('se_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('se_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('se_done')
    # ncells = rows * cols (capped at 4096)
    a.raw(b'\x8B\x88' + le32(ax.LAYER_GRID_ROWS_OFF))       # mov ecx, [eax+0x60]
    a.raw(b'\x8B\x90' + le32(ax.LAYER_GRID_COLS_OFF))       # mov edx, [eax+0x64]
    a.raw(b'\x0F\xAF\xCA')                                  # imul ecx, edx
    a.raw(b'\x81\xF9\x00\x10\x00\x00')                      # cmp ecx, 4096
    a.jbe('se_nc_ok')
    a.raw(b'\xB9\x00\x10\x00\x00')                          # mov ecx, 4096
    a.label('se_nc_ok')
    a.raw(b'\x89\x0D' + le32(ncells_va))                    # mov [scan_ncells], ecx
    # cells = [layer + 0x68]
    a.raw(b'\x8B\x80' + le32(ax.LAYER_GRID_CELLS_OFF))      # mov eax, [eax+0x68]
    a.raw(b'\x85\xC0'); a.jz('se_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('se_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('se_done')
    a.raw(b'\xA3' + le32(cells_va))                         # mov [scan_cells], eax

    # visit id: ++dword_622200 (wrap at 0xEFFFFFFF -> 1), mirror it into scan_visit_id
    a.raw(b'\xA1' + le32(ax.ENTITY_VISIT_COUNTER_VA))       # eax = [counter]
    a.raw(b'\x40')                                          # inc eax
    a.raw(b'\x3D\xFF\xFF\xFF\xEF')                          # cmp eax, 0xEFFFFFFF
    a.jbe('se_vid_ok')
    a.raw(b'\xB8\x01\x00\x00\x00')                          # mov eax, 1
    a.label('se_vid_ok')
    a.raw(b'\xA3' + le32(ax.ENTITY_VISIT_COUNTER_VA))       # [counter] = eax
    a.raw(b'\xA3' + le32(visit_va))                         # [scan_visit_id] = eax

    a.raw(b'\xC7\x05' + le32(cellidx_va) + le32(0))         # cellidx = 0

    a.label('se_cell_loop')
    a.raw(b'\xA1' + le32(cellidx_va))                       # eax = cellidx
    a.raw(b'\x3B\x05' + le32(ncells_va))                    # cmp eax, [ncells]
    a.jae('se_done')
    # cell = cells + 16*idx
    a.raw(b'\x89\xC2')                                      # mov edx, eax
    a.raw(b'\xC1\xE2\x04')                                  # shl edx, 4
    a.raw(b'\x03\x15' + le32(cells_va))                     # add edx, [scan_cells]
    # list = [cell+4]
    a.raw(b'\x8B\x42\x04')                                  # mov eax, [edx+4]
    a.raw(b'\xA3' + le32(list_va))                          # mov [scan_list], eax
    a.raw(b'\x85\xC0'); a.jz('se_cell_next')
    # cnt = [cell+8] (capped at 256)
    a.raw(b'\x8B\x42\x08')                                  # mov eax, [edx+8]
    a.raw(b'\x3D\x00\x01\x00\x00')                          # cmp eax, 256
    a.jbe('se_cnt_ok')
    a.raw(b'\xB8\x00\x01\x00\x00')                          # mov eax, 256
    a.label('se_cnt_ok')
    a.raw(b'\xA3' + le32(cnt_va))                           # mov [scan_cnt], eax
    a.raw(b'\x85\xC0'); a.jz('se_cell_next')
    a.raw(b'\xC7\x05' + le32(k_va) + le32(0))               # k = 0

    a.label('se_ent_loop')
    a.raw(b'\xA1' + le32(k_va))                             # eax = k
    a.raw(b'\x3B\x05' + le32(cnt_va))                       # cmp eax, [cnt]
    a.jae('se_cell_next')
    # ent = list[k]
    a.raw(b'\x8B\x0D' + le32(list_va))                      # mov ecx, [scan_list]
    a.raw(b'\x8B\x04\x81')                                  # mov eax, [ecx + eax*4]
    a.raw(b'\xA3' + le32(cur_va))                           # mov [scan_cur_ent], eax
    a.raw(b'\x85\xC0'); a.jz('se_ent_next')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('se_ent_next')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('se_ent_next')
    # dedup: skip if entity already stamped this scan
    a.raw(b'\x8B\x48' + bytes([ax.ENTITY_VISIT_OFF]))       # mov ecx, [eax+0x2C]
    a.raw(b'\x3B\x0D' + le32(visit_va))                     # cmp ecx, [scan_visit_id]
    a.jae('se_ent_next')
    a.raw(b'\x8B\x0D' + le32(visit_va))                     # mov ecx, [scan_visit_id]
    a.raw(b'\x89\x48' + bytes([ax.ENTITY_VISIT_OFF]))       # mov [eax+0x2C], ecx
    # class filter (skip when scan_class_desc == 0)
    a.raw(b'\x8B\x0D' + le32(class_desc_va))                # mov ecx, [scan_class_desc]
    a.raw(b'\x85\xC9'); a.jz('se_collect')                  # 0 -> collect every entity
    a.raw(b'\x51')                                          # push classdesc (arg)
    a.raw(b'\x8B\x0D' + le32(cur_va))                       # mov ecx, [scan_cur_ent] (this)
    a.call_va(ax.SUB_416790_VA)                             # __thiscall is-a, ret 4 -> al
    a.raw(b'\x84\xC0'); a.jz('se_ent_next')                 # not the class -> skip

    a.label('se_collect')
    a.raw(b'\xA1' + le32(count_va))                         # eax = scan_count
    a.raw(b'\x3D' + le32(scan_max))                         # cmp eax, SCAN_ENTITIES_MAX
    # Table full -> END THE WHOLE SCAN (not just this cell). The result table is
    # a hard-bounded sample; SCAN_ENTITIES_MAX is sized to hold a map's entities,
    # so reaching it means we've collected the lot. (If full coverage with
    # truncation were ever wanted, branch to se_ent_next to keep walking.)
    a.jae('se_done')
    # position: sub_4FB0A0(ecx=ent, &scan_tmp_pos), ret 4
    a.raw(b'\x68' + le32(pos_va))                           # push &scan_tmp_pos
    a.raw(b'\x8B\x0D' + le32(cur_va))                       # mov ecx, [scan_cur_ent]
    a.call_va(ax.SUB_4FB0A0_VA)
    # record = scan_table + count*16
    a.raw(b'\x8B\x3D' + le32(count_va))                     # mov edi, scan_count
    a.raw(b'\xC1\xE7\x04')                                  # shl edi, 4
    a.raw(b'\x81\xC7' + le32(table_va))                     # add edi, scan_table
    a.raw(b'\xA1' + le32(cur_va))                           # eax = entity ptr
    a.raw(b'\x89\x07')                                      # [edi]    = ptr
    a.raw(b'\xA1' + le32(pos_va))                           # eax = x
    a.raw(b'\x89\x47\x04')                                  # [edi+4]  = x
    a.raw(b'\xA1' + le32(pos_va + 4))                       # eax = y
    a.raw(b'\x89\x47\x08')                                  # [edi+8]  = y
    a.raw(b'\xA1' + le32(cur_va))                           # eax = entity ptr
    a.raw(b'\x8B\x40' + bytes([ax.ENTITY_FLAGS_OFF]))       # mov eax, [eax+0x1C] (flags)
    a.raw(b'\x89\x47\x0C')                                  # [edi+0xC] = flags
    a.raw(b'\xFF\x05' + le32(count_va))                     # ++scan_count

    a.label('se_ent_next')
    a.raw(b'\xFF\x05' + le32(k_va))                         # ++k
    a.jmp('se_ent_loop')
    a.label('se_cell_next')
    a.raw(b'\xFF\x05' + le32(cellidx_va))                   # ++cellidx
    a.jmp('se_cell_loop')

    a.label('se_done')
    a.raw(b'\x61')                                          # popad
    a.raw(b'\xC3')                                          # ret

    # =====================================================================
    # scan_diag: validation entry — enumerate EVERY entity (class_desc = 0).
    # Leaves scan_count / scan_table populated for inspection (R-snapshot or
    # a direct CE read of scan_count). pushad/popad, no args/ret.
    # =====================================================================
    a.label('scan_diag')
    a.raw(b'\x60')                                          # pushad
    a.raw(b'\xC7\x05' + le32(class_desc_va) + le32(0))      # scan_class_desc = 0 (all)
    a.call_lbl('scan_entities')
    a.raw(b'\x61')                                          # popad
    a.raw(b'\xC3')                                          # ret

    # =====================================================================
    # scan_portal_active: walk every grid entity and, for each portal_table
    # entry, keep the NEAREST entity's Active bit in portal_active[i]. No result
    # table, so it is immune to SCAN_ENTITIES_MAX and reaches the teleporter pads
    # wherever they sit in the grid. portal_active[i] = 1 iff the entity nearest
    # portal_table[i] (within PORTAL_ACTIVE_MATCH_RADIUS_SQ) has its Active bit.
    # pushad/popad, no args/ret. Same bounded walk + visit-id dedup as
    # scan_entities; the per-entity body reads position then does the portal
    # match instead of class-collect. CTF flag-entity matching deliberately
    # uses raw entity +0x4C/+0x50 positions, not the sub_4FB0A0 getter: live
    # flag-base visual pieces can report the base anchor through the getter
    # while their raw anchor is offset from the actual capture/touch object.
    # =====================================================================
    if not (layout.has_field('portal_active') and layout.has_field('portal_table')
            and layout.has_field('portal_count')):
        a.label('scan_portal_active'); a.raw(b'\xC3')
        return

    portal_count_va = layout.va('portal_count')
    portal_table_va = layout.va('portal_table')
    active_va       = layout.va('portal_active')
    best_va         = layout.va('portal_best_dist')
    d2_va           = layout.va('scan_d2')
    entity_va       = layout.va('portal_entity') if layout.has_field('portal_entity') else 0
    # The scan's CTF role is CACHE-ONLY: it records the exact-anchor entities
    # (checker trigger / spawn marker / recreated flag) into flag_entity[] for
    # the far-base force-tick. flag_present[] is NOT derived here — it is
    # event-driven by the CActivateAction/CDeactivateAction apply detours
    # (detours/flag_events.py), which mirror the map script's checker state.
    flag_scan_enabled = (
        layout.has_field('flag_count')
        and layout.has_field('flag_table')
        and layout.has_field('flag_entity')
        and layout.has_field('flag_entity_match_radius_sq')
    )
    flag_count_va   = layout.va('flag_count') if flag_scan_enabled else 0
    flag_table_va   = layout.va('flag_table') if flag_scan_enabled else 0
    flag_entity_va  = layout.va('flag_entity') if flag_scan_enabled else 0
    flag_match_radius_va = layout.va('flag_entity_match_radius_sq') if flag_scan_enabled else 0
    flag_entity_slots = max(1, cfg.FLAG_ENTITY_SLOTS_PER_FLAG)
    # Door detection piggybacks the same walk, but the walk only maintains the
    # door_entity CACHE: every non-character entity sitting on a door_table
    # anchor (raw +0x4C/+0x50 match, like flags) is recorded — REGARDLESS of
    # its current SOLID state, because an OPEN door must stay cached so the
    # per-frame door_refresh_state (page-flip hook) can see it re-close.
    # door_blocked[] itself is owned by door_refresh_state, which re-reads the
    # cached entities' SOLID bit every frame; deriving state here was
    # live-tested and rejected (frame-counted scan interval => stale state
    # whenever FPS drops, e.g. with the overlay visible).
    door_scan_enabled = (
        cfg.DOOR_DETECT_ENABLED
        and layout.has_field('door_count')
        and layout.has_field('door_table')
        and layout.has_field('door_entity')
        and layout.has_field('door_match_radius_sq')
    )
    door_count_va         = layout.va('door_count') if door_scan_enabled else 0
    door_table_va         = layout.va('door_table') if door_scan_enabled else 0
    door_entity_va        = layout.va('door_entity') if door_scan_enabled else 0
    door_match_radius_va  = layout.va('door_match_radius_sq') if door_scan_enabled else 0
    door_entity_slots     = max(1, cfg.DOOR_ENTITY_SLOTS_PER_DOOR)
    # Dropped-flag detection piggybacks the same walk: while a flag is AWAY
    # from its base (flag_present[i] == 0), the world copy the drop-on-death
    # canned script creates is the only entity named exactly "Red Flag" /
    # "Blue Flag" (the 7 authored at-base blue icons carrying that name are
    # consumed the moment the flag is stolen — census pinned in tests), so an
    # exact name match against [ent+0x18]+8 (the sub_4FBF20 CString chain)
    # identifies it and its raw +0x4C/+0x50 is the dropped position. Present
    # flags cost two loads per entity; the compare only runs while a flag is
    # away. Runs AFTER the character shield below, so a player renamed like a
    # flag can never register as one.
    drop_scan_enabled = (
        cfg.CTF_DROPPED_FLAG_ENABLED
        and flag_scan_enabled
        and layout.has_field('flag_present')
        and layout.has_field('flag_team')
        and layout.has_field('flag_drop_valid')
        and layout.has_field('flag_drop_pos')
        and layout.has_field('drop_names')
    )
    flag_present_ds_va = layout.va('flag_present') if drop_scan_enabled else 0
    flag_team_ds_va    = layout.va('flag_team') if drop_scan_enabled else 0
    flag_drop_valid_va = layout.va('flag_drop_valid') if drop_scan_enabled else 0
    flag_drop_pos_va   = layout.va('flag_drop_pos') if drop_scan_enabled else 0
    drop_names_va      = layout.va('drop_names') if drop_scan_enabled else 0
    flag_drop_node_va  = (layout.va('flag_drop_node')
                          if drop_scan_enabled and layout.has_field('flag_drop_node') else 0)
    wp_scratch_ds_va   = (layout.va('wp_scratch')
                          if drop_scan_enabled and layout.has_field('wp_scratch') else 0)
    portal_max      = cfg.PORTAL_TABLE_MAX
    radius_bits     = struct.unpack('<I', struct.pack('<f', cfg.PORTAL_ACTIVE_MATCH_RADIUS_SQ))[0]

    a.label('scan_portal_active')
    a.raw(b'\x60')                                          # pushad
    a.raw(b'\x83\x3D' + le32(portal_count_va) + b'\x00')    # cmp [portal_count], 0
    a.jnz('spa_has_work')
    if flag_scan_enabled:
        a.raw(b'\x83\x3D' + le32(flag_count_va) + b'\x00')  # cmp [flag_count], 0
        a.jnz('spa_has_work')
    if door_scan_enabled:
        a.raw(b'\x83\x3D' + le32(door_count_va) + b'\x00')  # cmp [door_count], 0
        a.jnz('spa_has_work')
    a.jmp('spa_done')
    a.label('spa_has_work')

    # Init: portal_active[i] = 0, portal_best_dist[i] = match radius^2 (only an
    # entity strictly nearer than the radius can claim a portal).
    a.raw(b'\x31\xF6')                                      # xor esi, esi
    a.label('spa_init')
    a.raw(b'\x81\xFE' + le32(portal_max))                   # cmp esi, PORTAL_TABLE_MAX
    a.jae('spa_init_done')
    a.raw(b'\xC7\x04\xB5' + le32(active_va) + le32(0))      # mov [portal_active + esi*4], 0
    a.raw(b'\xC7\x04\xB5' + le32(best_va) + le32(radius_bits))  # mov [portal_best + esi*4], radius^2
    a.raw(b'\x46')                                          # inc esi
    a.jmp('spa_init')
    a.label('spa_init_done')
    if flag_scan_enabled:
        # Clear cached flag-anchor entities. The match loop below records the
        # distinct entities sitting exactly on each flag anchor (checker
        # trigger, spawn marker, recreated flag) for the far-base force-tick.
        a.raw(b'\x31\xF6')                                  # xor esi, esi
        a.label('spa_flag_init')
        a.raw(b'\x81\xFE' + le32(cfg.FLAG_TABLE_MAX * flag_entity_slots))
        a.jae('spa_flag_init_done')
        a.raw(b'\xC7\x04\xB5' + le32(flag_entity_va) + le32(0))
        a.raw(b'\x46')
        a.jmp('spa_flag_init')
        a.label('spa_flag_init_done')
    if door_scan_enabled:
        # The anchor-entity cache restarts empty each scan; the walk below
        # re-records every entity still sitting on a door anchor. The scan is
        # synchronous within this frame, so the per-frame refresh never sees a
        # half-built cache.
        a.raw(b'\xBF' + le32(door_entity_va))               # edi = door_entity
        a.raw(b'\xB9' + le32(cfg.DOOR_TABLE_MAX * door_entity_slots))
        a.raw(b'\x31\xC0')                                  # eax = 0
        a.raw(b'\xFC\xF3\xAB')                              # cld; rep stosd
    if drop_scan_enabled:
        # Dropped-flag positions restart unknown each scan; the walk below
        # re-proves every drop. Clear + rebuild happen synchronously inside
        # this one call, so consumers never see a half-built table.
        a.raw(b'\xBF' + le32(flag_drop_valid_va))           # edi = flag_drop_valid
        a.raw(b'\xB9' + le32(cfg.FLAG_TABLE_MAX))           # ecx = flag slots
        a.raw(b'\x31\xC0')                                  # eax = 0
        a.raw(b'\xFC\xF3\xAB')                              # cld; rep stosd

    # mgr -> layer -> grid (identical chain to scan_entities)
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))
    a.raw(b'\x85\xC0'); a.jz('spa_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('spa_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('spa_done')
    a.raw(b'\x8B\x80' + le32(ax.MGR_LAYER_ARRAY_OFF))
    a.raw(b'\x85\xC0'); a.jz('spa_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('spa_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('spa_done')
    a.raw(b'\x8B\x00')
    a.raw(b'\x85\xC0'); a.jz('spa_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('spa_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('spa_done')
    a.raw(b'\x8B\x88' + le32(ax.LAYER_GRID_ROWS_OFF))
    a.raw(b'\x8B\x90' + le32(ax.LAYER_GRID_COLS_OFF))
    a.raw(b'\x0F\xAF\xCA')
    a.raw(b'\x81\xF9\x00\x10\x00\x00')
    a.jbe('spa_nc_ok')
    a.raw(b'\xB9\x00\x10\x00\x00')
    a.label('spa_nc_ok')
    a.raw(b'\x89\x0D' + le32(ncells_va))
    a.raw(b'\x8B\x80' + le32(ax.LAYER_GRID_CELLS_OFF))
    a.raw(b'\x85\xC0'); a.jz('spa_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('spa_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('spa_done')
    a.raw(b'\xA3' + le32(cells_va))

    # visit id
    a.raw(b'\xA1' + le32(ax.ENTITY_VISIT_COUNTER_VA))
    a.raw(b'\x40')
    a.raw(b'\x3D\xFF\xFF\xFF\xEF')
    a.jbe('spa_vid_ok')
    a.raw(b'\xB8\x01\x00\x00\x00')
    a.label('spa_vid_ok')
    a.raw(b'\xA3' + le32(ax.ENTITY_VISIT_COUNTER_VA))
    a.raw(b'\xA3' + le32(visit_va))

    a.raw(b'\xC7\x05' + le32(cellidx_va) + le32(0))

    a.label('spa_cell_loop')
    a.raw(b'\xA1' + le32(cellidx_va))
    a.raw(b'\x3B\x05' + le32(ncells_va))
    a.jae('spa_after_grid_scan')
    a.raw(b'\x89\xC2')
    a.raw(b'\xC1\xE2\x04')
    a.raw(b'\x03\x15' + le32(cells_va))
    a.raw(b'\x8B\x42\x04')
    a.raw(b'\xA3' + le32(list_va))
    a.raw(b'\x85\xC0'); a.jz('spa_cell_next')
    a.raw(b'\x8B\x42\x08')
    a.raw(b'\x3D\x00\x01\x00\x00')
    a.jbe('spa_cnt_ok')
    a.raw(b'\xB8\x00\x01\x00\x00')
    a.label('spa_cnt_ok')
    a.raw(b'\xA3' + le32(cnt_va))
    a.raw(b'\x85\xC0'); a.jz('spa_cell_next')
    a.raw(b'\xC7\x05' + le32(k_va) + le32(0))

    a.label('spa_ent_loop')
    a.raw(b'\xA1' + le32(k_va))
    a.raw(b'\x3B\x05' + le32(cnt_va))
    a.jae('spa_cell_next')
    a.raw(b'\x8B\x0D' + le32(list_va))
    a.raw(b'\x8B\x04\x81')
    a.raw(b'\xA3' + le32(cur_va))
    a.raw(b'\x85\xC0'); a.jz('spa_ent_next')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('spa_ent_next')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('spa_ent_next')
    a.raw(b'\x8B\x48' + bytes([ax.ENTITY_VISIT_OFF]))
    a.raw(b'\x3B\x0D' + le32(visit_va))
    a.jae('spa_ent_next')
    a.raw(b'\x8B\x0D' + le32(visit_va))
    a.raw(b'\x89\x48' + bytes([ax.ENTITY_VISIT_OFF]))
    # position: sub_4FB0A0(ecx=ent, &scan_tmp_pos)
    a.raw(b'\x68' + le32(pos_va))
    a.raw(b'\x8B\x0D' + le32(cur_va))
    a.call_va(ax.SUB_4FB0A0_VA)
    # portal match: nearest within radius wins
    a.raw(b'\x31\xF6')                                      # xor esi, esi (portal i)
    a.label('spa_match')
    a.raw(b'\x3B\x35' + le32(portal_count_va))              # cmp esi, [portal_count]
    a.jae('spa_after_portal_match')
    # d2 = (portal[i].x - pos.x)^2 + (portal[i].y - pos.y)^2
    a.raw(b'\xD9\x04\xF5' + le32(portal_table_va))          # fld [portal_table + esi*8]
    a.raw(b'\xD8\x25' + le32(pos_va))                       # fsub [pos.x]
    a.raw(b'\xD8\xC8')                                      # fmul st,st
    a.raw(b'\xD9\x04\xF5' + le32(portal_table_va + 4))      # fld [portal_table + esi*8 + 4]
    a.raw(b'\xD8\x25' + le32(pos_va + 4))                   # fsub [pos.y]
    a.raw(b'\xD8\xC8')                                      # fmul st,st
    a.raw(b'\xDE\xC1')                                      # faddp -> st0 = d2
    a.raw(b'\xD9\x15' + le32(d2_va))                        # fst [scan_d2] (keep d2, no pop)
    a.raw(b'\xD8\x1C\xB5' + le32(best_va))                  # fcomp [portal_best + esi*4] (pops)
    a.raw(b'\xDF\xE0\x9E')                                  # fnstsw ax; sahf
    a.jae('spa_match_next')                                 # d2 >= best -> not nearer
    # nearer: best[i] = d2; remember the entity; active[i] = bit23(flags)
    a.raw(b'\xA1' + le32(d2_va))                            # eax = d2
    a.raw(b'\x89\x04\xB5' + le32(best_va))                  # [portal_best + esi*4] = d2
    if entity_va:
        a.raw(b'\xA1' + le32(cur_va))                       # eax = ent
        a.raw(b'\x89\x04\xB5' + le32(entity_va))            # [portal_entity + esi*4] = ent
    a.raw(b'\xA1' + le32(cur_va))                           # eax = ent
    a.raw(b'\x8B\x40' + bytes([ax.ENTITY_FLAGS_OFF]))       # eax = [ent+0x1C] flags
    a.raw(b'\xC1\xE8\x17')                                  # shr eax, 23  (Active bit -> bit 0)
    a.raw(b'\x83\xE0\x01')                                  # and eax, 1
    a.raw(b'\x89\x04\xB5' + le32(active_va))                # [portal_active + esi*4] = 0/1
    a.label('spa_match_next')
    a.raw(b'\x46')                                          # inc esi
    a.jmp('spa_match')

    a.label('spa_after_portal_match')
    if flag_scan_enabled or door_scan_enabled:
        # Do not let a player/bot standing on a flag base count as one of the
        # home flag/base entities. Live CE caught exactly that: the red carrier
        # at its red base was cached in flag_entity[red][0], which made
        # flag_present[red] true while the host was carrying the real red flag.
        # That both kept routing the carrier into the empty base and let the
        # far-base force-tick path tick the bot a second time as a "base"
        # entity. Portal matching above still sees characters; only flag-base
        # presence ignores them. The DOOR match below needs the same shield:
        # a character is SOLID, so one standing in an OPEN doorway would
        # otherwise mark that door blocked for a scan interval.
        a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))          # eax = [mgr]
        a.raw(b'\x85\xC0'); a.jz('spa_flag_not_character')
        a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('spa_flag_not_character')
        a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('spa_flag_not_character')
        a.raw(b'\x8B\x98\x94\x02\x00\x00')                   # ebx = [mgr+0x294] char count
        a.raw(b'\x8B\xB8\x90\x02\x00\x00')                   # edi = [mgr+0x290] char array
        a.raw(b'\x85\xFF'); a.jz('spa_flag_not_character')
        a.raw(b'\x81\xFF\x00\x00\x10\x00'); a.jb('spa_flag_not_character')
        a.raw(b'\x81\xFF\x00\x00\x00\x70'); a.jae('spa_flag_not_character')
        a.raw(b'\x83\xFB' + bytes([MAX_CHARACTER_SCAN]))
        a.jbe('spa_flag_char_count_ok')
        a.raw(b'\xBB' + le32(MAX_CHARACTER_SCAN))
        a.label('spa_flag_char_count_ok')
        a.raw(b'\x31\xD2')                                  # edx = char index
        a.raw(b'\x8B\x0D' + le32(cur_va))                    # ecx = ent
        a.label('spa_flag_char_loop')
        a.raw(b'\x39\xDA'); a.jae('spa_flag_not_character')  # idx >= count
        a.raw(b'\x3B\x0C\x97')                              # cmp ecx, [edi + edx*4]
        a.jz('spa_ent_next')                                # character -> skip flag match
        a.raw(b'\x42')                                      # ++idx
        a.jmp('spa_flag_char_loop')

        a.label('spa_flag_not_character')
    if flag_scan_enabled:
        a.raw(b'\x31\xF6')                                  # xor esi, esi (flag i)
        a.label('spa_flag_match')
        a.raw(b'\x3B\x35' + le32(flag_count_va))            # cmp esi, [flag_count]
        a.jae('spa_after_flag_match')
        # d2 = (flag[i].x - ent.raw_x)^2 + (flag[i].y - ent.raw_y)^2.
        # The generic position getter is right for portal pads, but for CTF
        # flag bases it can alias nearby visual/base pieces to the same anchor.
        # The capture/touch entities sit exactly on the authored flag anchor in
        # raw entity coordinates, so use those fields for the cache.
        a.raw(b'\xA1' + le32(cur_va))                       # eax = ent
        a.raw(b'\xD9\x04\xF5' + le32(flag_table_va))        # fld [flag_table + esi*8]
        a.raw(b'\xD8\x60\x4C')                              # fsub [ent+0x4C] raw x
        a.raw(b'\xD8\xC8')                                  # fmul st,st
        a.raw(b'\xD9\x04\xF5' + le32(flag_table_va + 4))    # fld [flag_table + esi*8 + 4]
        a.raw(b'\xD8\x60\x50')                              # fsub [ent+0x50] raw y
        a.raw(b'\xD8\xC8')                                  # fmul st,st
        a.raw(b'\xDE\xC1')                                  # faddp -> st0 = d2
        a.raw(b'\xD9\x15' + le32(d2_va))                    # fst [scan_d2] (keep d2, no pop)
        a.raw(b'\xD8\x1D' + le32(flag_match_radius_va))     # fcomp [match_radius] (pops)
        a.raw(b'\xDF\xE0\x9E')                              # fnstsw ax; sahf
        a.ja('spa_flag_match_next')                         # d2 > radius -> skip
        # Cache each distinct entity matched exactly at this flag anchor. Up to
        # three can legitimately coexist there (checker trigger, spawn marker,
        # recreated flag entity); the force-tick wakes all cached slots, so the
        # checker cannot be evicted by grid iteration order. This cache carries
        # NO presence meaning — flag_present[] is owned by the checker
        # (de)activation event detours.
        a.raw(b'\x8B\x15' + le32(cur_va))                   # edx = ent
        if flag_entity_slots == 2:
            slot_index_scale = b'\xF5'                      # [esi*8 + disp]
            slot_index_pre = b''
        else:
            # index = i * flag_entity_slots, addressed as [ecx*4 + disp].
            assert flag_entity_slots == 3, 'unrolled store expects 2 or 3 slots'
            slot_index_scale = b'\x8D'                      # [ecx*4 + disp]
            slot_index_pre = b'\x8D\x0C\x76'                # lea ecx, [esi + esi*2]
        a.raw(slot_index_pre)
        for k in range(flag_entity_slots):
            a.raw(b'\x8B\x04' + slot_index_scale + le32(flag_entity_va + 4 * k))
            a.raw(b'\x85\xC0'); a.jz(f'spa_flag_store{k}')  # empty slot -> claim
            a.raw(b'\x39\xD0'); a.jz('spa_flag_match_next') # same entity already stored
        a.jmp('spa_flag_match_next')                        # all slots occupied
        for k in range(flag_entity_slots):
            a.label(f'spa_flag_store{k}')
            a.raw(b'\x89\x14' + slot_index_scale + le32(flag_entity_va + 4 * k))
            a.jmp('spa_flag_match_next')
        a.label('spa_flag_match_next')
        a.raw(b'\x46')                                      # inc esi
        a.jmp('spa_flag_match')
        a.label('spa_after_flag_match')

    if drop_scan_enabled:
        # --- Dropped-flag name match. Only flags currently AWAY from their
        # base are candidates (flag_present gate — also what makes the name
        # unambiguous, see the drop_scan_enabled comment above). The compare
        # is an exact NUL-terminated byte match of the entity name against
        # this flag team's expected string (drop_names + team*16).
        a.raw(b'\x31\xF6')                                  # xor esi, esi (flag i)
        a.label('spa_drop_match')
        a.raw(b'\x3B\x35' + le32(flag_count_va))            # i >= flag_count?
        a.jae('spa_drop_done')
        a.raw(b'\x83\xFE' + bytes([cfg.FLAG_TABLE_MAX]))    # i >= table max?
        a.jae('spa_drop_done')
        a.raw(b'\x83\x3C\xB5' + le32(flag_present_ds_va) + b'\x00')  # flag home?
        a.jnz('spa_drop_next')                              # present -> no dropped copy
        # entity name ASCII = [ent+0x18] + 8, range-checked like every deref.
        a.raw(b'\xA1' + le32(cur_va))                       # eax = ent
        a.raw(b'\x8B\x40' + bytes([ax.ENTITY_NAME_CSTR_OFF]))  # eax = name CString hdr
        a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('spa_drop_done')   # unnamed -> no flag matches
        a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('spa_drop_done')
        a.raw(b'\x8D\x50\x08')                              # lea edx, [eax+8] (ASCII)
        # expected = drop_names + (flag_team[i] & 1) * 16
        a.raw(b'\x8B\x04\xB5' + le32(flag_team_ds_va))      # eax = flag_team[i]
        a.raw(b'\x83\xE0\x01')                              # and eax, 1 (defensive)
        a.raw(b'\xC1\xE0\x04')                              # shl eax, 4 (16B name slots)
        a.raw(b'\x8D\x98' + le32(drop_names_va))            # lea ebx, [eax + drop_names]
        a.label('spa_drop_cmp')
        a.raw(b'\x8A\x02')                                  # al = [entity name]
        a.raw(b'\x3A\x03')                                  # cmp al, [expected]
        a.jnz('spa_drop_next')                              # mismatch -> not this flag
        a.raw(b'\x84\xC0'); a.jz('spa_drop_hit')            # both NUL -> exact match
        a.raw(b'\x42\x43')                                  # inc edx; inc ebx
        a.jmp('spa_drop_cmp')
        a.label('spa_drop_hit')
        # Record the dropped copy's raw position (the authored/created entity
        # position IS +0x4C/+0x50, same convention as the flag/door anchors).
        a.raw(b'\xA1' + le32(cur_va))                       # eax = ent
        a.raw(b'\x8B\x48' + bytes([ax.ENTITY_POS_X_OFF]))   # ecx = raw x bits
        a.raw(b'\x89\x0C\xF5' + le32(flag_drop_pos_va))     # flag_drop_pos[i].x
        a.raw(b'\x8B\x48' + bytes([ax.ENTITY_POS_Y_OFF]))   # ecx = raw y bits
        a.raw(b'\x89\x0C\xF5' + le32(flag_drop_pos_va + 4)) # flag_drop_pos[i].y
        a.raw(b'\xC7\x04\xB5' + le32(flag_drop_valid_va) + le32(1))
        if flag_drop_node_va and wp_scratch_ds_va:
            # Bind the drop to its nearest graph node — the root of the
            # drop_dist BFS row the follower descends while pursuing beyond
            # the direct radius. wp_find_nearest clobbers GPRs; the flag idx
            # is spilled into scan_d2 (dead here — its portal-match use ended
            # earlier in this entity's pass).
            a.raw(b'\x89\x35' + le32(d2_va))                # spill flag idx (esi)
            a.raw(b'\x8B\x04\xF5' + le32(flag_drop_pos_va)) # eax = drop.x
            a.raw(b'\xA3' + le32(wp_scratch_ds_va))         # wp_scratch.x
            a.raw(b'\x8B\x04\xF5' + le32(flag_drop_pos_va + 4))
            a.raw(b'\xA3' + le32(wp_scratch_ds_va + 4))     # wp_scratch.y
            a.call_lbl('wp_find_nearest')                   # ebx = nearest or -1
            a.raw(b'\x8B\x35' + le32(d2_va))                # esi = flag idx
            a.raw(b'\x89\x1C\xB5' + le32(flag_drop_node_va))  # flag_drop_node[i] = ebx
        # An entity carries ONE name, so it can match at most one flag —
        # after a hit the remaining flags cannot match; end the flag loop.
        a.jmp('spa_drop_done')
        a.label('spa_drop_next')
        a.raw(b'\x46')                                      # inc esi
        a.jmp('spa_drop_match')
        a.label('spa_drop_done')

    if door_scan_enabled:
        # Door anchor-entity caching. Characters never reach here (the
        # exclusion above jumps them to spa_ent_next), so a bot/player
        # standing in a doorway is never cached as a door piece. Entities are
        # cached regardless of SOLID state — the per-frame refresh needs the
        # OPEN door entity too, to see it close again.
        a.raw(b'\x31\xF6')                                  # xor esi, esi (door i)
        a.label('spa_door_match')
        a.raw(b'\x3B\x35' + le32(door_count_va))            # cmp esi, [door_count]
        a.jae('spa_ent_next')
        # d2 = (door[i].x - ent.raw_x)^2 + (door[i].y - ent.raw_y)^2 — raw
        # entity coordinates, same rationale as the flag anchors: the authored
        # Level Part position IS the door entity's +0x4C/+0x50.
        a.raw(b'\xA1' + le32(cur_va))                       # eax = ent
        a.raw(b'\xD9\x04\xF5' + le32(door_table_va))        # fld [door_table + esi*8]
        a.raw(b'\xD8\x60\x4C')                              # fsub [ent+0x4C] raw x
        a.raw(b'\xD8\xC8')                                  # fmul st,st
        a.raw(b'\xD9\x04\xF5' + le32(door_table_va + 4))    # fld [door_table + esi*8 + 4]
        a.raw(b'\xD8\x60\x50')                              # fsub [ent+0x50] raw y
        a.raw(b'\xD8\xC8')                                  # fmul st,st
        a.raw(b'\xDE\xC1')                                  # faddp -> st0 = d2
        a.raw(b'\xD8\x1D' + le32(door_match_radius_va))     # fcomp [match_radius] (pops)
        a.raw(b'\xDF\xE0\x9E')                              # fnstsw ax; sahf
        a.ja('spa_door_next')                               # d2 > radius -> not this door
        # Cache each distinct entity at this anchor (same claim/dedup shape as
        # the flag-anchor slots; stride = DOOR_ENTITY_SLOTS_PER_DOOR).
        assert door_entity_slots == 3, 'door cache unrolled store expects 3 slots'
        a.raw(b'\x8B\x15' + le32(cur_va))                   # edx = ent
        a.raw(b'\x8D\x0C\x76')                              # lea ecx, [esi + esi*2]
        for k in range(door_entity_slots):
            a.raw(b'\x8B\x04\x8D' + le32(door_entity_va + 4 * k))
            a.raw(b'\x85\xC0'); a.jz(f'spa_door_store{k}')  # empty slot -> claim
            a.raw(b'\x39\xD0'); a.jz('spa_door_next')       # already cached
        a.jmp('spa_door_next')                              # all slots occupied
        for k in range(door_entity_slots):
            a.label(f'spa_door_store{k}')
            a.raw(b'\x89\x14\x8D' + le32(door_entity_va + 4 * k))
            a.jmp('spa_door_next')
        a.label('spa_door_next')
        a.raw(b'\x46')                                      # inc esi
        a.jmp('spa_door_match')

    a.label('spa_ent_next')
    a.raw(b'\xFF\x05' + le32(k_va))
    a.jmp('spa_ent_loop')
    a.label('spa_cell_next')
    a.raw(b'\xFF\x05' + le32(cellidx_va))
    a.jmp('spa_cell_loop')

    a.label('spa_after_grid_scan')
    a.label('spa_done')
    a.raw(b'\x61')                                          # popad
    a.raw(b'\xC3')                                          # ret
