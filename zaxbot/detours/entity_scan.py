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
    flag_scan_enabled = (
        layout.has_field('flag_count')
        and layout.has_field('flag_table')
        and layout.has_field('flag_entity')
        and layout.has_field('flag_present')
        and layout.has_field('flag_entity_match_radius_sq')
    )
    flag_count_va   = layout.va('flag_count') if flag_scan_enabled else 0
    flag_table_va   = layout.va('flag_table') if flag_scan_enabled else 0
    flag_team_va    = layout.va('flag_team') if flag_scan_enabled else 0
    flag_entity_va  = layout.va('flag_entity') if flag_scan_enabled else 0
    flag_present_va = layout.va('flag_present') if flag_scan_enabled else 0
    flag_match_radius_va = layout.va('flag_entity_match_radius_sq') if flag_scan_enabled else 0
    flag_carried_enabled = (
        flag_scan_enabled
        and layout.has_field('ctf_score_block')
        and layout.has_field('ctf_score_team')
        and layout.has_field('ctf_score_target_def')
        and layout.has_field('ctf_score_gid')
        and layout.has_field('ctf_score_inv')
    )
    ctf_score_block_va = layout.va('ctf_score_block') if flag_carried_enabled else 0
    ctf_score_team_va = layout.va('ctf_score_team') if flag_carried_enabled else 0
    ctf_score_target_def_va = layout.va('ctf_score_target_def') if flag_carried_enabled else 0
    ctf_score_gid_va = layout.va('ctf_score_gid') if flag_carried_enabled else 0
    portal_max      = cfg.PORTAL_TABLE_MAX
    radius_bits     = struct.unpack('<I', struct.pack('<f', cfg.PORTAL_ACTIVE_MATCH_RADIUS_SQ))[0]

    a.label('scan_portal_active')
    a.raw(b'\x60')                                          # pushad
    a.raw(b'\x83\x3D' + le32(portal_count_va) + b'\x00')    # cmp [portal_count], 0
    a.jnz('spa_has_work')
    if flag_scan_enabled:
        a.raw(b'\x83\x3D' + le32(flag_count_va) + b'\x00')  # cmp [flag_count], 0
        a.jz('spa_done')
    else:
        a.jz('spa_done')
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
        # Clear cached flag-anchor entities. The match loop below records up to
        # two entities at each flag base because the home/base interaction can be
        # split across a visual flag and a touch/action object at the same point.
        a.raw(b'\x31\xF6')                                  # xor esi, esi
        a.label('spa_flag_init')
        a.raw(b'\x81\xFE' + le32(cfg.FLAG_TABLE_MAX * cfg.FLAG_ENTITY_SLOTS_PER_FLAG))
        a.jae('spa_flag_init_done')
        a.raw(b'\xC7\x04\xB5' + le32(flag_entity_va) + le32(0))
        a.raw(b'\x46')
        a.jmp('spa_flag_init')
        a.label('spa_flag_init_done')
        a.raw(b'\x31\xF6')                                  # xor esi, esi
        a.label('spa_flag_present_init')
        a.raw(b'\x81\xFE' + le32(cfg.FLAG_TABLE_MAX))
        a.jae('spa_flag_present_init_done')
        a.raw(b'\xC7\x04\xB5' + le32(flag_present_va) + le32(0))
        a.raw(b'\x46')
        a.jmp('spa_flag_present_init')
        a.label('spa_flag_present_init_done')
        if flag_carried_enabled:
            # Reuse ctf_score_gid as this scan's dropped-away bitmask:
            # bit0 = Blue Flag item seen away from the Blue base;
            # bit1 = Red Flag item seen away from the Red base. The carried
            # inventory check below overwrites this diagnostic field later.
            a.raw(b'\xC7\x05' + le32(ctf_score_gid_va) + le32(0))
            a.raw(b'\x6A\xFF')                              # push -1
            a.raw(b'\x68' + le32(ax.BLUE_FLAG_STR_VA))       # push "Blue Flag"
            a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))   # item registry
            a.call_va(ax.SUB_523DF0_VA)
            a.raw(b'\xA3' + le32(ctf_score_target_def_va))   # temp: Blue flag def id
            a.raw(b'\x6A\xFF')                              # push -1
            a.raw(b'\x68' + le32(ax.RED_FLAG_STR_VA))        # push "Red Flag"
            a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))   # item registry
            a.call_va(ax.SUB_523DF0_VA)
            a.raw(b'\xA3' + le32(ctf_score_team_va))         # temp: Red flag def id

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
    if flag_scan_enabled:
        # Do not let a player/bot standing on a flag base count as one of the
        # home flag/base entities. Live CE caught exactly that: the red carrier
        # at its red base was cached in flag_entity[red][0], which made
        # flag_present[red] true while the host was carrying the real red flag.
        # That both kept routing the carrier into the empty base and let the
        # far-base force-tick path tick the bot a second time as a "base"
        # entity. Portal matching above still sees characters; only flag-base
        # presence ignores them.
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
        if flag_carried_enabled:
            # A dropped CTF flag is a world item entity whose CInventoryItem
            # definition id lives at +8, the same field the inventory guard
            # compares after inv.vtable[+0x68]. If that exact Red/Blue flag
            # item exists away from its own home anchor, subtract it from
            # flag_present[] after the scan so base action entities cannot make
            # the team look home while the flag is dropped elsewhere.
            a.raw(b'\xA1' + le32(cur_va))                   # eax = ent
            a.raw(b'\x8B\x58\x08')                          # ebx = ent->definition id
            a.raw(b'\x83\x3D' + le32(ctf_score_target_def_va) + b'\xFF')
            a.jz('spa_drop_check_red')
            a.raw(b'\x3B\x1D' + le32(ctf_score_target_def_va))
            a.jnz('spa_drop_check_red')
            a.raw(b'\x31\xC9')                              # ecx = team 0 (Blue)
            a.call_lbl('spa_note_dropped_flag_entity')
            a.jmp('spa_drop_done')
            a.label('spa_drop_check_red')
            a.raw(b'\x83\x3D' + le32(ctf_score_team_va) + b'\xFF')
            a.jz('spa_drop_done')
            a.raw(b'\x3B\x1D' + le32(ctf_score_team_va))
            a.jnz('spa_drop_done')
            a.raw(b'\xB9\x01\x00\x00\x00')                  # ecx = team 1 (Red)
            a.call_lbl('spa_note_dropped_flag_entity')
            a.label('spa_drop_done')

        a.raw(b'\x31\xF6')                                  # xor esi, esi (flag i)
        a.label('spa_flag_match')
        a.raw(b'\x3B\x35' + le32(flag_count_va))            # cmp esi, [flag_count]
        a.jae('spa_ent_next')
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
        # Store the first two distinct entities matched at this flag anchor.
        # Presence is location/entity-count based, not Active-bit based:
        # Active is also used by camera-gating, so an off-camera home flag can
        # be inactive while still present. Seeing the expected second exact-
        # anchor entity is the current best signal that the flag/base pair is
        # home.
        a.raw(b'\x8B\x15' + le32(cur_va))                   # edx = ent
        a.raw(b'\x8B\x04\xF5' + le32(flag_entity_va))       # eax = flag_entity[i*2 + 0]
        a.raw(b'\x85\xC0'); a.jz('spa_flag_store0')
        a.raw(b'\x39\xD0'); a.jz('spa_flag_match_next')     # same entity already stored
        a.raw(b'\x8B\x04\xF5' + le32(flag_entity_va + 4))   # eax = flag_entity[i*2 + 1]
        a.raw(b'\x85\xC0'); a.jz('spa_flag_store1')
        a.raw(b'\x39\xD0'); a.jz('spa_flag_match_next')     # same entity already stored
        a.jmp('spa_flag_match_next')                        # both slots occupied
        a.label('spa_flag_store0')
        a.raw(b'\x89\x14\xF5' + le32(flag_entity_va))       # flag_entity[i*2 + 0] = ent
        a.jmp('spa_flag_match_next')
        a.label('spa_flag_store1')
        a.raw(b'\x89\x14\xF5' + le32(flag_entity_va + 4))   # flag_entity[i*2 + 1] = ent
        a.raw(b'\xC7\x04\xB5' + le32(flag_present_va) + le32(1))  # flag_present[i] = 1
        a.label('spa_flag_match_next')
        a.raw(b'\x46')                                      # inc esi
        a.jmp('spa_flag_match')

    a.label('spa_ent_next')
    a.raw(b'\xFF\x05' + le32(k_va))
    a.jmp('spa_ent_loop')
    a.label('spa_cell_next')
    a.raw(b'\xFF\x05' + le32(cellidx_va))
    a.jmp('spa_cell_loop')

    a.label('spa_after_grid_scan')
    if flag_carried_enabled:
        # Apply the dropped-away subtraction before the carried-inventory
        # subtraction. This covers "flag is away but no longer carried" after a
        # carrier death/respawn: home-base action entities may still be matched
        # at the anchor, but the exact Red/Blue flag world item is elsewhere.
        a.raw(b'\xA1' + le32(ctf_score_gid_va))              # eax = dropped-away bitmask
        a.raw(b'\xA9\x01\x00\x00\x00')                      # test eax, 1 (Blue)
        a.jz('spa_dropped_blue_done')
        a.raw(b'\x31\xC9')                                  # ecx = team 0
        a.call_lbl('spa_clear_flag_present_team')
        a.label('spa_dropped_blue_done')
        a.raw(b'\xA1' + le32(ctf_score_gid_va))              # eax = dropped-away bitmask
        a.raw(b'\xA9\x02\x00\x00\x00')                      # test eax, 2 (Red)
        a.jz('spa_dropped_red_done')
        a.raw(b'\xB9\x01\x00\x00\x00')                      # ecx = team 1
        a.call_lbl('spa_clear_flag_present_team')
        a.label('spa_dropped_red_done')

        # The exact-anchor entity pair can remain present at a base while the
        # actual Red/Blue flag inventory item is carried. After the entity scan
        # refreshes flag_present[], subtract the existing guard helper's
        # stronger "own flag away" check so CTF routing sees carried flags as
        # absent without adding an inventory scan to the per-frame movement path.
        a.raw(b'\xC7\x05' + le32(ctf_score_team_va) + le32(0))  # Blue team
        a.raw(b'\x6A\xFF')                                     # push -1
        a.raw(b'\x68' + le32(ax.BLUE_FLAG_STR_VA))             # push "Blue Flag"
        a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))         # item registry
        a.call_va(ax.SUB_523DF0_VA)
        a.raw(b'\xA3' + le32(ctf_score_target_def_va))
        a.call_lbl('csg_check_current_team')
        a.raw(b'\x83\x3D' + le32(ctf_score_block_va) + b'\x00')
        a.jz('spa_carried_blue_done')
        a.raw(b'\x31\xC9')                                     # ecx = team 0
        a.call_lbl('spa_clear_flag_present_team')
        a.label('spa_carried_blue_done')

        a.raw(b'\xC7\x05' + le32(ctf_score_team_va) + le32(1))  # Red team
        a.raw(b'\x6A\xFF')                                     # push -1
        a.raw(b'\x68' + le32(ax.RED_FLAG_STR_VA))              # push "Red Flag"
        a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))         # item registry
        a.call_va(ax.SUB_523DF0_VA)
        a.raw(b'\xA3' + le32(ctf_score_target_def_va))
        a.call_lbl('csg_check_current_team')
        a.raw(b'\x83\x3D' + le32(ctf_score_block_va) + b'\x00')
        a.jz('spa_carried_red_done')
        a.raw(b'\xB9\x01\x00\x00\x00')                         # ecx = team 1
        a.call_lbl('spa_clear_flag_present_team')
        a.label('spa_carried_red_done')
    a.jmp('spa_done')

    a.label('spa_done')
    a.raw(b'\x61')                                          # popad
    a.raw(b'\xC3')                                          # ret

    if flag_carried_enabled:
        # spa_note_dropped_flag_entity(ecx = team): the current grid entity is
        # the exact Red/Blue flag world item. If its raw position is not at that
        # team's home flag anchor, OR no matching home anchor exists, set the
        # corresponding bit in ctf_score_gid. Called only from the coarse scan.
        a.label('spa_note_dropped_flag_entity')
        a.raw(b'\x89\x0D' + le32(ctf_score_block_va))        # temp team
        a.raw(b'\x8B\x1D' + le32(flag_count_va))             # ebx = flag_count
        a.raw(b'\x83\xFB' + bytes([cfg.FLAG_TABLE_MAX]))
        a.jbe('spa_ndfe_count_ok')
        a.raw(b'\xBB' + le32(cfg.FLAG_TABLE_MAX))
        a.label('spa_ndfe_count_ok')
        a.raw(b'\x31\xF6')                                   # esi = 0
        a.label('spa_ndfe_loop')
        a.raw(b'\x39\xDE'); a.jae('spa_ndfe_away')           # no home match -> away
        a.raw(b'\x8B\x04\xB5' + le32(flag_team_va))          # eax = flag_team[i]
        a.raw(b'\x3B\x05' + le32(ctf_score_block_va))
        a.jnz('spa_ndfe_next')
        # d2 = (flag[i].x - ent.raw_x)^2 + (flag[i].y - ent.raw_y)^2
        a.raw(b'\xA1' + le32(cur_va))                        # eax = ent
        a.raw(b'\xD9\x04\xF5' + le32(flag_table_va))         # fld [flag_table + esi*8]
        a.raw(b'\xD8\x60\x4C')                               # fsub [ent+0x4C] raw x
        a.raw(b'\xD8\xC8')                                   # fmul st,st
        a.raw(b'\xD9\x04\xF5' + le32(flag_table_va + 4))     # fld [flag_table + esi*8 + 4]
        a.raw(b'\xD8\x60\x50')                               # fsub [ent+0x50] raw y
        a.raw(b'\xD8\xC8')                                   # fmul st,st
        a.raw(b'\xDE\xC1')                                   # faddp -> st0 = d2
        a.raw(b'\xD8\x1D' + le32(flag_match_radius_va))      # fcomp [match_radius] (pop)
        a.raw(b'\xDF\xE0\x9E')                               # fnstsw ax; sahf
        a.jbe('spa_ndfe_home')                               # d2 <= radius: flag is home
        a.label('spa_ndfe_next')
        a.raw(b'\x46')                                       # ++i
        a.jmp('spa_ndfe_loop')
        a.label('spa_ndfe_away')
        a.raw(b'\xB8\x01\x00\x00\x00')                       # eax = 1
        a.raw(b'\x8B\x0D' + le32(ctf_score_block_va))        # ecx = team
        a.raw(b'\xD3\xE0')                                   # shl eax, cl
        a.raw(b'\x09\x05' + le32(ctf_score_gid_va))          # dropped_mask |= bit(team)
        a.label('spa_ndfe_home')
        a.raw(b'\xC3')

        # spa_clear_flag_present_team(ecx = team): clear flag_present[i] for the
        # live flag-table entry tagged with that team. Called only from the
        # coarse scan, never from the per-frame movement path.
        a.label('spa_clear_flag_present_team')
        a.raw(b'\x8B\x1D' + le32(flag_count_va))             # ebx = flag_count
        a.raw(b'\x83\xFB' + bytes([cfg.FLAG_TABLE_MAX]))
        a.jbe('spa_cfpt_count_ok')
        a.raw(b'\xBB' + le32(cfg.FLAG_TABLE_MAX))
        a.label('spa_cfpt_count_ok')
        a.raw(b'\x31\xF6')                                   # esi = 0
        a.label('spa_cfpt_loop')
        a.raw(b'\x39\xDE'); a.jae('spa_cfpt_ret')            # i >= count
        a.raw(b'\x8B\x04\xB5' + le32(flag_team_va))          # eax = flag_team[i]
        a.raw(b'\x39\xC8')                                   # team?
        a.jnz('spa_cfpt_next')
        a.raw(b'\xC7\x04\xB5' + le32(flag_present_va) + le32(0))
        a.label('spa_cfpt_next')
        a.raw(b'\x46')                                       # ++i
        a.jmp('spa_cfpt_loop')
        a.label('spa_cfpt_ret')
        a.raw(b'\xC3')
