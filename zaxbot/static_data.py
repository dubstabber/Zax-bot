"""Static data packing for the .zaxbot section."""

import math
import struct


PROMPT_DM = b'Bot: 1=spawn  R=snap\x00'
PROMPT_CTF = b'Bot team: 1=Blue 2=Red\x00'
PROMPT_SK = b'SK bot: 1=spawn  R=snap\x00'


DUMP_TAGS = (
    ('tag_snap_marker', 'snap'),
    ('tag_part', 'part[X]'),
    ('tag_char', 'char[X]'),
    ('tag_worldmgr', 'worldmgr'),
    ('tag_dpmgr', 'dpmgr'),
    ('tag_idx_nbhd', 'idx_nbhd'),
    ('tag_stats', 'stats[X]'),
    ('tag_cstr', 'cstr[X]'),
    ('tag_mgr_root', 'mgr_root'),
    ('tag_session', 'session'),
    ('tag_charptr', 'charptr'),
    ('tag_ai_fire', 'ai_fire'),
    ('tag_ai_pos',  'ai_pos'),
    ('tag_weapon_info', 'weapon_info'),
    ('tag_host_weapon', 'host_weapon'),
    ('tag_pc2_weapon',  'pc2_weapon'),
    ('tag_host_wpn_bytes', 'host_wpn_b'),
    ('tag_pc2_wpn_bytes',  'pc2_wpn_b'),
    ('tag_ai_move', 'ai_move'),
    ('tag_hazard',  'hazard'),
    ('tag_wp_diag', 'wp_diag'),
    ('tag_wp_lv',   'wp_lv'),
    ('tag_wp_lay',  'wp_lay'),
    ('tag_wp_map',  'wp_map'),
    ('tag_plasma_diag', 'plasma'),
    ('tag_pheat', 'pheat'),
    # Door-state diagnostics (layout fields exist only on door-enabled builds;
    # the writer loop skips tags whose field is absent).
    ('tag_door_cnt', 'door_cnt'),
    ('tag_door_blk', 'door_blk'),
    ('tag_door_ent', 'door_ent'),
    ('tag_door_dyn', 'door_dyn'),
    ('tag_edge_door', 'edge_door'),
    ('tag_edge_pass', 'edge_pass'),
    # Routing-decision state (goal/carry/missing-policy/suspend/epoch) for
    # diagnosing per-bot route commitment.
    ('tag_rstate', 'rstate'),
    # Switch tables (count/pairs/live table prefix) for switch detection.
    ('tag_switch', 'switch'),
)


def pack_tag(text, tag_len):
    data = text.encode('ascii')
    assert len(data) <= tag_len, f'tag too long: {text!r}'
    return data + b'\x00' * (tag_len - len(data))


def write_bot_name_tables(section, scratch_off, layout, names, wide_slot_size, ascii_slot_size):
    """Write parallel UTF-16LE and ASCII bot-name tables into scratch."""
    for idx, name in enumerate(names):
        wide = name.encode('utf-16-le') + b'\x00\x00'
        assert len(wide) <= wide_slot_size, (
            f'bot name {name!r} too long ({len(wide)} bytes; max {wide_slot_size})'
        )
        slot_off = scratch_off + layout.off('bot_names') + idx * wide_slot_size
        section[slot_off:slot_off + len(wide)] = wide

        ascii_bytes = name.encode('ascii') + b'\x00'
        assert len(ascii_bytes) <= ascii_slot_size, (
            f'bot name {name!r} too long for ASCII slot '
            f'({len(ascii_bytes)} bytes; max {ascii_slot_size})'
        )
        ascii_off = scratch_off + layout.off('bot_names_ascii') + idx * ascii_slot_size
        section[ascii_off:ascii_off + len(ascii_bytes)] = ascii_bytes


def write_bot_color_table(section, scratch_off, layout, colors):
    """Pack (color1, color2) dword pairs into scratch parallel to bot_names."""
    field = layout.field('bot_colors')
    packed = b''.join(struct.pack('<II', c1, c2) for c1, c2 in colors)
    assert len(packed) <= field.size, (
        f'BOT_COLORS does not fit scratch field: {len(packed)} > {field.size}'
    )
    layout.write(section, scratch_off, 'bot_colors', packed)


