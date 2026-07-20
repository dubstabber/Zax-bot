"""Per-feature build-time static tables packed into scratch: portal,
flag, door, switch, SK mineral/bin and filler-item map records."""

import struct


def write_portal_static_table(section, scratch_off, layout, portal_maps, map_name_slot,
                              portal_routes=()):
    """Pack build-time portal map records and point coordinates into scratch.

    ``portal_routes`` is the destination-carrying view from
    ``resolve_portal_routes`` (parallel to ``portal_maps`` by construction);
    when the routing layout fields exist, each point's resolved destination is
    packed into ``portal_static_dests`` / ``portal_static_hasdest`` at the
    SAME point index as its source in ``portal_static_points``."""
    if not layout.has_field('portal_static_map_count'):
        return

    portal_maps = tuple(portal_maps or ())
    portal_dests = {}
    for map_name, routes in tuple(portal_routes or ()):
        portal_dests[map_name] = tuple(dest for _src, dest in routes)
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
    packed_dests = bytearray(point_capacity * 8)
    packed_hasdest = bytearray(point_capacity * 4)
    point_index = 0
    for map_idx, (map_name, points) in enumerate(portal_maps):
        name_bytes = map_name.encode('latin1')
        if b'\x00' in name_bytes:
            raise ValueError(f'portal map name contains NUL: {map_name!r}')
        if len(name_bytes) + 1 > map_name_slot:
            raise ValueError(
                f'portal map name too long for {map_name_slot}-byte slot: {map_name!r}'
            )
        dests = portal_dests.get(map_name, ())
        if dests and len(dests) != len(points):
            raise ValueError(
                f'portal routes for {map_name!r} not parallel to points: '
                f'{len(dests)} dests vs {len(points)} sources'
            )
        rec_off = map_idx * map_stride
        packed_maps[rec_off:rec_off + len(name_bytes)] = name_bytes
        count_off = rec_off + map_name_slot
        packed_maps[count_off:count_off + 8] = struct.pack('<II', len(points), point_index)
        for pt_idx, (x, y) in enumerate(points):
            struct.pack_into('<ff', packed_points, point_index * 8, float(x), float(y))
            dest = dests[pt_idx] if pt_idx < len(dests) else None
            if dest is not None:
                struct.pack_into('<ff', packed_dests, point_index * 8,
                                 float(dest[0]), float(dest[1]))
                struct.pack_into('<I', packed_hasdest, point_index * 4, 1)
            point_index += 1

    layout.write(section, scratch_off, 'portal_static_map_count',
                 struct.pack('<I', len(portal_maps)))
    layout.write(section, scratch_off, 'portal_static_point_count',
                 struct.pack('<I', total_points))
    if layout.has_field('portal_static_maps'):
        layout.write(section, scratch_off, 'portal_static_maps', bytes(packed_maps))
    if layout.has_field('portal_static_points'):
        layout.write(section, scratch_off, 'portal_static_points', bytes(packed_points))
    if layout.has_field('portal_static_dests'):
        layout.write(section, scratch_off, 'portal_static_dests', bytes(packed_dests))
        layout.write(section, scratch_off, 'portal_static_hasdest', bytes(packed_hasdest))
    # Live routing tables start empty/unbound; load_portals + bind_portal_nodes
    # refill them per match.
    if layout.has_field('portal_dest_table'):
        layout.write(section, scratch_off, 'portal_dest_table',
                     b'\x00' * layout.field('portal_dest_table').size)
        layout.write(section, scratch_off, 'portal_has_dest',
                     b'\x00' * layout.field('portal_has_dest').size)
        layout.write(section, scratch_off, 'portal_node',
                     b'\xFF' * layout.field('portal_node').size)
        layout.write(section, scratch_off, 'portal_dest_node',
                     b'\xFF' * layout.field('portal_dest_node').size)
        layout.write(section, scratch_off, 'bot_portal_target',
                     b'\x00' * layout.field('bot_portal_target').size)
        layout.write(section, scratch_off, 'bot_portal_cd',
                     b'\x00' * layout.field('bot_portal_cd').size)
        layout.write(section, scratch_off, 'bot_pad_try',
                     b'\x00' * layout.field('bot_pad_try').size)
        layout.write(section, scratch_off, 'route_portal_hop', struct.pack('<I', 0))


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




