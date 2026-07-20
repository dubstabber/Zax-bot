"""CTF flag tables, BFS routing fields and dropped-flag pursuit
state."""

from .model import ScratchField


def extend_flag_tables(c):
    flag_table_max = c.flag_table_max
    flag_static_map_max = c.flag_static_map_max
    flag_static_point_max = c.flag_static_point_max
    flag_map_name_slot = c.flag_map_name_slot
    flag_entity_slots = c.flag_entity_slots
    overlay_color_size = c.overlay_color_size
    overlay_fields = c.overlay_fields
    tail_off = c.tail_off
    flag_static_base = c.flag_static_base
    flag_table_max_capped = c.flag_table_max_capped


    # --- CTF flag overlay data --------------------------------------------
    # Mirrors the portal static-table block. The live flag_table is populated
    # once per match by load_flags from the compact build-time static table
    # (parsed from Data.dat). Placed at the very tail so no existing scratch
    # offset shifts. Map entries are fixed-size:
    #   name[FLAG_MAP_NAME_SLOT] | count u32 | first_point_index u32
    flag_table_max_capped = max(0, flag_table_max)
    flag_static_map_max_capped = max(0, flag_static_map_max)
    flag_static_point_max_capped = max(0, flag_static_point_max)
    flag_name_slot_capped = max(0, flag_map_name_slot)
    flag_map_stride = flag_name_slot_capped + 8
    flag_base = tail_off
    overlay_fields.extend([
        ScratchField('flag_count', flag_base + 0x00, 0x04,
                     'flag: live entries in flag_table for active map'),
        ScratchField('overlay_flag_color', flag_base + 0x04, overlay_color_size,
                     'overlay: detected-flag CColor (rebuilt per-frame)'),
        ScratchField('flag_entity_match_radius_sq', flag_base + 0x14, 0x04,
                     'flag: max d^2 for matching live entities to a flag anchor'),
        ScratchField('flag_home_tick_radius_sq', flag_base + 0x18, 0x04,
                     'flag: max d^2 for force-ticking home flag entities'),
        ScratchField('flag_evt_present', flag_base + 0x1C, 0x04,
                     'flag: value (0/1) the activate/deactivate event detours write to flag_present'),
    ])
    flag_static_base = flag_base + 0x20
    if flag_table_max_capped > 0:
        overlay_fields.append(ScratchField(
            'flag_table', flag_static_base,
            flag_table_max_capped * 8,
            'flag: float[2] per CTF flag home base (world coords)',
        ))
        flag_static_base += flag_table_max_capped * 8
        overlay_fields.append(ScratchField(
            'flag_team', flag_static_base,
            flag_table_max_capped * 4,
            'flag: team tag per live flag_table entry (0=Blue, 1=Red)',
        ))
        flag_static_base += flag_table_max_capped * 4
        flag_entity_slots_capped = max(1, flag_entity_slots)
        overlay_fields.append(ScratchField(
            'flag_entity', flag_static_base,
            flag_table_max_capped * flag_entity_slots_capped * 4,
            'flag: live entity ptrs matched exactly at each flag anchor (checker/marker/flag)',
        ))
        flag_static_base += flag_table_max_capped * flag_entity_slots_capped * 4
        overlay_fields.append(ScratchField(
            'flag_present', flag_static_base,
            flag_table_max_capped * 4,
            'flag: 1 iff the expected exact-anchor flag/base entity pair is matched',
        ))
        flag_static_base += flag_table_max_capped * 4
    overlay_fields.extend([
        ScratchField('flag_static_map_count', flag_static_base + 0x00, 0x04,
                     'flag: build-time static map table count'),
        ScratchField('flag_static_point_count', flag_static_base + 0x04, 0x04,
                     'flag: build-time static point table count'),
    ])
    flag_static_base += 0x08
    if flag_static_map_max_capped > 0 and flag_map_stride > 8:
        overlay_fields.append(ScratchField(
            'flag_static_maps', flag_static_base,
            flag_static_map_max_capped * flag_map_stride,
            'flag: static map records (name/count/first point)',
        ))
        flag_static_base += flag_static_map_max_capped * flag_map_stride
    if flag_static_point_max_capped > 0:
        overlay_fields.append(ScratchField(
            'flag_static_points', flag_static_base,
            flag_static_point_max_capped * 8,
            'flag: static float[2] point table parsed from Data.dat',
        ))
        flag_static_base += flag_static_point_max_capped * 8
        overlay_fields.append(ScratchField(
            'flag_static_team', flag_static_base,
            flag_static_point_max_capped * 4,
            'flag: static team tag (DWORD per point, parallel to flag_static_points)',
        ))
        flag_static_base += flag_static_point_max_capped * 4

    c.flag_static_base = flag_static_base
    c.flag_table_max_capped = flag_table_max_capped