def write_portal_static_table(section, scratch_off, layout, portal_maps, map_name_slot):
    """Pack build-time portal map records and point coordinates into scratch."""
    if not layout.has_field('portal_static_map_count'):
        return

    portal_maps = tuple(portal_maps or ())
    if not layout.has_field('portal_static_maps') and portal_maps:
        raise ValueError('portal map data present but layout has no portal_static_maps field')
    if not layout.has_field('portal_static_points') and any(points for _, points in portal_maps):
        raise ValueError('portal point data present but layout has no portal_static_points field')

    layout.write(section, scratch_off, 'portal_count', struct.pack('<I', 0))
    if layout.has_field('portal_table'):
        layout.write(section, scratch_off, 'portal_table', b'\x00' * layout.field('portal_table').size)

    map_capacity = 0
    point_capacity = 0
    if layout.has_field('portal_static_maps'):
        map_capacity = layout.field('portal_static_maps').size // (map_name_slot + 8)
    if layout.has_field('portal_static_points'):
        point_capacity = layout.field('portal_static_points').size // 8

    if len(portal_maps) > map_capacity:
        raise ValueError(
            f'portal map table has {len(portal_maps)} rows but scratch holds {map_capacity}'
        )

    total_points = sum(len(points) for _, points in portal_maps)
    if total_points > point_capacity:
        raise ValueError(
            f'portal point table has {total_points} rows but scratch holds {point_capacity}'
        )

    map_stride = map_name_slot + 8
    packed_maps = bytearray(map_capacity * map_stride)
    packed_points = bytearray(point_capacity * 8)
    point_index = 0
    for map_idx, (map_name, points) in enumerate(portal_maps):
        name_bytes = map_name.encode('latin1')
        if b'\x00' in name_bytes:
            raise ValueError(f'portal map name contains NUL: {map_name!r}')
        if len(name_bytes) + 1 > map_name_slot:
            raise ValueError(
                f'portal map name too long for {map_name_slot}-byte slot: {map_name!r}'
            )
        rec_off = map_idx * map_stride
        packed_maps[rec_off:rec_off + len(name_bytes)] = name_bytes
        count_off = rec_off + map_name_slot
        packed_maps[count_off:count_off + 8] = struct.pack('<II', len(points), point_index)
        for x, y in points:
            struct.pack_into('<ff', packed_points, point_index * 8, float(x), float(y))
            point_index += 1

    layout.write(section, scratch_off, 'portal_static_map_count',
                 struct.pack('<I', len(portal_maps)))
    layout.write(section, scratch_off, 'portal_static_point_count',
                 struct.pack('<I', total_points))
    if layout.has_field('portal_static_maps'):
        layout.write(section, scratch_off, 'portal_static_maps', bytes(packed_maps))
    if layout.has_field('portal_static_points'):
        layout.write(section, scratch_off, 'portal_static_points', bytes(packed_points))


def write_flag_static_table(section, scratch_off, layout, flag_maps, map_name_slot):
    """Pack build-time CTF flag map records and point coordinates into scratch.

    Mirror of ``write_portal_static_table`` for the flag overlay block.
    """
    if not layout.has_field('flag_static_map_count'):
        return

    flag_maps = tuple(flag_maps or ())
    if not layout.has_field('flag_static_maps') and flag_maps:
        raise ValueError('flag map data present but layout has no flag_static_maps field')
    if not layout.has_field('flag_static_points') and any(points for _, points in flag_maps):
        raise ValueError('flag point data present but layout has no flag_static_points field')

    layout.write(section, scratch_off, 'flag_count', struct.pack('<I', 0))
    if layout.has_field('flag_table'):
        layout.write(section, scratch_off, 'flag_table', b'\x00' * layout.field('flag_table').size)
    if layout.has_field('flag_entity'):
        layout.write(section, scratch_off, 'flag_entity', b'\x00' * layout.field('flag_entity').size)
    if layout.has_field('flag_present'):
        layout.write(section, scratch_off, 'flag_present', b'\x00' * layout.field('flag_present').size)

    map_capacity = 0
    point_capacity = 0
    if layout.has_field('flag_static_maps'):
        map_capacity = layout.field('flag_static_maps').size // (map_name_slot + 8)
    if layout.has_field('flag_static_points'):
        point_capacity = layout.field('flag_static_points').size // 8

    if len(flag_maps) > map_capacity:
        raise ValueError(
            f'flag map table has {len(flag_maps)} rows but scratch holds {map_capacity}'
        )

    total_points = sum(len(points) for _, points in flag_maps)
    if total_points > point_capacity:
        raise ValueError(
            f'flag point table has {total_points} rows but scratch holds {point_capacity}'
        )

    map_stride = map_name_slot + 8
    packed_maps = bytearray(map_capacity * map_stride)
    packed_points = bytearray(point_capacity * 8)
    # Parallel team tag (DWORD per point: 0=Blue, 1=Red) so the runtime can map a
    # bot's own team to its HOME base vs the ENEMY base. flag file order is not a
    # reliable Red/Blue ordering, hence the explicit tag.
    packed_team = bytearray(point_capacity * 4)
    point_index = 0
    for map_idx, (map_name, points) in enumerate(flag_maps):
        name_bytes = map_name.encode('latin1')
        if b'\x00' in name_bytes:
            raise ValueError(f'flag map name contains NUL: {map_name!r}')
        if len(name_bytes) + 1 > map_name_slot:
            raise ValueError(
                f'flag map name too long for {map_name_slot}-byte slot: {map_name!r}'
            )
        rec_off = map_idx * map_stride
        packed_maps[rec_off:rec_off + len(name_bytes)] = name_bytes
        count_off = rec_off + map_name_slot
        packed_maps[count_off:count_off + 8] = struct.pack('<II', len(points), point_index)
        for x, y, team in points:
            struct.pack_into('<ff', packed_points, point_index * 8, float(x), float(y))
            struct.pack_into('<I', packed_team, point_index * 4, int(team) & 0xFFFFFFFF)
            point_index += 1

    layout.write(section, scratch_off, 'flag_static_map_count',
                 struct.pack('<I', len(flag_maps)))
    layout.write(section, scratch_off, 'flag_static_point_count',
                 struct.pack('<I', total_points))
    if layout.has_field('flag_static_maps'):
        layout.write(section, scratch_off, 'flag_static_maps', bytes(packed_maps))
    if layout.has_field('flag_static_points'):
        layout.write(section, scratch_off, 'flag_static_points', bytes(packed_points))
    if layout.has_field('flag_static_team'):
        layout.write(section, scratch_off, 'flag_static_team', bytes(packed_team))