def write_sk_static_table(section, scratch_off, layout, sk_maps, map_name_slot):
    """Pack build-time SK map records (mineral anchors + team-bound bins) and
    seed every live SK table for the runtime ``load_sk`` copy.

    ``sk_maps`` is a sequence of ``sk_data.MapSkData``. Each map record is
    ``name[slot] | mineral_count u32 | mineral_first u32 | bin_count u32 |
    bin_first u32``. Minerals pack as (x f32, y f32) in parse order; bins as
    (x f32, y f32, team u32) in TEAM order (the live table is indexed by team
    id, so load_sk scatters by the packed team field).
    """
    if not layout.has_field('sk_static_map_count'):
        return

    sk_maps = tuple(sk_maps or ())
    # Live tables start inert: routing disarmed, counts zero, node binds -1,
    # BFS rows unreachable, per-bot state clean. load_sk re-seeds per match.
    layout.write(section, scratch_off, 'sk_routing_active', struct.pack('<I', 0))
    layout.write(section, scratch_off, 'sk_mineral_count', struct.pack('<I', 0))
    layout.write(section, scratch_off, 'sk_bin_count', struct.pack('<I', 0))
    layout.write(section, scratch_off, 'sk_mineral_node',
                 b'\xFF' * layout.field('sk_mineral_node').size)
    layout.write(section, scratch_off, 'sk_bin_node',
                 b'\xFF' * layout.field('sk_bin_node').size)
    layout.write(section, scratch_off, 'sk_ore_dist',
                 b'\xFF' * layout.field('sk_ore_dist').size)
    layout.write(section, scratch_off, 'sk_bin_dist',
                 b'\xFF' * layout.field('sk_bin_dist').size)

    map_stride = map_name_slot + 16
    map_capacity = layout.field('sk_static_maps').size // map_stride
    mineral_capacity = layout.field('sk_static_minerals').size // 8
    bin_capacity = layout.field('sk_static_bins').size // 12

    if len(sk_maps) > map_capacity:
        raise ValueError(
            f'SK map table has {len(sk_maps)} rows but scratch holds {map_capacity}'
        )
    total_minerals = sum(len(m.minerals) for m in sk_maps)
    if total_minerals > mineral_capacity:
        raise ValueError(
            f'SK mineral table has {total_minerals} rows but scratch holds {mineral_capacity}'
        )
    total_bins = sum(len(m.bins) for m in sk_maps)
    if total_bins > bin_capacity:
        raise ValueError(
            f'SK bin table has {total_bins} rows but scratch holds {bin_capacity}'
        )

    live_mineral_cap = layout.field('sk_mineral_table').size // 8
    live_bin_cap = layout.field('sk_bin_table').size // 8

    packed_maps = bytearray(map_capacity * map_stride)
    packed_minerals = bytearray(mineral_capacity * 8)
    packed_bins = bytearray(bin_capacity * 12)
    mineral_index = 0
    bin_index = 0
    for map_idx, m in enumerate(sk_maps):
        name_bytes = m.map_name.encode('latin1')
        if b'\x00' in name_bytes:
            raise ValueError(f'SK map name contains NUL: {m.map_name!r}')
        if len(name_bytes) + 1 > map_name_slot:
            raise ValueError(
                f'SK map name too long for {map_name_slot}-byte slot: {m.map_name!r}'
            )
        if len(m.minerals) > live_mineral_cap:
            raise ValueError(
                f'{m.map_name!r} authors {len(m.minerals)} minerals but the live '
                f'table holds {live_mineral_cap} (raise SK_MINERAL_TABLE_MAX)'
            )
        rec_off = map_idx * map_stride
        packed_maps[rec_off:rec_off + len(name_bytes)] = name_bytes
        struct.pack_into('<IIII', packed_maps, rec_off + map_name_slot,
                         len(m.minerals), mineral_index, len(m.bins), bin_index)
        for (x, y, _kind) in m.minerals:
            struct.pack_into('<ff', packed_minerals, mineral_index * 8,
                             float(x), float(y))
            mineral_index += 1
        for (x, y, team) in m.bins:
            if not (0 <= team < live_bin_cap):
                raise ValueError(
                    f'{m.map_name!r} bin team {team} outside the live table '
                    f'range [0, {live_bin_cap})'
                )
            struct.pack_into('<ffI', packed_bins, bin_index * 12,
                             float(x), float(y), team)
            bin_index += 1

    layout.write(section, scratch_off, 'sk_static_map_count',
                 struct.pack('<I', len(sk_maps)))
    layout.write(section, scratch_off, 'sk_static_maps', bytes(packed_maps))
    layout.write(section, scratch_off, 'sk_static_minerals', bytes(packed_minerals))
    layout.write(section, scratch_off, 'sk_static_bins', bytes(packed_bins))


