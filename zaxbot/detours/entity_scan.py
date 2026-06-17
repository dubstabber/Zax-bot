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
    # match instead of class-collect.
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
    portal_max      = cfg.PORTAL_TABLE_MAX
    radius_bits     = struct.unpack('<I', struct.pack('<f', cfg.PORTAL_ACTIVE_MATCH_RADIUS_SQ))[0]

    a.label('scan_portal_active')
    a.raw(b'\x60')                                          # pushad
    a.raw(b'\x83\x3D' + le32(portal_count_va) + b'\x00')    # cmp [portal_count], 0
    a.jz('spa_done')

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
    a.jae('spa_done')
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
    a.jae('spa_ent_next')
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

    a.label('spa_ent_next')
    a.raw(b'\xFF\x05' + le32(k_va))
    a.jmp('spa_ent_loop')
    a.label('spa_cell_next')
    a.raw(b'\xFF\x05' + le32(cellidx_va))
    a.jmp('spa_cell_loop')

    a.label('spa_done')
    a.raw(b'\x61')                                          # popad
    a.raw(b'\xC3')                                          # ret