def write_door_static_table(section, scratch_off, layout, door_maps, map_name_slot):
    """Pack build-time door map records, point coordinates, per-door flags and
    bot-usable opener records into scratch.

    ``door_maps`` is a sequence of ``door_data.MapDoorData``. Each map record
    is ``name[slot] | point_count u32 | point_first u32 | opener_count u32 |
    opener_first u32``. Also zeroes the live tables and seeds the per-bot
    wedge-door latch to -1.
    """
    if not layout.has_field('door_static_map_count'):
        return

    door_maps = tuple(door_maps or ())
    if not layout.has_field('door_static_maps') and door_maps:
        raise ValueError('door map data present but layout has no door_static_maps field')
    if not layout.has_field('door_static_points') and any(m.doors for m in door_maps):
        raise ValueError('door point data present but layout has no door_static_points field')

    layout.write(section, scratch_off, 'door_count', struct.pack('<I', 0))
    if layout.has_field('door_table'):
        layout.write(section, scratch_off, 'door_table', b'\x00' * layout.field('door_table').size)
    if layout.has_field('door_blocked'):
        layout.write(section, scratch_off, 'door_blocked', b'\x00' * layout.field('door_blocked').size)
    if layout.has_field('door_entity'):
        layout.write(section, scratch_off, 'door_entity', b'\x00' * layout.field('door_entity').size)
    if layout.has_field('door_flags'):
        layout.write(section, scratch_off, 'door_flags', b'\x00' * layout.field('door_flags').size)
    if layout.has_field('door_opener_count'):
        layout.write(section, scratch_off, 'door_opener_count', struct.pack('<I', 0))
    if layout.has_field('door_opener'):
        layout.write(section, scratch_off, 'door_opener', b'\x00' * layout.field('door_opener').size)
    if layout.has_field('edge_pass'):
        # 0x0F = traversable both ways for both teams (safe default until
        # build_edge_doors runs).
        layout.write(section, scratch_off, 'edge_pass', b'\x0F' * layout.field('edge_pass').size)
    if layout.has_field('route_block_door'):
        # -1 = "no door latched to this bot's failed-edge marker".
        field = layout.field('route_block_door')
        layout.write(section, scratch_off, 'route_block_door', b'\xFF' * field.size)
    if layout.has_field('edge_door'):
        # -1 = "no door near this graph edge" (build_edge_doors re-inits per match).
        layout.write(section, scratch_off, 'edge_door', b'\xFF' * layout.field('edge_door').size)
    if layout.has_field('flag_dist_open'):
        # -1 = unreachable, same sentinel as flag_dist.
        layout.write(section, scratch_off, 'flag_dist_open', b'\xFF' * layout.field('flag_dist_open').size)

    map_stride = map_name_slot + 16
    map_capacity = 0
    point_capacity = 0
    opener_capacity = 0
    if layout.has_field('door_static_maps'):
        map_capacity = layout.field('door_static_maps').size // map_stride
    if layout.has_field('door_static_points'):
        point_capacity = layout.field('door_static_points').size // 8
    if layout.has_field('door_static_openers'):
        opener_capacity = layout.field('door_static_openers').size // 16

    if len(door_maps) > map_capacity:
        raise ValueError(
            f'door map table has {len(door_maps)} rows but scratch holds {map_capacity}'
        )

    total_points = sum(len(m.doors) for m in door_maps)
    if total_points > point_capacity:
        raise ValueError(
            f'door point table has {total_points} rows but scratch holds {point_capacity}'
        )
    total_openers = sum(len(m.openers) for m in door_maps)
    if total_openers > opener_capacity:
        raise ValueError(
            f'door opener table has {total_openers} rows but scratch holds {opener_capacity}'
        )

    packed_maps = bytearray(map_capacity * map_stride)
    packed_points = bytearray(point_capacity * 8)
    packed_flags = bytearray(point_capacity)
    packed_openers = bytearray(opener_capacity * 16)
    point_index = 0
    opener_index = 0
    for map_idx, m in enumerate(door_maps):
        name_bytes = m.map_name.encode('latin1')
        if b'\x00' in name_bytes:
            raise ValueError(f'door map name contains NUL: {m.map_name!r}')
        if len(name_bytes) + 1 > map_name_slot:
            raise ValueError(
                f'door map name too long for {map_name_slot}-byte slot: {m.map_name!r}'
            )
        if len(m.flags) != len(m.doors):
            raise ValueError(f'door flags not parallel to doors for {m.map_name!r}')
        rec_off = map_idx * map_stride
        packed_maps[rec_off:rec_off + len(name_bytes)] = name_bytes
        struct.pack_into('<IIII', packed_maps, rec_off + map_name_slot,
                         len(m.doors), point_index, len(m.openers), opener_index)
        for (x, y), fl in zip(m.doors, m.flags):
            struct.pack_into('<ff', packed_points, point_index * 8, float(x), float(y))
            packed_flags[point_index] = fl & 0xFF
            point_index += 1
        for (ox, oy, di, mask) in m.openers:
            if not (0 <= di < len(m.doors)):
                raise ValueError(f'opener door index {di} out of range for {m.map_name!r}')
            struct.pack_into('<ffII', packed_openers, opener_index * 16,
                             float(ox), float(oy), di, mask & 0x3)
            opener_index += 1

    layout.write(section, scratch_off, 'door_static_map_count',
                 struct.pack('<I', len(door_maps)))
    layout.write(section, scratch_off, 'door_static_point_count',
                 struct.pack('<I', total_points))
    if layout.has_field('door_static_maps'):
        layout.write(section, scratch_off, 'door_static_maps', bytes(packed_maps))
    if layout.has_field('door_static_points'):
        layout.write(section, scratch_off, 'door_static_points', bytes(packed_points))
    if layout.has_field('door_static_flags'):
        layout.write(section, scratch_off, 'door_static_flags', bytes(packed_flags))
    if layout.has_field('door_static_openers'):
        layout.write(section, scratch_off, 'door_static_openers', bytes(packed_openers))


