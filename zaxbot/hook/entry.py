"""``build_hook`` orchestrator for the .zaxbot section.

Builds the scratch layout, allocates a single ``Asm`` instance, then calls
each emitter in the order their labels appear in the final section. Returns
``(section_bytes, info)`` where ``info`` is the label-VA dict consumed by
``zaxbot.patch_manifest``.

Emit order matters: it determines absolute label positions and thus the
detour-target VAs that are patched back into ``Zax.exe``. Do not reorder
without re-establishing a new byte-identity baseline."""

from .. import config as cfg
from ..asm import Asm
from ..layout import build_scratch_layout
from ..portal_data import resolve_portal_data
from ..flag_data import resolve_flag_data
from ..door_data import resolve_door_topology
from ..static_data import write_static_scratch_data
from . import aim_lead, apply_colors, detect_mode, dispatcher, snapshot, spawn, waypoint_diag, waypoint_edit, weapon_speed
from .helpers import emit_logc_body, emit_wbuf_body
from ..detours import (
    bot_fire_aim,
    bot_movement,
    bot_perception,
    char_iter,
    ctf_score_guard,
    df90_match_change,
    dp_poll,
    entity_scan,
    flag_events,
    flag_route,
    name_block,
    overlay,
    pickup_register,
    portal_register,
    spawn_safety,
    walk_controller,
    world_scan,
)


# Every label name that ``patch_manifest`` will look up by ``<label>_va`` key.
# Maps the label inside .zaxbot to its info-dict key.
_DETOUR_LABEL_KEYS = {
    'detour_dp':               'detour_dp_va',
    'detour_df90':             'detour_df90_va',
    'detour_5AA4E0':           'detour_5AA4E0_va',
    'detour_4FBC50':           'detour_4FBC50_va',
    'detour_542550':           'detour_542550_va',
    'detour_542360':           'detour_542360_va',
    'detour_5436F0':           'detour_5436F0_va',
    'detour_4EF900_test':      'detour_4EF900_test_va',
    'detour_4FC7C0':           'detour_4FC7C0_va',
    'detour_417390':           'detour_417390_va',
    'detour_5AC299':           'detour_5AC299_va',
    'detour_name_query1':      'detour_name_query1_va',
    'detour_name_query2':      'detour_name_query2_va',
    'detour_name_block_skip':  'detour_name_block_skip_va',
    'detour_4F5204':           'detour_4F5204_va',
    'detour_5693A0':           'detour_5693A0_va',
    'detour_53DA40':           'detour_53DA40_va',
    'detour_4C11A0':           'detour_4C11A0_va',
    'detour_5A9960':           'detour_5A9960_va',
    'detour_4C29F0':           'detour_4C29F0_va',
    'detour_4C2D60':           'detour_4C2D60_va',
}


def _switch_on():
    """Switch tables require the door tables (pair door indices reference the
    active map's door_table order)."""
    return cfg.SWITCH_DETECT_ENABLED and cfg.DOOR_DETECT_ENABLED


