"""``write_static_scratch_data`` — packs every build-time knob, string
and table into the scratch area (feature blocks gated on
``layout.has_field``)."""

import math
import struct

from .common import (DUMP_TAGS, PROMPT_CTF, PROMPT_DM, PROMPT_SK,
                     _FORCE_MODE_TABLE, pack_tag, write_bot_color_table,
                     write_bot_name_tables)
from .tables import (write_door_static_table, write_flag_static_table,
                     write_item_static_table, write_portal_static_table,
                     write_sk_static_table, write_switch_static_table)


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
    portal_routes=(),
    portal_wander_chance=0,
    portal_jump_reacquire_sq=36864.0,
    portal_veto_radius_sq=1600.0,
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
    switch_wander_chance=0,
    wp_edge_len_quantum=16.0,
    ctf_drop_pursue_enabled=False,
    ctf_drop_pursue_radius_sq=122500.0,
    ctf_drop_reached_radius_sq=576.0,
    ctf_drop_direct_radius_sq=25600.0,
    ctf_drop_abandon_radius_sq=490000.0,
    sk_maps=(),
    sk_map_name_slot=0,
    sk_return_carry_rand_lo=30,
    sk_return_carry_rand_hi=100,
    sk_pile_pursue_radius_sq=90000.0,
    sk_pile_reached_radius_sq=576.0,
    sk_pile_ttl_frames=2700,
    item_maps=(),
    item_map_name_slot=0,
    item_pursue_radius_sq=62500.0,
    goody_direct_radius_sq=25600.0,
    goody_abandon_radius_sq=360000.0,
    weapon_pursue_radius_sq=122500.0,
    mine_avoid_radius_sq=9216.0,
    mine_spacing_sq=16384.0,
    mine_place_chance=0,
    mine_ctf_mid_band=16,
    mine_ctf_mid_chance=0,
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
            portal_routes=portal_routes,
        )
    if layout.has_field('portal_wander_chance'):
        layout.write(section, scratch_off, 'portal_wander_chance',
                     struct.pack('<I', max(0, min(100, int(portal_wander_chance)))))
        layout.write(section, scratch_off, 'portal_jump_sq',
                     struct.pack('<f', portal_jump_reacquire_sq))
    if layout.has_field('portal_veto_radius_sq'):
        layout.write(section, scratch_off, 'portal_veto_radius_sq',
                     struct.pack('<f', portal_veto_radius_sq))
    if layout.has_field('flag_static_map_count'):
        write_flag_static_table(
            section,
            scratch_off,
            layout,
            flag_maps,
            flag_map_name_slot,
        )
    # Dropped-flag pursuit knobs + the expected entity names ("Blue Flag" /
    # "Red Flag", 16-byte slots indexed by flag_team) matched by the periodic
    # grid walk against [ent+0x18]+8 while a flag is away from its base.
    if layout.has_field('drop_pursue_enabled'):
        layout.write(section, scratch_off, 'drop_pursue_enabled',
                     struct.pack('<I', 1 if ctf_drop_pursue_enabled else 0))
        layout.write(section, scratch_off, 'drop_pursue_radius_sq',
                     struct.pack('<f', ctf_drop_pursue_radius_sq))
        layout.write(section, scratch_off, 'drop_reached_radius_sq',
                     struct.pack('<f', ctf_drop_reached_radius_sq))
        layout.write(section, scratch_off, 'drop_direct_radius_sq',
                     struct.pack('<f', ctf_drop_direct_radius_sq))
        layout.write(section, scratch_off, 'drop_abandon_radius_sq',
                     struct.pack('<f', ctf_drop_abandon_radius_sq))
        # Node binds / route roots start invalid; drop_dist rows are rebuilt
        # by drop_route_refresh before first use (gated on root == node).
        if layout.has_field('flag_drop_node'):
            layout.write(section, scratch_off, 'flag_drop_node',
                         b'\xFF' * layout.field('flag_drop_node').size)
        if layout.has_field('drop_route_root'):
            layout.write(section, scratch_off, 'drop_route_root', b'\xFF' * 8)
        drop_names = b'Blue Flag\x00'.ljust(16, b'\x00') + b'Red Flag\x00'.ljust(16, b'\x00')
        layout.write(section, scratch_off, 'drop_names', drop_names)
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
    if layout.has_field('switch_wander_chance'):
        layout.write(section, scratch_off, 'switch_wander_chance',
                     struct.pack('<I', max(0, min(100, int(switch_wander_chance)))))
        # switch_node starts unbound until load_switches binds it per match.
        layout.write(section, scratch_off, 'switch_node',
                     b'\xFF' * layout.field('switch_node').size)
    if layout.has_field('elen_quantum'):
        layout.write(section, scratch_off, 'elen_quantum',
                     struct.pack('<f', max(1.0, float(wp_edge_len_quantum))))
    if layout.has_field('sk_static_map_count'):
        write_sk_static_table(section, scratch_off, layout, sk_maps,
                              sk_map_name_slot)
        # Per-bot RETURN-threshold roll bounds. LO >= 1 (0 is the per-bot
        # "unrolled" sentinel) and HI >= LO so the engine RNG range is valid.
        sk_lo = max(1, int(sk_return_carry_rand_lo))
        sk_hi = max(sk_lo, int(sk_return_carry_rand_hi))
        layout.write(section, scratch_off, 'sk_return_lo',
                     struct.pack('<I', sk_lo))
        layout.write(section, scratch_off, 'sk_return_hi',
                     struct.pack('<I', sk_hi))
        layout.write(section, scratch_off, 'sk_pile_pursue_radius_sq',
                     struct.pack('<f', sk_pile_pursue_radius_sq))
        layout.write(section, scratch_off, 'sk_pile_reached_radius_sq',
                     struct.pack('<f', sk_pile_reached_radius_sq))
        layout.write(section, scratch_off, 'sk_pile_ttl',
                     struct.pack('<I', max(1, int(sk_pile_ttl_frames))))
    if layout.has_field('item_static_map_count'):
        write_item_static_table(section, scratch_off, layout, item_maps,
                                item_map_name_slot)
        layout.write(section, scratch_off, 'item_pursue_radius_sq',
                     struct.pack('<f', item_pursue_radius_sq))
        layout.write(section, scratch_off, 'goody_direct_radius_sq',
                     struct.pack('<f', goody_direct_radius_sq))
        layout.write(section, scratch_off, 'goody_abandon_radius_sq',
                     struct.pack('<f', goody_abandon_radius_sq))
        if layout.has_field('weapon_pursue_radius_sq'):
            layout.write(section, scratch_off, 'weapon_pursue_radius_sq',
                         struct.pack('<f', weapon_pursue_radius_sq))
    # Proximity-mine knobs (avoid/spacing bubbles + the placement roll
    # threshold). Static-packed — deliberately OUTSIDE load_mine's per-match
    # clear range so the chance stays live-tunable across matches.
    if layout.has_field('mine_avoid_radius_sq'):
        layout.write(section, scratch_off, 'mine_avoid_radius_sq',
                     struct.pack('<f', mine_avoid_radius_sq))
        layout.write(section, scratch_off, 'mine_spacing_sq',
                     struct.pack('<f', mine_spacing_sq))
        layout.write(section, scratch_off, 'mine_place_chance',
                     struct.pack('<I', max(0, min(100, int(mine_place_chance)))))
        layout.write(section, scratch_off, 'mine_ctf_mid_band',
                     struct.pack('<I', max(0, int(mine_ctf_mid_band))))
        layout.write(section, scratch_off, 'mine_ctf_mid_chance',
                     struct.pack('<I', max(0, min(100, int(mine_ctf_mid_chance)))))
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

    # --- Bot-menu GUI label strings ---------------------------------------
    # Fixed, mode-agnostic button/title text the B-key dialog builder points
    # its label/button children at (the widget text setters copy them). No cfg
    # knob — these are UI constants.
    if layout.has_field('menu_str_title'):
        layout.write(section, scratch_off, 'menu_str_title',  b'Add Bots\x00')
        layout.write(section, scratch_off, 'menu_str_addbot', b'Add Bot\x00')
        layout.write(section, scratch_off, 'menu_str_blue',   b'Add Blue Bot\x00')
        layout.write(section, scratch_off, 'menu_str_red',    b'Add Red Bot\x00')
        layout.write(section, scratch_off, 'menu_str_close',  b'Close\x00')

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