def write_switch_static_table(section, scratch_off, layout, switch_maps, map_name_slot):
    """Pack build-time switch map records, centers, class bytes and
    (switch, door) pair records into scratch.

    ``switch_maps`` is a sequence of ``door_data.MapDoorData`` (only maps with
    switches). Each map record is ``name[slot] | switch_count u32 |
    switch_first u32 | pair_count u32 | pair_first u32``. Pairs pack as
    ``switch_idx | door_idx << 16`` — door indices reference the SAME map's
    door_table order (both loaders copy in parse order). Also zeroes the live
    tables.
    """
    if not layout.has_field('switch_static_map_count'):
        return

    switch_maps = tuple(switch_maps or ())
    if not layout.has_field('switch_static_maps') and switch_maps:
        raise ValueError('switch map data present but layout has no switch_static_maps field')

    layout.write(section, scratch_off, 'switch_count', struct.pack('<I', 0))
    layout.write(section, scratch_off, 'switch_pair_count', struct.pack('<I', 0))
    if layout.has_field('switch_table'):
        layout.write(section, scratch_off, 'switch_table', b'\x00' * layout.field('switch_table').size)
    if layout.has_field('switch_flags'):
        layout.write(section, scratch_off, 'switch_flags', b'\x00' * layout.field('switch_flags').size)
    if layout.has_field('switch_pairs'):
        layout.write(section, scratch_off, 'switch_pairs', b'\x00' * layout.field('switch_pairs').size)

    map_stride = map_name_slot + 16
    map_capacity = 0
    point_capacity = 0
    pair_capacity = 0
    if layout.has_field('switch_static_maps'):
        map_capacity = layout.field('switch_static_maps').size // map_stride
    if layout.has_field('switch_static_points'):
        point_capacity = layout.field('switch_static_points').size // 8
    if layout.has_field('switch_static_pairs'):
        pair_capacity = layout.field('switch_static_pairs').size // 4

    if len(switch_maps) > map_capacity:
        raise ValueError(
            f'switch map table has {len(switch_maps)} rows but scratch holds {map_capacity}'
        )
    total_points = sum(len(m.switches) for m in switch_maps)
    if total_points > point_capacity:
        raise ValueError(
            f'switch point table has {total_points} rows but scratch holds {point_capacity}'
        )
    total_pairs = sum(len(m.switch_pairs) for m in switch_maps)
    if total_pairs > pair_capacity:
        raise ValueError(
            f'switch pair table has {total_pairs} rows but scratch holds {pair_capacity}'
        )

    packed_maps = bytearray(map_capacity * map_stride)
    packed_points = bytearray(point_capacity * 8)
    packed_flags = bytearray(point_capacity)
    packed_pairs = bytearray(pair_capacity * 4)
    point_index = 0
    pair_index = 0
    for map_idx, m in enumerate(switch_maps):
        name_bytes = m.map_name.encode('latin1')
        if b'\x00' in name_bytes:
            raise ValueError(f'switch map name contains NUL: {m.map_name!r}')
        if len(name_bytes) + 1 > map_name_slot:
            raise ValueError(
                f'switch map name too long for {map_name_slot}-byte slot: {m.map_name!r}'
            )
        rec_off = map_idx * map_stride
        packed_maps[rec_off:rec_off + len(name_bytes)] = name_bytes
        struct.pack_into('<IIII', packed_maps, rec_off + map_name_slot,
                         len(m.switches), point_index,
                         len(m.switch_pairs), pair_index)
        for (x, y, fl) in m.switches:
            struct.pack_into('<ff', packed_points, point_index * 8, float(x), float(y))
            packed_flags[point_index] = fl & 0xFF
            point_index += 1
        for (si, di) in m.switch_pairs:
            if not (0 <= si < len(m.switches)):
                raise ValueError(f'pair switch index {si} out of range for {m.map_name!r}')
            if not (0 <= di < max(1, len(m.doors))):
                raise ValueError(f'pair door index {di} out of range for {m.map_name!r}')
            struct.pack_into('<I', packed_pairs, pair_index * 4,
                             (si & 0xFFFF) | ((di & 0xFFFF) << 16))
            pair_index += 1

    layout.write(section, scratch_off, 'switch_static_map_count',
                 struct.pack('<I', len(switch_maps)))
    layout.write(section, scratch_off, 'switch_static_point_count',
                 struct.pack('<I', total_points))
    if layout.has_field('switch_static_maps'):
        layout.write(section, scratch_off, 'switch_static_maps', bytes(packed_maps))
    if layout.has_field('switch_static_points'):
        layout.write(section, scratch_off, 'switch_static_points', bytes(packed_points))
    if layout.has_field('switch_static_flags'):
        layout.write(section, scratch_off, 'switch_static_flags', bytes(packed_flags))
    if layout.has_field('switch_static_pairs'):
        layout.write(section, scratch_off, 'switch_static_pairs', bytes(packed_pairs))


_FORCE_MODE_TABLE = {None: 0xFFFFFFFF, 'dm': 0, 'ctf': 1, 'sk': 2}