def build_hook(section_va_abs):
    """Assemble the .zaxbot section. Returns ``(section_bytes, info)``.

    On B: open a mode-aware text-prompt menu via ``sub_59B260``. On the next
    digit key, spawn a bot bound to the chosen team. ``detect_mode`` calls
    the engine's ``sub_59FF90(ecx=mgr)`` getter for the active game-type
    instance and matches ``[result+0]`` against the three known vtables to
    return 0 (DM), 1 (CTF), or 2 (SK); unknown vtables drop a one-shot
    0x200-byte dump and fall back to DM. ``zaxbot/config.py``'s
    ``FORCE_MODE`` knob short-circuits detection for offline testing.
    """
    scratch_va = section_va_abs + cfg.SCRATCH_OFF
    layout = build_scratch_layout(
        scratch_va,
        cfg.NEW_SECTION_SIZE - cfg.SCRATCH_OFF,
        cfg.NUM_BOT_NAMES,
        cfg.NAME_SLOT_SIZE,
        cfg.NAME_SLOT_ASCII,
        cfg.WEAPON_SPEEDS_MAX,
        force_bot_ammo_max=cfg.FORCE_BOT_AMMO_MAX,
        force_bot_ammo_slot_size=cfg.FORCE_BOT_AMMO_SLOT_SIZE,
        overlay_vertex_max=cfg.OVERLAY_VERTEX_MAX,
        overlay_edge_max=cfg.OVERLAY_EDGE_MAX,
        pickup_table_max=cfg.PICKUP_TABLE_MAX,
        portal_table_max=cfg.PORTAL_TABLE_MAX,
        portal_static_map_max=cfg.PORTAL_STATIC_MAP_MAX,
        portal_static_point_max=cfg.PORTAL_STATIC_POINT_MAX,
        portal_map_name_slot=cfg.PORTAL_MAP_NAME_SLOT,
        scan_entities_max=cfg.SCAN_ENTITIES_MAX,
        flag_table_max=cfg.FLAG_TABLE_MAX,
        flag_static_map_max=cfg.FLAG_STATIC_MAP_MAX,
        flag_static_point_max=cfg.FLAG_STATIC_POINT_MAX,
        flag_map_name_slot=cfg.FLAG_MAP_NAME_SLOT,
        flag_route_max=cfg.FLAG_ROUTE_MAX,
        flag_entity_slots=cfg.FLAG_ENTITY_SLOTS_PER_FLAG,
        door_table_max=cfg.DOOR_TABLE_MAX if cfg.DOOR_DETECT_ENABLED else 0,
        door_static_map_max=cfg.DOOR_STATIC_MAP_MAX if cfg.DOOR_DETECT_ENABLED else 0,
        door_static_point_max=cfg.DOOR_STATIC_POINT_MAX if cfg.DOOR_DETECT_ENABLED else 0,
        door_map_name_slot=cfg.DOOR_MAP_NAME_SLOT if cfg.DOOR_DETECT_ENABLED else 0,
        door_entity_slots=cfg.DOOR_ENTITY_SLOTS_PER_DOOR,
        door_opener_table_max=cfg.DOOR_OPENER_TABLE_MAX if cfg.DOOR_DETECT_ENABLED else 0,
        door_opener_static_max=cfg.DOOR_OPENER_STATIC_MAX if cfg.DOOR_DETECT_ENABLED else 0,
        switch_table_max=cfg.SWITCH_TABLE_MAX if _switch_on() else 0,
        switch_pair_max=cfg.SWITCH_PAIR_MAX if _switch_on() else 0,
        switch_static_map_max=cfg.SWITCH_STATIC_MAP_MAX if _switch_on() else 0,
        switch_static_point_max=cfg.SWITCH_STATIC_POINT_MAX if _switch_on() else 0,
        switch_static_pair_max=cfg.SWITCH_STATIC_PAIR_MAX if _switch_on() else 0,
        switch_map_name_slot=cfg.SWITCH_MAP_NAME_SLOT if _switch_on() else 0,
    )

    a = Asm(section_va_abs + cfg.HOOK_ENTRY_OFF)

    # --- Hook payload bodies (order is load-bearing) -----------------------
    dispatcher.emit(a, layout)
    detect_mode.emit(a, layout)
    spawn.emit(a, layout)
    # apply_bot_colors is a callable subroutine used from spawn (post-spawn
    # color application). Emit it immediately after spawn so the call_lbl
    # forward-reference resolves to a nearby site.
    apply_colors.emit(a, layout)
    emit_wbuf_body(a, dummy_va=layout.va('dummy'))
    snapshot.emit(a, layout)
    waypoint_diag.emit(a, layout)
    waypoint_edit.emit(a, layout)
    emit_logc_body(
        a,
        stepfn_va=layout.va('stepfn'),
        dummy_va=layout.va('dummy'),
        logbyte_va=layout.va('logbyte'),
    )

    # --- Detours (emit order = section layout order) -----------------------
    dp_poll.emit(a, layout)
    df90_match_change.emit(a, layout)
    walk_controller.emit(a, layout)
    # world_scan must precede bot_movement so the `call_lbl 'pick_pickup'`
    # forward reference is at a sane distance (matches the `pick_target`
    # precedent — the linker is two-pass so source order isn't required, but
    # adjacency keeps the section grep-friendly).
    world_scan.emit(a, layout)
    bot_movement.emit(a, layout)
    # pick_target must be emitted before bot_fire_aim's detour body so the
    # call_lbl inside detour_5436F0 resolves to a forward-defined label
    # (works either way since Asm.link() is a two-pass linker, but emit
    # order also fixes the absolute VAs, so we keep perception adjacent).
    bot_perception.emit(a, layout)
    aim_lead.emit(a, layout)
    weapon_speed.emit(a, layout)
    bot_fire_aim.emit(a, layout)
    spawn_safety.emit(a, layout)
    name_block.emit(a, layout)
    char_iter.emit(a, layout)
    overlay.emit(a, layout)
    pickup_register.emit(a, layout)
    portal_register.emit(a, layout)
    entity_scan.emit(a, layout)
    flag_events.emit(a, layout)
    flag_route.emit(a, layout)
    ctf_score_guard.emit(a, layout)

    code = a.link()
    assert len(code) <= cfg.SCRATCH_OFF, (
        f'hook code overflows scratch: {len(code):#x}'
    )

    overlay_waypoints, overlay_edges = cfg.resolve_overlay_data()
    portal_maps = resolve_portal_data()
    flag_maps = resolve_flag_data()
    door_maps = resolve_door_topology() if cfg.DOOR_DETECT_ENABLED else ()

    section = bytearray(cfg.NEW_SECTION_SIZE)
    section[cfg.HOOK_ENTRY_OFF:cfg.HOOK_ENTRY_OFF + len(code)] = code
    write_static_scratch_data(
        section,
        cfg.SCRATCH_OFF,
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
    )

    info = {
        'hook_entry_va': section_va_abs + cfg.HOOK_ENTRY_OFF,
        'hook_entry_size': len(code),
        'scratch_va': scratch_va,
        'msg_va': layout.va('msg'),
    }
    for label, key in _DETOUR_LABEL_KEYS.items():
        info[key] = section_va_abs + a.labels[label]
    return bytes(section), info
