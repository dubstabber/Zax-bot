"""``build_scratch_layout`` — assembles the scratch-field list by
calling each feature block in the original (offset-load-bearing)
order. The ctx namespace ``c`` carries the call parameters plus the
running offsets/caps blocks pass to one another; block-owned names
are pre-seeded to ``None``.
"""

from types import SimpleNamespace

from . import (carry, chase, core, door, entity_scan, fight, flag, lane, lava,
               menu, movement, need, pickup, portal, role, sk, switch,
               waypoints, wedge)
from .model import ScratchLayout


def build_scratch_layout(
    base_va,
    scratch_size,
    num_bot_names,
    name_slot_size,
    name_slot_ascii,
    weapon_speeds_max,
    force_bot_ammo_max=0,
    force_bot_ammo_slot_size=0,
    overlay_vertex_max=0,
    overlay_edge_max=0,
    pickup_table_max=0,
    portal_table_max=0,
    portal_static_map_max=0,
    portal_static_point_max=0,
    portal_map_name_slot=0,
    scan_entities_max=0,
    flag_table_max=0,
    flag_static_map_max=0,
    flag_static_point_max=0,
    flag_map_name_slot=0,
    flag_route_max=0,
    flag_entity_slots=2,
    door_table_max=0,
    door_static_map_max=0,
    door_static_point_max=0,
    door_map_name_slot=0,
    door_entity_slots=3,
    door_opener_table_max=0,
    door_opener_static_max=0,
    switch_table_max=0,
    switch_pair_max=0,
    switch_static_map_max=0,
    switch_static_point_max=0,
    switch_static_pair_max=0,
    switch_map_name_slot=0,
    sk_mineral_table_max=0,
    sk_bin_table_max=0,
    sk_static_map_max=0,
    sk_static_mineral_max=0,
    sk_static_bin_max=0,
    sk_map_name_slot=0,
    sk_pile_table_max=0,
    item_table_max=0,
    item_static_map_max=0,
    item_static_point_max=0,
    item_map_name_slot=0,
    item_categories=0,
):
    c = SimpleNamespace(**locals())
    c.MAX_BOT_SLOTS = None
    c.fields = None
    c.AI_HAZARD_CAP = None
    c.ai_off = None
    c.OVERLAY_BASE = None
    c.OVERLAY_TABLE_OFF = None
    c.overlay_color_size = None
    c.overlay_edge_max_capped = None
    c.overlay_edge_stride = None
    c.overlay_fields = None
    c.overlay_vertex_max_capped = None
    c.overlay_vertex_stride = None
    c.wp_io_off = None
    c.pickup_base = None
    c.pickup_table_max_capped = None
    c.div_base = None
    c.div_stride = None
    c.plasma_tmp_base = None
    c.portal_static_base = None
    c.portal_static_point_max_capped = None
    c.portal_table_max_capped = None
    c.tail_off = None
    c.flag_static_base = None
    c.flag_table_max_capped = None
    c.flag_route_max_capped = None
    c.door_static_base = None
    c.door_table_max_capped = None
    c.door_dyn_base = None
    c.sw_off = None
    c.switch_table_max_capped = None
    c.sk_pile_max_capped = None
    c.bot_state_end = None
    c.bot_state_fields = None

    core.extend_core(c)
    movement.extend_movement(c)
    waypoints.extend_wp_overlay(c)
    waypoints.extend_wp_saveload(c)
    pickup.extend_pickup_table(c)
    pickup.extend_pickup_divert(c)
    lava.extend_lava(c)
    portal.extend_portal_tables(c)
    entity_scan.extend_entity_scan(c)
    flag.extend_flag_tables(c)
    flag.extend_flag_routing(c)
    door.extend_door_switch_tables(c)
    portal.extend_portal_routing(c)
    flag.extend_drop_pursuit(c)
    switch.extend_switch_wander(c)
    waypoints.extend_weighted_routing(c)
    sk.extend_sk(c)
    sk.extend_goody(c)
    wedge.extend_wedge(c)
    menu.extend_menu(c)
    role.extend_role(c)
    chase.extend_chase(c)
    fight.extend_fight(c)
    lane.extend_lane(c)
    need.extend_need(c)
    carry.extend_carry(c)

    c.fields.extend(c.overlay_fields)
    return ScratchLayout(base_va, scratch_size, c.fields)