def write_static_scratch_data(
    section,
    scratch_off,
    layout,
    *,
    dump_filename,
    dump_msg,
    step_filename,
    full_msg,
    dump_magic,
    dump_tag_len,
    bot_names,
    name_slot_size,
    name_slot_ascii,
    bot_colors,
    prompt_dm_va,
    prompt_ctf_va,
    prompt_sk_va,
    fire_range_sq=90000.0,
    projectile_speed=600.0,
    speed_scale=1.0 / 60.0,
    muzzle_offset=20.0,
    lead_probability=0.5,
    weapon_speeds=(),
    force_bot_item_name=None,
    force_bot_ammo_names=(),
    force_bot_ammo_slot_size=0,
    force_mode=None,
    movement_enabled=True,
    wander_target_radius=600.0,
    wander_target_timeout_frames=600,
    stuck_frames_threshold=30,
    stuck_delta_sq=4.0,
    item_attractor_radius_sq=40000.0,
    item_attractor_weight=0.7,
    item_scan_interval_frames=30,
    hazard_repulsion_radius_sq=90000.0,
    hazard_repulsion_weight=2.0,
    hazard_default_radius_sq=90000.0,
    bot_move_speed=3.0,
    hazard_flee_frames=120,
    wp_follow_enabled=True,
    wp_reached_radius_sq=4096.0,
    wp_edge_lookahead=0.15,
    wp_edge_follow_enabled=True,
    wp_progress_timeout_frames=150,
    wp_stuck_reached_radius_sq=16384.0,
    wp_slide_turn_step_deg=30.0,
    overlay_enabled=False,
    overlay_waypoints=(),
    overlay_edges=(),
    overlay_vertex_color=(255, 255, 0, 255),
    overlay_edge_color=(0, 255, 0, 255),
    overlay_selected_color=(255, 0, 255, 255),
    overlay_pickup_color=(255, 128, 0, 255),
    overlay_portal_color=(255, 64, 255, 255),
    overlay_flag_color=(0, 0, 255, 255),
    ctf_flag_entity_match_radius_sq=256.0,
    ctf_flag_home_force_tick_radius_sq=4096.0,
    overlay_vertex_radius=8.0,
    overlay_vertex_aspect=1.0,
    overlay_cull_min_x=-96.0,
    overlay_cull_max_x=736.0,
    overlay_cull_min_y=-96.0,
    overlay_cull_max_y=576.0,
    pickup_register_enabled=True,
    pickup_divert_enabled=True,
    pickup_divert_radius_sq=62500.0,
    pickup_reached_radius_sq=576.0,
    pickup_cooldown_frames=180,
    pickup_divert_timeout_frames=150,
    pickup_divert_avoid_damage=True,
    wp_snap_radius_sq=576.0,
    wp_dir_name=b'waypoints',
    wp_file_suffix=b'.zwpt',
    lava_avoid_enabled=True,
    lava_heat_threshold=128,
    lava_lookahead_px=48.0,
    lava_sweep_step_deg=30.0,
    lava_flee_enabled=True,
    lava_flee_frames=15,
    portal_maps=(),
    portal_map_name_slot=0,
    flag_maps=(),
    flag_map_name_slot=0,
    door_maps=(),
    door_map_name_slot=0,
    overlay_door_color=(255, 128, 255, 255),
    door_entity_match_radius_sq=576.0,
    door_wedge_match_radius_sq=9216.0,
    door_edge_radius_sq=1600.0,
    switch_map_name_slot=0,
    overlay_switch_color=(128, 255, 255, 255),
):
    # Digit-validation per mode. DM and SK are both free-for-all (only '1' is
    # meaningful — "spawn one bot"); CTF is the only team mode and accepts
    # '1'..'2' for Blue/Red. The map's MaxPlayers (clamped to MAX_BOT_SLOTS)
    # caps total bots in all three modes — pressing '1' repeatedly adds more.
    max_for_mode = struct.pack('<III', 1, 2, 1)
    prompts_table = struct.pack('<III', prompt_dm_va, prompt_ctf_va, prompt_sk_va)
    forced_mode_value = _FORCE_MODE_TABLE.get(force_mode)
    if forced_mode_value is None:
        raise ValueError(f'FORCE_MODE must be None / dm / ctf / sk, got {force_mode!r}')
    layout.write(section, scratch_off, 'forced_mode', struct.pack('<I', forced_mode_value))

    layout.write(section, scratch_off, 'msg', dump_msg)
    layout.write(section, scratch_off, 'fn', dump_filename)
    layout.write(section, scratch_off, 'stepfn', step_filename)
    layout.write(section, scratch_off, 'active_bot_slot', struct.pack('<I', 0xFFFFFFFF))
    layout.write(section, scratch_off, 'msg_full', full_msg)
    layout.write(section, scratch_off, 'prompt_dm', PROMPT_DM)
    layout.write(section, scratch_off, 'prompt_ctf', PROMPT_CTF)
    layout.write(section, scratch_off, 'prompt_sk', PROMPT_SK)
    layout.write(section, scratch_off, 'max_for_mode', max_for_mode)
    layout.write(section, scratch_off, 'prompts_table', prompts_table)
    layout.write(section, scratch_off, 'fire_range_sq', struct.pack('<f', fire_range_sq))
    # proj_speed is what apply_lead reads each frame; compute_proj_speed
    # rewrites it per fire call. default_proj_speed is the immutable fallback
    # copied in if both the WEAPON_SPEEDS override AND the dynamic def-read
    # paths bail out (NULL weapon, NULL def, etc.). speed_scale is the
    # multiplier applied to the engine's raw pixels/sec field when the
    # def-read path takes over (see hook/weapon_speed.py:cps_try_def_field).
    layout.write(section, scratch_off, 'proj_speed', struct.pack('<f', projectile_speed))
    layout.write(section, scratch_off, 'default_proj_speed', struct.pack('<f', projectile_speed))
    layout.write(section, scratch_off, 'speed_scale', struct.pack('<f', speed_scale))
    layout.write(section, scratch_off, 'muzzle_offset', struct.pack('<f', muzzle_offset))
    layout.write(section, scratch_off, 'muzzle_sq', struct.pack('<f', muzzle_offset * muzzle_offset))
    # Clamp to [0, 100] and round to nearest. 0 ⇒ never lead, 100 ⇒ always.
    lead_threshold = max(0, min(100, int(round(lead_probability * 100))))
    layout.write(section, scratch_off, 'lead_threshold', struct.pack('<I', lead_threshold))

    # Pack WEAPON_SPEEDS into the runtime table. Each entry is
    # (item_def_va u32, speed float). Terminated by a (0, 0.0) sentinel; the
    # asm scan stops on the first zero item_def_va. Empty list -> first dword
    # is 0 -> no matches.
    weapon_field = layout.field('weapon_table')
    max_entries = weapon_field.size // 8 - 1  # one slot reserved for sentinel
    if len(weapon_speeds) > max_entries:
        raise ValueError(
            f'WEAPON_SPEEDS has {len(weapon_speeds)} rows but scratch holds '
            f'{max_entries} (raise cfg.WEAPON_SPEEDS_MAX)'
        )
    packed = b''.join(struct.pack('<If', item_def_va, speed)
                       for item_def_va, speed in weapon_speeds)
    packed += struct.pack('<If', 0, 0.0)  # sentinel
    layout.write(section, scratch_off, 'weapon_table', packed)

    # FORCE_BOT_ITEM_NAME — empty/NUL first byte means "no override".
    force_field = layout.field('force_bot_item_name')
    force_name = b'\x00' if force_bot_item_name is None else force_bot_item_name
    if len(force_name) > force_field.size:
        raise ValueError(
            f'FORCE_BOT_ITEM_NAME is too long for scratch field: '
            f'{len(force_name)} > {force_field.size}'
        )
    layout.write(section, scratch_off, 'force_bot_item_name', force_name)

    # FORCE_BOT_AMMO_NAMES — packed flat into the slot table; the count field
    # tells the asm loop how many slots are populated. Only emit when the
    # layout actually allocated the ammo fields (callers that don't need them
    # build the layout with force_bot_ammo_max=0 and the fields are absent).
    if layout.has_field('force_bot_ammo_count'):
        ammo_field = layout.field('force_bot_ammo_names')
        if force_bot_ammo_slot_size <= 0:
            raise ValueError(
                'force_bot_ammo_slot_size must be > 0 when force_bot_ammo_names is allocated'
            )
        max_slots = ammo_field.size // force_bot_ammo_slot_size
        if len(force_bot_ammo_names) > max_slots:
            raise ValueError(
                f'force_bot_ammo_names has {len(force_bot_ammo_names)} entries '
                f'but the scratch table only holds {max_slots}'
            )
        packed = bytearray(ammo_field.size)
        for idx, name in enumerate(force_bot_ammo_names):
            if len(name) > force_bot_ammo_slot_size:
                raise ValueError(
                    f'force_bot_ammo_names[{idx}] is {len(name)} bytes; max '
                    f'{force_bot_ammo_slot_size}'
                )
            slot_off = idx * force_bot_ammo_slot_size
            packed[slot_off:slot_off + len(name)] = name
        layout.write(section, scratch_off, 'force_bot_ammo_names', bytes(packed))
        layout.write(
            section,
            scratch_off,
            'force_bot_ammo_count',
            struct.pack('<I', len(force_bot_ammo_names)),
        )

    # --- Bot movement knobs (DM-only first pass) --------------------------
    # All written here so the per-frame movement detour can read them as
    # plain DWORDs without re-encoding constants every build.
    layout.write(section, scratch_off, 'movement_enabled',
                 struct.pack('<I', 1 if movement_enabled else 0))
    layout.write(section, scratch_off, 'wander_target_radius',
                 struct.pack('<f', wander_target_radius))
    layout.write(section, scratch_off, 'wander_target_timeout',
                 struct.pack('<I', wander_target_timeout_frames))
    layout.write(section, scratch_off, 'stuck_frames_threshold',
                 struct.pack('<I', stuck_frames_threshold))
    layout.write(section, scratch_off, 'stuck_delta_sq',
                 struct.pack('<f', stuck_delta_sq))
    layout.write(section, scratch_off, 'item_attractor_radius_sq',
                 struct.pack('<f', item_attractor_radius_sq))
    layout.write(section, scratch_off, 'item_attractor_weight',
                 struct.pack('<f', item_attractor_weight))
    layout.write(section, scratch_off, 'item_scan_interval',
                 struct.pack('<I', item_scan_interval_frames))
    layout.write(section, scratch_off, 'hazard_repulsion_radius_sq',
                 struct.pack('<f', hazard_repulsion_radius_sq))
    layout.write(section, scratch_off, 'hazard_repulsion_weight',
                 struct.pack('<f', hazard_repulsion_weight))
    layout.write(section, scratch_off, 'hazard_default_radius_sq',
                 struct.pack('<f', hazard_default_radius_sq))
    layout.write(section, scratch_off, 'bot_move_speed',
                 struct.pack('<f', bot_move_speed))
    layout.write(section, scratch_off, 'hazard_flee_frames',
                 struct.pack('<I', hazard_flee_frames))
    layout.write(section, scratch_off, 'wp_follow_enabled',
                 struct.pack('<I', 1 if wp_follow_enabled else 0))
    layout.write(section, scratch_off, 'wp_reached_radius_sq',
                 struct.pack('<f', wp_reached_radius_sq))
    layout.write(section, scratch_off, 'wp_edge_lookahead',
                 struct.pack('<f', wp_edge_lookahead))
    layout.write(section, scratch_off, 'wp_edge_follow_enabled',
                 struct.pack('<I', 1 if wp_edge_follow_enabled else 0))
    layout.write(section, scratch_off, 'wp_progress_timeout',
                 struct.pack('<I', wp_progress_timeout_frames))
    # Reuse the dormant wp_relocate_frames slot for the stuck-near-node arrival
    # radius. Keeping the field name avoids shifting the established scratch
    # layout while giving the movement detour a second radius for wedged bots.
    layout.write(section, scratch_off, 'wp_relocate_frames',
                 struct.pack('<f', wp_stuck_reached_radius_sq))
    # Wall-slide angle step, packed as radians for the movement detour's fadd.
    layout.write(section, scratch_off, 'wp_slide_turn_step',
                 struct.pack('<f', math.radians(wp_slide_turn_step_deg)))
    # Proactive lava-avoidance knobs.
    layout.write(section, scratch_off, 'lava_avoid_enabled',
                 struct.pack('<I', 1 if lava_avoid_enabled else 0))
    layout.write(section, scratch_off, 'lava_heat_threshold',
                 struct.pack('<I', lava_heat_threshold))
    layout.write(section, scratch_off, 'lava_lookahead_px',
                 struct.pack('<f', lava_lookahead_px))
    layout.write(section, scratch_off, 'lava_sweep_step',
                 struct.pack('<f', math.radians(lava_sweep_step_deg)))
    layout.write(section, scratch_off, 'lava_flee_enabled',
                 struct.pack('<I', 1 if lava_flee_enabled else 0))
    layout.write(section, scratch_off, 'lava_flee_frames',
                 struct.pack('<I', lava_flee_frames))

    # --- Waypoint overlay --------------------------------------------------
    # Pack vertex / edge tables into scratch. Both colors are stored RAW
    # (RGBA byte values padded with 0s); the runtime detour re-runs
    # sub_53F010 each frame to compute the palette index, but the byte
    # layout is convenient for tweaking colors from a hex dump.
    layout.write(section, scratch_off, 'overlay_enabled',
                 struct.pack('<I', 1 if overlay_enabled else 0))
    layout.write(section, scratch_off, 'overlay_vertex_radius',
                 struct.pack('<f', overlay_vertex_radius))
    layout.write(section, scratch_off, 'overlay_vertex_aspect',
                 struct.pack('<f', overlay_vertex_aspect))
    if layout.has_field('overlay_cull_min_x'):
        layout.write(section, scratch_off, 'overlay_cull_min_x',
                     struct.pack('<f', overlay_cull_min_x))
        layout.write(section, scratch_off, 'overlay_cull_max_x',
                     struct.pack('<f', overlay_cull_max_x))
        layout.write(section, scratch_off, 'overlay_cull_min_y',
                     struct.pack('<f', overlay_cull_min_y))
        layout.write(section, scratch_off, 'overlay_cull_max_y',
                     struct.pack('<f', overlay_cull_max_y))
    layout.write(section, scratch_off, 'overlay_vertex_count',
                 struct.pack('<I', len(overlay_waypoints)))
    layout.write(section, scratch_off, 'overlay_edge_count',
                 struct.pack('<I', len(overlay_edges)))
    # Initial color bytes (B, G, R, A) — sub_568BG order. Runtime
    # sub_53F010 overwrites this every frame; the values here are just a
    # seed so a freshly-loaded binary has a sane CColor in case the first
    # render happens before the detour fires.
    def _pack_color(rgba):
        r, g, b, a = rgba
        return bytes((b & 0xFF, g & 0xFF, r & 0xFF, a & 0xFF)) + b'\x00' * 12
    layout.write(section, scratch_off, 'overlay_vertex_color',
                 _pack_color(overlay_vertex_color))
    layout.write(section, scratch_off, 'overlay_edge_color',
                 _pack_color(overlay_edge_color))
    layout.write(section, scratch_off, 'overlay_selected_color',
                 _pack_color(overlay_selected_color))
    if layout.has_field('overlay_pickup_color'):
        layout.write(section, scratch_off, 'overlay_pickup_color',
                     _pack_color(overlay_pickup_color))
    if layout.has_field('overlay_portal_color'):
        layout.write(section, scratch_off, 'overlay_portal_color',
                     _pack_color(overlay_portal_color))
    if layout.has_field('overlay_flag_color'):
        layout.write(section, scratch_off, 'overlay_flag_color',
                     _pack_color(overlay_flag_color))
    if layout.has_field('overlay_door_color'):
        layout.write(section, scratch_off, 'overlay_door_color',
                     _pack_color(overlay_door_color))
    if layout.has_field('door_match_radius_sq'):
        layout.write(section, scratch_off, 'door_match_radius_sq',
                     struct.pack('<f', door_entity_match_radius_sq))
        layout.write(section, scratch_off, 'door_wedge_radius_sq',
                     struct.pack('<f', door_wedge_match_radius_sq))
    if layout.has_field('door_edge_radius_sq'):
        layout.write(section, scratch_off, 'door_edge_radius_sq',
                     struct.pack('<f', door_edge_radius_sq))
    if layout.has_field('flag_entity_match_radius_sq'):
        layout.write(section, scratch_off, 'flag_entity_match_radius_sq',
                     struct.pack('<f', ctf_flag_entity_match_radius_sq))
    if layout.has_field('flag_home_tick_radius_sq'):
        layout.write(section, scratch_off, 'flag_home_tick_radius_sq',
                     struct.pack('<f', ctf_flag_home_force_tick_radius_sq))
    if layout.has_field('portal_static_map_count'):
        write_portal_static_table(
            section,
            scratch_off,
            layout,
            portal_maps,
            portal_map_name_slot,
        )
    if layout.has_field('flag_static_map_count'):
        write_flag_static_table(
            section,
            scratch_off,
            layout,
            flag_maps,
            flag_map_name_slot,
        )
    if layout.has_field('door_static_map_count'):
        # door_maps may include switch-only records (empty doors) since the
        # parse keeps every map with doors OR switches; the door tables only
        # take the door-carrying ones (keeps DOOR_STATIC_MAP_MAX at the door
        # census) while the switch tables take the switch-carrying ones.
        write_door_static_table(
            section,
            scratch_off,
            layout,
            tuple(m for m in door_maps if m.doors),
            door_map_name_slot,
        )
    if layout.has_field('overlay_switch_color'):
        layout.write(section, scratch_off, 'overlay_switch_color',
                     _pack_color(overlay_switch_color))
    if layout.has_field('switch_static_map_count'):
        write_switch_static_table(
            section,
            scratch_off,
            layout,
            tuple(m for m in door_maps if m.switches),
            switch_map_name_slot,
        )
    # Pickup self-registration master switch (per-frame CPickupAI detour).
    if layout.has_field('pickup_register_enabled'):
        layout.write(section, scratch_off, 'pickup_register_enabled',
                     struct.pack('<I', 1 if pickup_register_enabled else 0))
    # Stage-2 pickup-divert knobs (read by the movement detour's divert block).
    if layout.has_field('pickup_divert_enabled'):
        layout.write(section, scratch_off, 'pickup_divert_enabled',
                     struct.pack('<I', 1 if pickup_divert_enabled else 0))
        layout.write(section, scratch_off, 'pickup_divert_radius_sq',
                     struct.pack('<f', pickup_divert_radius_sq))
        layout.write(section, scratch_off, 'pickup_reached_radius_sq',
                     struct.pack('<f', pickup_reached_radius_sq))
        layout.write(section, scratch_off, 'pickup_cooldown_frames',
                     struct.pack('<I', pickup_cooldown_frames))
        layout.write(section, scratch_off, 'pickup_divert_timeout',
                     struct.pack('<I', pickup_divert_timeout_frames))
        layout.write(section, scratch_off, 'pickup_divert_avoid_damage',
                     struct.pack('<I', 1 if pickup_divert_avoid_damage else 0))
    # wp_selected_idx is "no selection" until the user picks one.
    layout.write(section, scratch_off, 'wp_selected_idx',
                 struct.pack('<I', 0xFFFFFFFF))
    layout.write(section, scratch_off, 'wp_snap_radius_sq',
                 struct.pack('<f', wp_snap_radius_sq))
    # Static-string scaffolding used by wp_save / wp_load. The dir name is
    # passed to CreateDirectoryA; prefix + suffix are concatenated with the
    # sanitized map name at runtime into wp_filename_buf.
    layout.write(section, scratch_off, 'wp_dir_static',
                 wp_dir_name + b'\x00')
    layout.write(section, scratch_off, 'wp_prefix_static',
                 wp_dir_name + b'/\x00')
    layout.write(section, scratch_off, 'wp_suffix_static',
                 wp_file_suffix + b'\x00')
    layout.write(section, scratch_off, 'wp_msg_saved',
                 b'[wp] saved\x00')
    layout.write(section, scratch_off, 'wp_msg_loaded',
                 b'[wp] loaded\x00')
    layout.write(section, scratch_off, 'wp_msg_nomap',
                 b'[wp] no map name\x00')
    layout.write(section, scratch_off, 'wp_msg_failed',
                 b'[wp] save/load failed\x00')
    if overlay_waypoints and layout.has_field('overlay_vertices'):
        packed_v = b''.join(struct.pack('<ff', float(x), float(y))
                            for x, y in overlay_waypoints)
        layout.write(section, scratch_off, 'overlay_vertices', packed_v)
    if overlay_edges and layout.has_field('overlay_edges'):
        packed_e = b''.join(struct.pack('<HH', int(i), int(j))
                            for i, j in overlay_edges)
        layout.write(section, scratch_off, 'overlay_edges', packed_e)

    # The dump header magic is written once; runtime code rewrites tag/src/len.
    layout.write(section, scratch_off, 'thdr', struct.pack('<I', dump_magic))

    for field_name, tag in DUMP_TAGS:
        if not layout.has_field(field_name):
            continue                     # door tags absent on doors-off builds
        layout.write(section, scratch_off, field_name, pack_tag(tag, dump_tag_len))

    write_bot_name_tables(
        section,
        scratch_off,
        layout,
        bot_names,
        name_slot_size,
        name_slot_ascii,
    )

    assert len(bot_colors) == len(bot_names), (
        f'BOT_COLORS ({len(bot_colors)}) must be parallel to BOT_NAMES ({len(bot_names)})'
    )
    write_bot_color_table(section, scratch_off, layout, bot_colors)
