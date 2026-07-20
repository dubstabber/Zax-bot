"""Door + switch tables (static + live), door-aware routing and
switch-seek state (one authored block: switches need the door tables)."""

from .model import ScratchField


def extend_door_switch_tables(c):
    door_table_max = c.door_table_max
    door_static_map_max = c.door_static_map_max
    door_static_point_max = c.door_static_point_max
    door_map_name_slot = c.door_map_name_slot
    door_entity_slots = c.door_entity_slots
    door_opener_table_max = c.door_opener_table_max
    door_opener_static_max = c.door_opener_static_max
    switch_table_max = c.switch_table_max
    switch_pair_max = c.switch_pair_max
    switch_static_map_max = c.switch_static_map_max
    switch_static_point_max = c.switch_static_point_max
    switch_static_pair_max = c.switch_static_pair_max
    switch_map_name_slot = c.switch_map_name_slot
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    overlay_color_size = c.overlay_color_size
    overlay_edge_max_capped = c.overlay_edge_max_capped
    overlay_fields = c.overlay_fields
    overlay_vertex_max_capped = c.overlay_vertex_max_capped
    flag_static_base = c.flag_static_base
    flag_route_max_capped = c.flag_route_max_capped
    door_static_base = c.door_static_base
    door_table_max_capped = c.door_table_max_capped
    door_dyn_base = c.door_dyn_base
    sw_off = c.sw_off
    switch_table_max_capped = c.switch_table_max_capped


    # --- Door detection tables ---------------------------------------------
    # Mirrors the portal/flag static-table blocks. The live door_table is
    # populated once per match by load_doors from the compact build-time
    # static table (parsed from Data.dat); door_blocked[] is refreshed by the
    # periodic grid walk (scan_portal_active) reading each anchored entity's
    # SOLID flag. Placed at the very tail so no existing scratch offset shifts.
    door_table_max_capped = max(0, door_table_max)
    door_static_map_max_capped = max(0, door_static_map_max)
    door_static_point_max_capped = max(0, door_static_point_max)
    door_name_slot_capped = max(0, door_map_name_slot)
    door_opener_table_max_capped = max(0, door_opener_table_max)
    door_opener_static_max_capped = max(0, door_opener_static_max)
    # Door map records carry TWO (count, first) pairs: points and openers.
    door_map_stride = door_name_slot_capped + 16
    if door_table_max_capped > 0:
        door_base = flag_static_base
        overlay_fields.extend([
            ScratchField('door_count', door_base + 0x00, 0x04,
                         'door: live entries in door_table for active map'),
            ScratchField('overlay_door_color', door_base + 0x04, overlay_color_size,
                         'overlay: detected-door CColor (rebuilt per-frame)'),
            ScratchField('door_match_radius_sq', door_base + 0x14, 0x04,
                         'door: max d^2 for matching a grid entity to a door anchor'),
            ScratchField('door_wedge_radius_sq', door_base + 0x18, 0x04,
                         'door: max d^2 bot-to-door when latching a wedge to a blocked door'),
            ScratchField('door_tmp_d2', door_base + 0x1C, 0x04,
                         'door: per-call d^2 temp (door_capture_wedge)'),
            ScratchField('door_tmp_best', door_base + 0x20, 0x04,
                         'door: per-call best-d^2 tracker (door_capture_wedge)'),
            ScratchField('door_table', door_base + 0x24,
                         door_table_max_capped * 8,
                         'door: float[2] per door center (world coords, active map)'),
            ScratchField('door_blocked', door_base + 0x24 + door_table_max_capped * 8,
                         door_table_max_capped * 4,
                         'door: 1 = a SOLID entity sits on this anchor (door closed)'),
            ScratchField('route_block_door',
                         door_base + 0x24 + door_table_max_capped * 12,
                         MAX_BOT_SLOTS * 4,
                         'door: per-bot door idx the failed-edge marker wedged against (-1 none)'),
        ])
        door_static_base = door_base + 0x24 + door_table_max_capped * 12 + MAX_BOT_SLOTS * 4
        overlay_fields.extend([
            ScratchField('door_static_map_count', door_static_base + 0x00, 0x04,
                         'door: build-time static map table count'),
            ScratchField('door_static_point_count', door_static_base + 0x04, 0x04,
                         'door: build-time static point table count'),
        ])
        door_static_base += 0x08
        if door_static_map_max_capped > 0 and door_map_stride > 8:
            overlay_fields.append(ScratchField(
                'door_static_maps', door_static_base,
                door_static_map_max_capped * door_map_stride,
                'door: static map records (name/count/first point)',
            ))
            door_static_base += door_static_map_max_capped * door_map_stride
        if door_static_point_max_capped > 0:
            overlay_fields.append(ScratchField(
                'door_static_points', door_static_base,
                door_static_point_max_capped * 8,
                'door: static float[2] point table parsed from Data.dat',
            ))
            door_static_base += door_static_point_max_capped * 8
            overlay_fields.append(ScratchField(
                'door_static_flags', door_static_base,
                door_static_point_max_capped,
                'door: per-static-point flag byte (bit0 = has ANY authored opener)',
            ))
            door_static_base += door_static_point_max_capped
        if door_opener_static_max_capped > 0:
            overlay_fields.append(ScratchField(
                'door_static_openers', door_static_base,
                door_opener_static_max_capped * 16,
                'door: static (x f32, y f32, door_idx u32, team_mask u32) opener records',
            ))
            door_static_base += door_opener_static_max_capped * 16
        if door_opener_table_max_capped > 0:
            overlay_fields.extend([
                ScratchField('door_opener_count', door_static_base, 0x04,
                             'door: live opener records for the active map'),
                ScratchField('door_opener', door_static_base + 0x04,
                             door_opener_table_max_capped * 16,
                             'door: live (x, y, door_idx, team_mask) bot-usable opener records'),
                ScratchField('door_flags', door_static_base + 0x04 + door_opener_table_max_capped * 16,
                             door_table_max_capped,
                             'door: live per-door flag byte (bit0 = has ANY authored opener)'),
                ScratchField('edge_pass',
                             door_static_base + 0x04 + door_opener_table_max_capped * 16
                             + door_table_max_capped,
                             max(1, overlay_edge_max_capped),
                             'door: per-edge byte — bits0-1 team0 / bits2-3 team1 from-i/from-j closed-door traversability'),
                ScratchField('cnh_blk',
                             door_static_base + 0x04 + door_opener_table_max_capped * 16
                             + door_table_max_capped + max(1, overlay_edge_max_capped),
                             0x04,
                             'door: per-edge blocked-door spill (bfs_run / ctf_next_hop temp)'),
                ScratchField('door_mask_i',
                             door_static_base + 0x08 + door_opener_table_max_capped * 16
                             + door_table_max_capped + max(1, overlay_edge_max_capped),
                             0x04,
                             'door: active from-i edge_pass mask (1 << team*2), set per bfs_run/next-hop'),
                ScratchField('door_mask_j',
                             door_static_base + 0x0C + door_opener_table_max_capped * 16
                             + door_table_max_capped + max(1, overlay_edge_max_capped),
                             0x04,
                             'door: active from-j edge_pass mask (2 << team*2)'),
            ])
            door_static_base += (0x10 + door_opener_table_max_capped * 16
                                 + door_table_max_capped + max(1, overlay_edge_max_capped))

        # --- Per-frame door state + door-aware routing -------------------
        # door_entity[] is the anchor-entity cache maintained by the periodic
        # grid walk; door_refresh_state re-reads the cached entities' SOLID
        # bit every frame (state must not be coupled to the FPS-dependent
        # scan interval). edge_door[] is the static per-match edge->door
        # adjacency; flag_dist_open is the second BFS field that skips
        # closed-door edges (rebuilt on door_dirty, debounced).
        door_entity_slots_capped = max(1, door_entity_slots)
        door_dyn_base = door_static_base
        overlay_fields.extend([
            ScratchField('door_entity', door_dyn_base,
                         door_table_max_capped * door_entity_slots_capped * 4,
                         'door: cached non-character entity ptrs at each door anchor'),
            ScratchField('door_dirty',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4,
                         0x04, 'door: 1 = door_blocked[] changed since last open-field rebuild'),
            ScratchField('door_rebuild_cd',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x04,
                         0x04, 'door: frames until the next open-field rebuild is allowed'),
            ScratchField('route_use_open',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x08,
                         0x04, 'door: 1 = ctf_next_hop scanning the open field (skip blocked edges)'),
            ScratchField('bfs_start',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x0C,
                         0x04, 'door: BFS start node spill (bfs_run input)'),
            ScratchField('bfs_skip',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x10,
                         0x04, 'door: 1 = bfs_run skips edges crossing blocked doors'),
            ScratchField('bed_len2',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x14,
                         0x04, 'door: build_edge_doors |seg|^2 temp'),
            ScratchField('bed_rx',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x18,
                         0x04, 'door: build_edge_doors (door - P).x temp'),
            ScratchField('bed_ry',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x1C,
                         0x04, 'door: build_edge_doors (door - P).y temp'),
            ScratchField('bed_d2',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x20,
                         0x04, 'door: build_edge_doors point-segment d^2 temp'),
            ScratchField('bed_best',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x24,
                         0x04, 'door: build_edge_doors best-d^2 tracker'),
            ScratchField('door_edge_radius_sq',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x28,
                         0x04, 'door: max d^2 door-to-edge-segment for edge_door adjacency'),
        ])
        door_dyn_base += door_table_max_capped * door_entity_slots_capped * 4 + 0x2C
        if overlay_edge_max_capped > 0:
            overlay_fields.append(ScratchField(
                'edge_door', door_dyn_base, overlay_edge_max_capped * 4,
                'door: per-edge nearest door idx within DOOR_EDGE_RADIUS (-1 = none)',
            ))
            door_dyn_base += overlay_edge_max_capped * 4
        if flag_route_max_capped > 0 and overlay_vertex_max_capped > 0:
            # TEAM-MAJOR: row for (team, base) = (team*FLAG_ROUTE_MAX + base).
            # Two fields because closed-door traversability is per-team
            # (same-team-conditional walk-up doors).
            overlay_fields.append(ScratchField(
                'flag_dist_open', door_dyn_base,
                2 * flag_route_max_capped * overlay_vertex_max_capped * 4,
                'door: per-team BFS hop-distance fields gating closed-door edges directionally',
            ))
            door_dyn_base += 2 * flag_route_max_capped * overlay_vertex_max_capped * 4
        # R-snapshot tags for the door-state diagnostic chunks (the fixed tag
        # block at 0x730..0x8FF is full). Only present on door-enabled builds;
        # the static writer and snapshot emitter both skip absent tag fields.
        for tag_field in ('tag_door_cnt', 'tag_door_blk', 'tag_door_ent',
                          'tag_door_dyn', 'tag_edge_door', 'tag_edge_pass',
                          'tag_rstate'):
            overlay_fields.append(ScratchField(
                tag_field, door_dyn_base, 0x10,
                'diag: door-state dump chunk tag',
            ))
            door_dyn_base += 0x10

        # --- Switch tables (CollideTriggerAI bump switches) ----------------
        # Mirrors the door static/live split. Live tables are per-match copies
        # (load_switches); pairs bind switch idx -> door idx in the SAME map's
        # door_table order (u32 = switch_idx | door_idx << 16). Nested inside
        # the door block because pair door indices are meaningless without the
        # door tables.
        switch_table_max_capped = max(0, switch_table_max)
        switch_pair_max_capped = max(0, switch_pair_max)
        switch_static_map_max_capped = max(0, switch_static_map_max)
        switch_static_point_max_capped = max(0, switch_static_point_max)
        switch_static_pair_max_capped = max(0, switch_static_pair_max)
        switch_name_slot_capped = max(0, switch_map_name_slot)
        switch_map_stride = switch_name_slot_capped + 16
        if switch_table_max_capped > 0:
            sw_base = door_dyn_base
            overlay_fields.extend([
                ScratchField('switch_count', sw_base + 0x00, 0x04,
                             'switch: live entries in switch_table for active map'),
                ScratchField('overlay_switch_color', sw_base + 0x04, overlay_color_size,
                             'overlay: detected-switch CColor (rebuilt per-frame)'),
                ScratchField('switch_pair_count', sw_base + 0x14, 0x04,
                             'switch: live (switch, door) pair records for active map'),
                ScratchField('switch_table', sw_base + 0x18,
                             switch_table_max_capped * 8,
                             'switch: float[2] per switch center (world coords, active map)'),
            ])
            sw_off = sw_base + 0x18 + switch_table_max_capped * 8
            if switch_pair_max_capped > 0:
                overlay_fields.append(ScratchField(
                    'switch_pairs', sw_off, switch_pair_max_capped * 4,
                    'switch: live u32 pair records (switch_idx | door_idx << 16)',
                ))
                sw_off += switch_pair_max_capped * 4
            switch_flags_padded = (switch_table_max_capped + 3) & ~3
            overlay_fields.append(ScratchField(
                'switch_flags', sw_off, switch_table_max_capped,
                'switch: live per-switch class byte (door_data.SWITCH_FLAG_*)',
            ))
            sw_off += switch_flags_padded
            overlay_fields.extend([
                ScratchField('switch_static_map_count', sw_off + 0x00, 0x04,
                             'switch: build-time static map table count'),
                ScratchField('switch_static_point_count', sw_off + 0x04, 0x04,
                             'switch: build-time static point table count'),
            ])
            sw_off += 0x08
            if switch_static_map_max_capped > 0 and switch_map_stride > 16:
                overlay_fields.append(ScratchField(
                    'switch_static_maps', sw_off,
                    switch_static_map_max_capped * switch_map_stride,
                    'switch: static map records (name | switch cnt/first | pair cnt/first)',
                ))
                sw_off += switch_static_map_max_capped * switch_map_stride
            if switch_static_point_max_capped > 0:
                overlay_fields.append(ScratchField(
                    'switch_static_points', sw_off,
                    switch_static_point_max_capped * 8,
                    'switch: static float[2] center table parsed from Data.dat',
                ))
                sw_off += switch_static_point_max_capped * 8
                if switch_static_pair_max_capped > 0:
                    overlay_fields.append(ScratchField(
                        'switch_static_pairs', sw_off,
                        switch_static_pair_max_capped * 4,
                        'switch: static u32 pair records (switch_idx | door_idx << 16)',
                    ))
                    sw_off += switch_static_pair_max_capped * 4
                switch_static_flags_padded = (switch_static_point_max_capped + 3) & ~3
                overlay_fields.append(ScratchField(
                    'switch_static_flags', sw_off,
                    switch_static_point_max_capped,
                    'switch: per-static-switch class byte (door_data.SWITCH_FLAG_*)',
                ))
                sw_off += switch_static_flags_padded
            overlay_fields.append(ScratchField(
                'tag_switch', sw_off, 0x10,
                'diag: switch-table dump chunk tag',
            ))
            sw_off += 0x10

            # --- Switch-seek routing state -----------------------------
            # Per-team (2 entries each): a bot whose goal is open-field
            # unreachable (or far cheaper through a closed door) requests a
            # seek; the page-flip eval picks the best viable door-opening
            # switch (paired door blocked, node bound, best full-field score
            # toward the requester's goal), BFS-fills seek_dist rooted at its
            # node with this team's door gating, and activates. ctf_next_hop
            # then descends seek_dist; at the switch node the follower
            # final-approaches the switch center to BUMP it.
            overlay_fields.append(ScratchField(
                'switch_node', sw_off, switch_table_max_capped * 4,
                'seek: nearest graph node per live switch (-1 = unbound)',
            ))
            sw_off += switch_table_max_capped * 4
            for name, desc in (
                ('seek_active',   'seek: [team] active switch idx+1 (0 = none)'),
                ('seek_node',     'seek: [team] graph node of the active switch'),
                ('seek_pending',  'seek: [team] 1 = a bot requested an eval'),
                ('seek_req_node', 'seek: [team] requesting bot\'s node'),
                ('seek_req_goal', 'seek: [team] requesting bot\'s goal base idx'),
                ('seek_tried',    'seek: [team] eval-round tried-candidate bitmask'),
                ('seek_fail',     'seek: [team] timeout blacklist bitmask (cleared on rebuild)'),
                ('seek_timer',    'seek: [team] frames before the active seek expires'),
                ('seek_best',     'seek: [team] eval-round best candidate idx+1 (0 = none)'),
                ('seek_best_score', 'seek: [team] best combined walk+goal score so far'),
                ('seek_req_open', 'seek: [team] requester open-field dist to goal (-1 unreachable) — activation benefit bar'),
            ):
                overlay_fields.append(ScratchField(name, sw_off, 0x08, desc))
                sw_off += 0x08
            overlay_fields.append(ScratchField(
                'bot_seek', sw_off, MAX_BOT_SLOTS * 4,
                'seek: per-bot 1 = descending the seek field this leg',
            ))
            sw_off += MAX_BOT_SLOTS * 4
            # Candidate-index spill for switch_seek_eval. MUST NOT be bfs_u:
            # bfs_run uses bfs_u as its dequeued-node scratch and overwrites
            # it, which mis-attributed every eval result to a NODE id (live
            # R-dump: tried-bit 14 / best 47 on a 2-switch map).
            overlay_fields.append(ScratchField(
                'seek_eval_s', sw_off, 0x04,
                'seek: eval candidate index spill (survives bfs_run)',
            ))
            sw_off += 0x04
            overlay_fields.append(ScratchField(
                'bot_door_patience', sw_off, MAX_BOT_SLOTS * 4,
                'door: per-bot count of progress-timeouts bypassed while wedged at a closed door',
            ))
            sw_off += MAX_BOT_SLOTS * 4
            if overlay_vertex_max_capped > 0:
                overlay_fields.append(ScratchField(
                    'seek_dist', sw_off,
                    2 * overlay_vertex_max_capped * 4,
                    'seek: per-team BFS hop field rooted at the active switch node',
                ))
                sw_off += 2 * overlay_vertex_max_capped * 4
            overlay_fields.append(ScratchField(
                'tag_seek', sw_off, 0x10,
                'diag: switch-seek state dump chunk tag',
            ))
            sw_off += 0x10

    c.door_static_base = door_static_base
    c.door_table_max_capped = door_table_max_capped
    c.door_dyn_base = door_dyn_base
    c.sw_off = sw_off
    c.switch_table_max_capped = switch_table_max_capped