def extend_flag_routing(c):
    overlay_vertex_max = c.overlay_vertex_max
    flag_route_max = c.flag_route_max
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    overlay_fields = c.overlay_fields
    overlay_vertex_max_capped = c.overlay_vertex_max_capped
    flag_static_base = c.flag_static_base
    flag_route_max_capped = c.flag_route_max_capped


    # --- CTF flag routing (BFS path field over the waypoint graph) --------
    # Precomputed once per match by build_flag_routes (from detour_df90) and
    # consumed by ctf_next_hop at each bot node arrival. flag_dist[i][node] =
    # hop distance from flag base i's nearest node to `node` (0xFFFFFFFF =
    # unreachable / no graph). BFS/routing fields are global; bfs_* are
    # transient load-time scratch for the BFS itself.
    flag_route_max_capped = max(0, flag_route_max)
    overlay_vertex_max_capped = max(0, overlay_vertex_max)
    if flag_route_max_capped > 0 and overlay_vertex_max_capped > 0:
        route_base = flag_static_base
        overlay_fields.extend([
            ScratchField('flag_routing_active', route_base + 0x00, 0x04,
                         'flag-route: 1 iff CTF + graph + flags + routes built this match'),
            ScratchField('route_cur', route_base + 0x04, 0x04,
                         'flag-route: ctf_next_hop spill of current node idx'),
            ScratchField('bfs_head', route_base + 0x08, 0x04,
                         'flag-route: BFS queue head (load-time)'),
            ScratchField('bfs_tail', route_base + 0x0C, 0x04,
                         'flag-route: BFS queue tail (load-time)'),
            ScratchField('bfs_u', route_base + 0x10, 0x04,
                         'flag-route: BFS current node u (load-time)'),
            ScratchField('bfs_du', route_base + 0x14, 0x04,
                         'flag-route: BFS dist of u (load-time)'),
            ScratchField('bfs_disti', route_base + 0x18, 0x04,
                         'flag-route: BFS dist-array base for the base being built'),
            ScratchField('bfr_i', route_base + 0x1C, 0x04,
                         'flag-route: build_flag_routes outer base-loop index (survives wp_find_nearest+BFS)'),
            ScratchField('route_carry', route_base + 0x20, 0x04,
                         'flag-route: ctf_next_hop carry flag spill (survives sub_4267E0/sub_425290)'),
            ScratchField('route_goal_flag', route_base + 0x24, 0x04,
                         'flag-route: ctf_pick_goal output = goal flag index (home if carrying, else enemy; -1 = none)'),
            ScratchField('route_missing_policy', route_base + 0x28, MAX_BOT_SLOTS * 4,
                         'flag-route: per-bot missing-flag policy (0 unset, 1 search, 2 wait/base-route)'),
            ScratchField('route_missing_goal', route_base + 0x28 + MAX_BOT_SLOTS * 4, MAX_BOT_SLOTS * 4,
                         'flag-route: per-bot goal index that route_missing_policy applies to'),
            ScratchField('bot_route_suspend', route_base + 0x28 + MAX_BOT_SLOTS * 8, MAX_BOT_SLOTS * 4,
                         'flag-route: per-bot frames of routing suspension (roam after a routed wedge)'),
            ScratchField('route_block_hits', route_base + 0x28 + MAX_BOT_SLOTS * 12, MAX_BOT_SLOTS * 4,
                         'flag-route: per-bot count of routed hops forced off the marked failed edge'),
        ])
        route_tail = route_base + 0x28 + MAX_BOT_SLOTS * 16
        overlay_fields.extend([
            ScratchField('route_epoch', route_tail + 0x00, 0x04,
                         'flag-route: bumped when the open-door BFS field is rebuilt; a routed bot whose stored epoch differs re-acquires so ctf_next_hop re-runs against the new field'),
            ScratchField('bot_route_epoch', route_tail + 0x04, MAX_BOT_SLOTS * 4,
                         'flag-route: per-bot last route_epoch re-evaluated under (mid-life door-change reroute trigger)'),
        ])
        route_tail += 0x04 + MAX_BOT_SLOTS * 4
        overlay_fields.extend([
            ScratchField('ctf_score_block', route_tail + 0x00, 0x04,
                         'ctf-score: 1 suppresses CGiveTeamAPointAction award'),
            ScratchField('ctf_score_team', route_tail + 0x04, 0x04,
                         'ctf-score: score recipient team from CGiveTeamAPointAction+8'),
            ScratchField('ctf_score_target_def', route_tail + 0x08, 0x04,
                         'ctf-score: own Red/Blue flag item-definition id for score recipient'),
            ScratchField('ctf_score_gid', route_tail + 0x0C, 0x04,
                         'ctf-score: Multiplayer Flag inventory group id'),
            ScratchField('ctf_score_inv', route_tail + 0x10, 0x04,
                         'ctf-score: inventory ptr across inventory helper calls'),
        ])
        route_tail += 0x14
        overlay_fields.append(ScratchField(
            'flag_route_node', route_tail, flag_route_max_capped * 4,
            'flag-route: nearest graph node to each routed flag base (goal node)',
        ))
        route_tail += flag_route_max_capped * 4
        overlay_fields.append(ScratchField(
            'flag_dist', route_tail,
            flag_route_max_capped * overlay_vertex_max_capped * 4,
            'flag-route: per-base BFS hop-distance field (FLAG_ROUTE_MAX x vertex_max dwords)',
        ))
        route_tail += flag_route_max_capped * overlay_vertex_max_capped * 4
        overlay_fields.append(ScratchField(
            'bfs_queue', route_tail, overlay_vertex_max_capped * 4,
            'flag-route: BFS FIFO of node indices (load-time transient)',
        ))
        route_tail += overlay_vertex_max_capped * 4
        flag_static_base = route_tail

    c.overlay_vertex_max_capped = overlay_vertex_max_capped
    c.flag_static_base = flag_static_base
    c.flag_route_max_capped = flag_route_max_capped



