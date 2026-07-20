"""World entity scanner collect table."""

from .model import ScratchField


def extend_entity_scan(c):
    scan_entities_max = c.scan_entities_max
    overlay_fields = c.overlay_fields
    portal_static_base = c.portal_static_base
    portal_table_max_capped = c.portal_table_max_capped
    tail_off = c.tail_off


    # --- World entity scanner (detours/entity_scan.py) --------------------
    # Loop state + result table for scan_entities (the general spatial-grid
    # walk). All per-call scratch; lives at the very tail so nothing else
    # shifts. scan_table is `scan_entities_max` records of 16 bytes each:
    #   (entity_ptr u32, x f32, y f32, flags u32).
    tail_off = portal_static_base
    if scan_entities_max > 0:
        scan_base = portal_static_base
        tail_off = scan_base + 0x30 + scan_entities_max * 16
        overlay_fields.extend([
            ScratchField('scan_class_desc', scan_base + 0x00, 0x04,
                         'scan: class descriptor to match (0 = collect every entity)'),
            ScratchField('scan_count',      scan_base + 0x04, 0x04,
                         'scan: live entries written to scan_table'),
            ScratchField('scan_visit_id',   scan_base + 0x08, 0x04,
                         'scan: this-scan visit id (mirrors engine dword_622200 dedup)'),
            ScratchField('scan_ncells',     scan_base + 0x0C, 0x04,
                         'scan: rows*cols cell count (capped)'),
            ScratchField('scan_cells',      scan_base + 0x10, 0x04,
                         'scan: grid cells array base'),
            ScratchField('scan_cellidx',    scan_base + 0x14, 0x04,
                         'scan: outer cell-loop index'),
            ScratchField('scan_list',       scan_base + 0x18, 0x04,
                         'scan: current cell entity-pointer array'),
            ScratchField('scan_cnt',        scan_base + 0x1C, 0x04,
                         'scan: current cell entity count (capped)'),
            ScratchField('scan_k',          scan_base + 0x20, 0x04,
                         'scan: inner entity-loop index'),
            ScratchField('scan_cur_ent',    scan_base + 0x24, 0x04,
                         'scan: current entity ptr (survives helper calls)'),
            ScratchField('scan_tmp_pos',    scan_base + 0x28, 0x08,
                         'scan: float[2] for sub_4FB0A0 entity-pos reads'),
            ScratchField('scan_table',      scan_base + 0x30, scan_entities_max * 16,
                         'scan: (ptr, x, y, flags) records collected by scan_entities'),
        ])
        # Per-portal active-state (scan_portal_active). portal_active is the
        # output (1 = nearest pad entity is Active); portal_best_dist is the
        # per-portal nearest-distance tracker (per-call temp); portal_scan_count
        # is the page-flip re-scan countdown. Sized to PORTAL_TABLE_MAX.
        if portal_table_max_capped > 0:
            pa_base = scan_base + 0x30 + scan_entities_max * 16
            overlay_fields.extend([
                ScratchField('portal_active',     pa_base + 0x00, portal_table_max_capped * 4,
                             'portal: per-pad active flag (1 = nearest entity has the Active bit)'),
                ScratchField('portal_best_dist',  pa_base + portal_table_max_capped * 4,
                             portal_table_max_capped * 4,
                             'portal: per-pad nearest-entity d^2 tracker (scan_portal_active temp)'),
                ScratchField('portal_scan_count', pa_base + portal_table_max_capped * 8, 0x04,
                             'portal: page-flip re-scan countdown for scan_portal_active'),
                ScratchField('scan_d2', pa_base + portal_table_max_capped * 8 + 0x04, 0x04,
                             'portal: float d^2 temp for the nearest-pad compare'),
                ScratchField('portal_entity', pa_base + portal_table_max_capped * 8 + 0x08,
                             portal_table_max_capped * 4,
                             'portal: the matched (nearest) entity ptr per pad (diag / direct read)'),
            ])
            tail_off = pa_base + portal_table_max_capped * 12 + 0x08

    c.tail_off = tail_off