def write_item_static_table(section, scratch_off, layout, item_maps, map_name_slot):
    """Pack build-time filler-item map records for the goody-pursuit layer
    and seed the live item tables. ``item_maps`` is a sequence of
    ``item_data.MapItemData``; each map record is ``name[slot] | item_count
    u32 | item_first u32``, points pack as (x f32, y f32, category u32)."""
    if not layout.has_field('item_static_map_count'):
        return

    item_maps = tuple(item_maps or ())
    layout.write(section, scratch_off, 'item_routing_active', struct.pack('<I', 0))
    layout.write(section, scratch_off, 'item_count', struct.pack('<I', 0))
    layout.write(section, scratch_off, 'item_node',
                 b'\xFF' * layout.field('item_node').size)
    layout.write(section, scratch_off, 'item_dist',
                 b'\xFF' * layout.field('item_dist').size)
    if layout.has_field('sk_pile_node'):
        layout.write(section, scratch_off, 'sk_pile_node',
                     b'\xFF' * layout.field('sk_pile_node').size)
        layout.write(section, scratch_off, 'sk_pile_dist',
                     b'\xFF' * layout.field('sk_pile_dist').size)

    map_stride = map_name_slot + 8
    map_capacity = layout.field('item_static_maps').size // map_stride
    point_capacity = layout.field('item_static_points').size // 12
    live_capacity = layout.field('item_table').size // 8

    if len(item_maps) > map_capacity:
        raise ValueError(
            f'item map table has {len(item_maps)} rows but scratch holds {map_capacity}'
        )
    total_points = sum(len(m.items) for m in item_maps)
    if total_points > point_capacity:
        raise ValueError(
            f'item point table has {total_points} rows but scratch holds {point_capacity}'
        )

    packed_maps = bytearray(map_capacity * map_stride)
    packed_points = bytearray(point_capacity * 12)
    point_index = 0
    for map_idx, m in enumerate(item_maps):
        name_bytes = m.map_name.encode('latin1')
        if b'\x00' in name_bytes:
            raise ValueError(f'item map name contains NUL: {m.map_name!r}')
        if len(name_bytes) + 1 > map_name_slot:
            raise ValueError(
                f'item map name too long for {map_name_slot}-byte slot: {m.map_name!r}'
            )
        if len(m.items) > live_capacity:
            raise ValueError(
                f'{m.map_name!r} authors {len(m.items)} fillers but the live '
                f'table holds {live_capacity} (raise ITEM_TABLE_MAX)'
            )
        rec_off = map_idx * map_stride
        packed_maps[rec_off:rec_off + len(name_bytes)] = name_bytes
        struct.pack_into('<II', packed_maps, rec_off + map_name_slot,
                         len(m.items), point_index)
        for (x, y, cat) in m.items:
            struct.pack_into('<ffI', packed_points, point_index * 12,
                             float(x), float(y), int(cat) & 0xFF)
            point_index += 1

    layout.write(section, scratch_off, 'item_static_map_count',
                 struct.pack('<I', len(item_maps)))
    layout.write(section, scratch_off, 'item_static_maps', bytes(packed_maps))
    layout.write(section, scratch_off, 'item_static_points', bytes(packed_points))


