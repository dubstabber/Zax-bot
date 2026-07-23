"""``write_static_from_config`` — packs the static scratch contents with
every knob taken from ``zaxbot.config`` (including the build-time Data.dat
resolves).

This is the cfg->kwargs mapping that used to live inline in
``hook/entry.py``. Adding a feature knob means adding its row here next to
its feature block; ``write_static_scratch_data`` itself stays cfg-free so
tests can pack reduced layouts with explicit kwargs.
"""

from .. import config as cfg
from ..door_data import resolve_door_topology
from ..flag_data import resolve_flag_data
from ..item_data import resolve_item_data
from ..portal_data import resolve_portal_data, resolve_portal_routes
from ..sk_data import resolve_sk_data
from .scratch import write_static_scratch_data


def write_static_from_config(section, scratch_off, layout):
    overlay_waypoints, overlay_edges = cfg.resolve_overlay_data()
    portal_maps = resolve_portal_data()
    flag_maps = resolve_flag_data()
    door_maps = resolve_door_topology() if cfg.DOOR_DETECT_ENABLED else ()
    sk_maps = resolve_sk_data() if cfg.SK_ENABLED else ()
    item_maps = resolve_item_data() if cfg.ITEM_PURSUIT_ENABLED else ()

    write_static_scratch_data(
        section,
        scratch_off,
        layout,
        dump_filename=cfg.DUMP_FILENAME,
        dump_msg=cfg.DUMP_MSG,
        step_filename=cfg.STEP_FILENAME,
        full_msg=cfg.FULL_MSG,
        dump_magic=cfg.DUMP_MAGIC,
        dump_tag_len=cfg.DUMP_TAG_LEN,
        bot_names=cfg.BOT_NAMES,
        name_slot_size=cfg.NAME_SLOT_SIZE,
        name_slot_ascii=cfg.NAME_SLOT_ASCII,
        bot_colors=cfg.BOT_COLORS,
        prompt_dm_va=layout.va('prompt_dm'),
        prompt_ctf_va=layout.va('prompt_ctf'),
        prompt_sk_va=layout.va('prompt_sk'),
        fire_range_sq=cfg.FIRE_RANGE_SQ,
        projectile_speed=cfg.PROJECTILE_SPEED,
        speed_scale=cfg.SPEED_SCALE,
        muzzle_offset=cfg.MUZZLE_OFFSET,
        lead_probability=cfg.LEAD_PROBABILITY,
        weapon_speeds=cfg.WEAPON_SPEEDS,
        force_bot_item_name=cfg.resolve_force_bot_item_name(),
        force_bot_ammo_names=cfg.resolve_force_bot_ammo_names(),
        force_bot_ammo_slot_size=cfg.FORCE_BOT_AMMO_SLOT_SIZE,
        force_mode=cfg.FORCE_MODE,
        movement_enabled=cfg.MOVEMENT_ENABLED,
        wander_target_radius=cfg.WANDER_TARGET_RADIUS,
        wander_target_timeout_frames=cfg.WANDER_TARGET_TIMEOUT_FRAMES,
        stuck_frames_threshold=cfg.STUCK_FRAMES_THRESHOLD,
        stuck_delta_sq=cfg.STUCK_DELTA_SQ,
        item_attractor_radius_sq=cfg.ITEM_ATTRACTOR_RADIUS_SQ,
        item_attractor_weight=cfg.ITEM_ATTRACTOR_WEIGHT,
        item_scan_interval_frames=cfg.ITEM_SCAN_INTERVAL_FRAMES,
        hazard_repulsion_radius_sq=cfg.HAZARD_REPULSION_RADIUS_SQ,
        hazard_repulsion_weight=cfg.HAZARD_REPULSION_WEIGHT,
        hazard_default_radius_sq=cfg.HAZARD_DEFAULT_RADIUS_SQ,
        bot_move_speed=cfg.BOT_MOVE_SPEED,
        hazard_flee_frames=cfg.HAZARD_FLEE_FRAMES,
        wp_follow_enabled=cfg.WP_FOLLOW_ENABLED,
        wp_reached_radius_sq=cfg.WP_REACHED_RADIUS_SQ,
        wp_edge_lookahead=cfg.WP_EDGE_LOOKAHEAD,
        wp_edge_follow_enabled=cfg.WP_EDGE_FOLLOW_ENABLED,
        wp_progress_timeout_frames=cfg.WP_PROGRESS_TIMEOUT_FRAMES,
        wp_stuck_reached_radius_sq=cfg.WP_STUCK_REACHED_RADIUS_SQ,
        wp_slide_turn_step_deg=cfg.WP_SLIDE_TURN_STEP_DEG,
        overlay_enabled=cfg.OVERLAY_ENABLED,
        overlay_waypoints=overlay_waypoints,
        overlay_edges=overlay_edges,
        overlay_vertex_color=cfg.OVERLAY_VERTEX_COLOR,
        overlay_edge_color=cfg.OVERLAY_EDGE_COLOR,
        overlay_selected_color=cfg.OVERLAY_SELECTED_COLOR,
        overlay_pickup_color=cfg.OVERLAY_PICKUP_COLOR,
        overlay_portal_color=cfg.OVERLAY_PORTAL_COLOR,
        overlay_flag_color=cfg.OVERLAY_FLAG_COLOR,
        ctf_flag_entity_match_radius_sq=cfg.CTF_FLAG_ENTITY_MATCH_RADIUS_SQ,
        ctf_flag_home_force_tick_radius_sq=cfg.CTF_FLAG_HOME_FORCE_TICK_RADIUS_SQ,
        overlay_vertex_radius=cfg.OVERLAY_VERTEX_RADIUS,
        overlay_vertex_aspect=cfg.OVERLAY_VERTEX_ASPECT,
        overlay_cull_min_x=cfg.OVERLAY_CULL_MIN_X,
        overlay_cull_max_x=cfg.OVERLAY_CULL_MAX_X,
        overlay_cull_min_y=cfg.OVERLAY_CULL_MIN_Y,
        overlay_cull_max_y=cfg.OVERLAY_CULL_MAX_Y,
        pickup_register_enabled=(
            cfg.PICKUP_REGISTER_ENABLED
            or cfg.PICKUP_DIVERT_ENABLED
            or (cfg.PICKUP_OVERLAY_MARKERS_ENABLED and cfg.OVERLAY_ENABLED)
        ),
        pickup_divert_enabled=cfg.PICKUP_DIVERT_ENABLED,
        pickup_divert_radius_sq=cfg.PICKUP_DIVERT_RADIUS_SQ,
        pickup_reached_radius_sq=cfg.PICKUP_REACHED_RADIUS_SQ,
        pickup_cooldown_frames=cfg.PICKUP_COOLDOWN_FRAMES,
        pickup_divert_timeout_frames=cfg.PICKUP_DIVERT_TIMEOUT_FRAMES,
        pickup_divert_avoid_damage=cfg.PICKUP_DIVERT_AVOID_DAMAGE,
        wp_snap_radius_sq=cfg.WP_SNAP_RADIUS_SQ,
        wp_dir_name=cfg.WP_DIR,
        wp_file_suffix=cfg.WP_FILE_SUFFIX,
        lava_avoid_enabled=cfg.LAVA_AVOID_ENABLED,
        lava_heat_threshold=cfg.LAVA_HEAT_THRESHOLD,
        lava_lookahead_px=cfg.LAVA_LOOKAHEAD_PX,
        lava_sweep_step_deg=cfg.LAVA_SWEEP_STEP_DEG,
        lava_flee_enabled=cfg.LAVA_FLEE_ENABLED,
        lava_flee_frames=cfg.LAVA_FLEE_FRAMES,
        portal_maps=portal_maps,
        portal_map_name_slot=cfg.PORTAL_MAP_NAME_SLOT,
        portal_routes=resolve_portal_routes(),
        portal_wander_chance=cfg.PORTAL_WANDER_CHANCE,
        portal_jump_reacquire_sq=cfg.PORTAL_JUMP_REACQUIRE_DIST_SQ,
        portal_veto_radius_sq=cfg.PORTAL_VETO_RADIUS_SQ,
        flag_maps=flag_maps,
        flag_map_name_slot=cfg.FLAG_MAP_NAME_SLOT,
        door_maps=door_maps,
        door_map_name_slot=cfg.DOOR_MAP_NAME_SLOT,
        overlay_door_color=cfg.OVERLAY_DOOR_COLOR,
        door_entity_match_radius_sq=cfg.DOOR_ENTITY_MATCH_RADIUS_SQ,
        door_wedge_match_radius_sq=cfg.DOOR_WEDGE_MATCH_RADIUS_SQ,
        door_edge_radius_sq=cfg.DOOR_EDGE_RADIUS_SQ,
        switch_map_name_slot=cfg.SWITCH_MAP_NAME_SLOT,
        overlay_switch_color=cfg.OVERLAY_SWITCH_COLOR,
        switch_wander_chance=(cfg.SWITCH_WANDER_CHANCE
                              if cfg.SWITCH_WANDER_ENABLED else 0),
        wp_edge_len_quantum=cfg.WP_EDGE_LEN_QUANTUM,
        ctf_drop_pursue_enabled=cfg.CTF_DROPPED_FLAG_ENABLED,
        ctf_drop_pursue_radius_sq=cfg.CTF_DROP_PURSUE_RADIUS_SQ,
        ctf_drop_reached_radius_sq=cfg.CTF_DROP_REACHED_RADIUS_SQ,
        ctf_drop_direct_radius_sq=cfg.CTF_DROP_DIRECT_RADIUS_SQ,
        ctf_drop_abandon_radius_sq=cfg.CTF_DROP_ABANDON_RADIUS_SQ,
        sk_maps=sk_maps,
        sk_map_name_slot=cfg.SK_MAP_NAME_SLOT,
        sk_return_carry_rand_lo=cfg.SK_RETURN_CARRY_RAND_LO,
        sk_return_carry_rand_hi=cfg.SK_RETURN_CARRY_RAND_HI,
        sk_pile_pursue_radius_sq=cfg.SK_PILE_PURSUE_RADIUS_SQ,
        sk_pile_reached_radius_sq=cfg.SK_PILE_REACHED_RADIUS_SQ,
        sk_pile_ttl_frames=cfg.SK_PILE_TTL_FRAMES,
        item_maps=item_maps,
        item_map_name_slot=cfg.ITEM_MAP_NAME_SLOT,
        item_pursue_radius_sq=cfg.ITEM_PURSUE_RADIUS_SQ,
        goody_direct_radius_sq=cfg.GOODY_DIRECT_RADIUS_SQ,
        goody_abandon_radius_sq=cfg.GOODY_ABANDON_RADIUS_SQ,
        mine_avoid_radius_sq=cfg.MINE_AVOID_RADIUS_SQ,
        mine_spacing_sq=cfg.MINE_SPACING_RADIUS_SQ,
        mine_place_chance=cfg.MINE_PLACE_CHANCE,
    )