def extend_drop_pursuit(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    overlay_fields = c.overlay_fields
    overlay_vertex_max_capped = c.overlay_vertex_max_capped
    flag_table_max_capped = c.flag_table_max_capped


    # --- CTF dropped-flag pursuit -------------------------------------------
    # Appended at the very tail (after the portal-routing block) so no existing
    # scratch offset shifts. flag_drop_valid/pos/node are rebuilt by the
    # periodic grid walk (name-matched world copies of away flags + their
    # nearest graph node); drop_dist is a per-drop BFS hop field (bfs_run,
    # rebuilt by drop_route_refresh when the drop's node changes) that the
    # follower descends while pursuing beyond the direct radius. The block
    # from flag_drop_valid through drop_pursue_enabled is CONTIGUOUS and
    # dumped whole by the R-snapshot `dpursuit` chunk (tests pin the
    # ordering); drop_dist sits after the tag and is excluded (2KB of field,
    # like seek_dist).
    if flag_table_max_capped > 0:
        drop_base = max(
            [f.end for f in fields] + [f.end for f in overlay_fields]
        )
        drop_base = (drop_base + 7) & ~7
        overlay_fields.extend([
            ScratchField('flag_drop_valid', drop_base,
                         flag_table_max_capped * 4,
                         'drop: 1 = a dropped world copy of flag i was seen last scan'),
            ScratchField('flag_drop_pos',
                         drop_base + flag_table_max_capped * 4,
                         flag_table_max_capped * 8,
                         'drop: float[2] dropped-copy position per flag (raw +0x4C/+0x50)'),
            ScratchField('flag_drop_node',
                         drop_base + flag_table_max_capped * 12,
                         flag_table_max_capped * 4,
                         'drop: nearest graph node to the dropped copy (-1 = unbound)'),
            ScratchField('bot_drop_target',
                         drop_base + flag_table_max_capped * 16,
                         MAX_BOT_SLOTS * 4,
                         'drop: per-bot latched pursuit (flag idx+1, 0 = none)'),
            ScratchField('bot_drop_cd',
                         drop_base + flag_table_max_capped * 16 + MAX_BOT_SLOTS * 4,
                         MAX_BOT_SLOTS * 4,
                         'drop: per-bot pursuit cooldown (thinks; after grab or timeout)'),
            ScratchField('bot_drop_try',
                         drop_base + flag_table_max_capped * 16 + MAX_BOT_SLOTS * 8,
                         MAX_BOT_SLOTS * 4,
                         'drop: per-bot direct-phase press-patience cycles used'),
            ScratchField('bot_drop_best',
                         drop_base + flag_table_max_capped * 16 + MAX_BOT_SLOTS * 12,
                         MAX_BOT_SLOTS * 4,
                         'drop: per-bot direct-phase min dsq-to-drop (float; FLT_MAX outside direct)'),
        ])
        drop_off = drop_base + flag_table_max_capped * 16 + MAX_BOT_SLOTS * 16
        overlay_fields.extend([
            ScratchField('drop_route_root', drop_off + 0x00, 0x08,
                         'drop: node each drop_dist row is currently built from (-1 = row invalid)'),
            ScratchField('drop_pursue_radius_sq', drop_off + 0x08, 0x04,
                         'drop: opportunistic divert trigger radius^2 (float)'),
            ScratchField('drop_reached_radius_sq', drop_off + 0x0C, 0x04,
                         'drop: divert arrival radius^2 (float)'),
            ScratchField('drop_direct_radius_sq', drop_off + 0x10, 0x04,
                         'drop: straight-steer (direct phase) radius^2 (float)'),
            ScratchField('drop_abandon_radius_sq', drop_off + 0x14, 0x04,
                         'drop: silently unlatch beyond this d^2 (float; objective bots exempt)'),
            ScratchField('drop_pursue_enabled', drop_off + 0x18, 0x04,
                         'drop: master enable flag (runtime)'),
            ScratchField('drop_names', drop_off + 0x1C, 0x20,
                         'drop: expected entity names, 16B slots by team (0 "Blue Flag", 1 "Red Flag")'),
            ScratchField('tag_dpursuit', drop_off + 0x3C, 0x10,
                         'diag: dropped-flag pursuit dump chunk tag'),
        ])
        if overlay_vertex_max_capped > 0:
            overlay_fields.append(ScratchField(
                'drop_dist', drop_off + 0x4C,
                2 * overlay_vertex_max_capped * 4,
                'drop: per-drop BFS hop field rooted at flag_drop_node (rows 0/1)',
            ))


