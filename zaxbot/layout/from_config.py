"""``build_layout_from_config`` — the production scratch layout, with every
table capacity taken from ``zaxbot.config``.

This is the cfg->kwargs mapping that used to live inline in
``hook/entry.py``. Adding a feature's tables means adding its block in the
block modules and its capacity row here; ``build_scratch_layout`` itself
stays cfg-free so tests can build reduced layouts with explicit kwargs.
"""

from .. import config as cfg
from .builder import build_scratch_layout


def _switch_on():
    """Switch tables require the door tables (pair door indices reference the
    active map's door_table order)."""
    return cfg.SWITCH_DETECT_ENABLED and cfg.DOOR_DETECT_ENABLED


def build_layout_from_config(scratch_va, scratch_size):
    return build_scratch_layout(
        scratch_va,
        scratch_size,
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
        sk_mineral_table_max=cfg.SK_MINERAL_TABLE_MAX if cfg.SK_ENABLED else 0,
        sk_bin_table_max=cfg.SK_BIN_TABLE_MAX if cfg.SK_ENABLED else 0,
        sk_static_map_max=cfg.SK_STATIC_MAP_MAX if cfg.SK_ENABLED else 0,
        sk_static_mineral_max=cfg.SK_STATIC_MINERAL_MAX if cfg.SK_ENABLED else 0,
        sk_static_bin_max=cfg.SK_STATIC_BIN_MAX if cfg.SK_ENABLED else 0,
        sk_map_name_slot=cfg.SK_MAP_NAME_SLOT if cfg.SK_ENABLED else 0,
        sk_pile_table_max=cfg.SK_PILE_TABLE_MAX if cfg.SK_ENABLED else 0,
        item_table_max=cfg.ITEM_TABLE_MAX if cfg.ITEM_PURSUIT_ENABLED else 0,
        item_static_map_max=cfg.ITEM_STATIC_MAP_MAX if cfg.ITEM_PURSUIT_ENABLED else 0,
        item_static_point_max=cfg.ITEM_STATIC_POINT_MAX if cfg.ITEM_PURSUIT_ENABLED else 0,
        item_map_name_slot=cfg.ITEM_MAP_NAME_SLOT if cfg.ITEM_PURSUIT_ENABLED else 0,
        item_categories=cfg.ITEM_CATEGORIES if cfg.ITEM_PURSUIT_ENABLED else 0,
        mine_table_max=cfg.MINE_TABLE_MAX if cfg.MINE_ENABLED else 0,
    )
