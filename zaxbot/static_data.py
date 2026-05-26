"""Static data packing for the .zaxbot section."""

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
    weapon_speeds=(),
    force_bot_item_id=None,
    force_mode=None,
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
    # copied in when the current weapon's prototype isn't in WEAPON_SPEEDS.
    layout.write(section, scratch_off, 'proj_speed', struct.pack('<f', projectile_speed))
    layout.write(section, scratch_off, 'default_proj_speed', struct.pack('<f', projectile_speed))

    # Pack WEAPON_SPEEDS into the runtime table. Each entry is (proto_va u32,
    # speed float). Terminated by a (0, 0.0) sentinel; the asm scan stops on
    # the first zero proto_va. Empty list → first dword is 0 → no matches.
    weapon_field = layout.field('weapon_table')
    max_entries = weapon_field.size // 8 - 1  # one slot reserved for sentinel
    if len(weapon_speeds) > max_entries:
        raise ValueError(
            f'WEAPON_SPEEDS has {len(weapon_speeds)} rows but scratch holds '
            f'{max_entries} (raise cfg.WEAPON_SPEEDS_MAX)'
        )
    packed = b''.join(struct.pack('<If', proto_va, speed)
                       for proto_va, speed in weapon_speeds)
    packed += struct.pack('<If', 0, 0.0)  # sentinel
    layout.write(section, scratch_off, 'weapon_table', packed)

    # FORCE_BOT_ITEM_ID — 0xFFFFFFFF sentinel means "no override".
    forced_item = 0xFFFFFFFF if force_bot_item_id is None else (force_bot_item_id & 0xFFFFFFFF)
    layout.write(section, scratch_off, 'force_bot_item_id', struct.pack('<I', forced_item))

    # The dump header magic is written once; runtime code rewrites tag/src/len.
    layout.write(section, scratch_off, 'thdr', struct.pack('<I', dump_magic))

    for field_name, tag in DUMP_TAGS:
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

