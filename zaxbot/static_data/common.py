"""Shared constants + small writers for static scratch packing:
menu prompts, dump-chunk tags, FORCE_MODE encoding, and the bot
name/color tables."""

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
    # Switch-seek state (switch_node bindings + per-team state + bot_seek).
    ('tag_seek', 'seek'),
    # Portal-routing state (dest tables, node bindings, per-bot pad latches).
    ('tag_proute', 'proute'),
    # Dropped-flag pursuit state (drop positions/valid + per-bot latches).
    ('tag_dpursuit', 'dpursuit'),
    # Roam switch wander-bump state (per-bot bump latches + census/knob).
    ('tag_swander', 'swander'),
    # Salvage King state (live mineral/bin tables + per-bot phase latches +
    # pile ring; the static pack + BFS rows after the tag are excluded).
    ('tag_skstate', 'skstate'),
    # Goody pursuit state (item field gate/count, resolved target, pile
    # dirty flag + node binds; the static pack + fields are excluded).
    ('tag_goody', 'goody'),
    # Wedge-escape + fight-stall state (wpfn_excl, per-bot wedge counter,
    # per-bot enemy-near stamp).
    ('tag_wedge', 'wedge'),
    # CTF role state (per-bot attacker/defender, per-team spawn counters,
    # per-base defend radii).
    ('tag_role', 'role'),
    # CTF enemy-carrier chase state (shared per-flag sighting intel, row
    # roots, per-bot latches/cooldowns).
    ('tag_chase', 'chase'),
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




# FORCE_MODE encoding: 0xFFFFFFFF = auto-detect.
_FORCE_MODE_TABLE = {None: 0xFFFFFFFF, 'dm': 0, 'ctf': 1, 'sk': 2}
