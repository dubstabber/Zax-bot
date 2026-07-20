"""Teleporter pad tables (static + live) and portal-routing state."""

from .model import ScratchField


def extend_portal_tables(c):
    portal_table_max = c.portal_table_max
    portal_static_map_max = c.portal_static_map_max
    portal_static_point_max = c.portal_static_point_max
    portal_map_name_slot = c.portal_map_name_slot
    overlay_color_size = c.overlay_color_size
    overlay_fields = c.overlay_fields
    plasma_tmp_base = c.plasma_tmp_base
    portal_static_base = c.portal_static_base
    portal_static_point_max_capped = c.portal_static_point_max_capped
    portal_table_max_capped = c.portal_table_max_capped

    # --- Teleport/portal overlay data -------------------------------------
    # The live portal_table is populated once per match by load_portals from
    # the compact build-time static table (parsed from Data.dat). Kept after
    # the existing overlay/lava tail so all older scratch offsets remain
    # stable. Map entries are fixed-size:
    #   name[PORTAL_MAP_NAME_SLOT] | count u32 | first_point_index u32
    portal_table_max_capped = max(0, portal_table_max)
    portal_static_map_max_capped = max(0, portal_static_map_max)
    portal_static_point_max_capped = max(0, portal_static_point_max)
    portal_name_slot_capped = max(0, portal_map_name_slot)
    portal_map_stride = portal_name_slot_capped + 8
    portal_base = plasma_tmp_base + 0x860
    overlay_fields.extend([
        ScratchField('portal_count', portal_base + 0x00, 0x04,
                     'portal: live entries in portal_table for active map'),
        ScratchField('overlay_portal_color', portal_base + 0x04, overlay_color_size,
                     'overlay: detected-portal CColor (rebuilt per-frame)'),
    ])
    portal_static_base = portal_base + 0x14
    if portal_table_max_capped > 0:
        overlay_fields.append(ScratchField(
            'portal_table', portal_static_base,
            portal_table_max_capped * 8,
            'portal: float[2] per detected teleport destination (world coords)',
        ))
        portal_static_base += portal_table_max_capped * 8
    overlay_fields.extend([
        ScratchField('portal_static_map_count', portal_static_base + 0x00, 0x04,
                     'portal: build-time static map table count'),
        ScratchField('portal_static_point_count', portal_static_base + 0x04, 0x04,
                     'portal: build-time static point table count'),
    ])
    portal_static_base += 0x08
    if portal_static_map_max_capped > 0 and portal_map_stride > 8:
        overlay_fields.append(ScratchField(
            'portal_static_maps', portal_static_base,
            portal_static_map_max_capped * portal_map_stride,
            'portal: static map records (name/count/first point)',
        ))
        portal_static_base += portal_static_map_max_capped * portal_map_stride
    if portal_static_point_max_capped > 0:
        overlay_fields.append(ScratchField(
            'portal_static_points', portal_static_base,
            portal_static_point_max_capped * 8,
            'portal: static float[2] point table parsed from Data.dat',
        ))
        portal_static_base += portal_static_point_max_capped * 8
    # Per-call scratch (float[2]) for the teleport-portal detour's sub_4FB0A0
    # source-position read (detours/portal_register.py). Appended at the very
    # tail of the portal block so no existing portal/overlay offset shifts.
    if portal_table_max_capped > 0:
        overlay_fields.append(ScratchField(
            'portal_reg_tmp', portal_static_base, 0x08,
            'portal: float[2] scratch for sub_4FB0A0 teleport source-pos read',
        ))
        portal_static_base += 0x08

    c.portal_static_base = portal_static_base
    c.portal_static_point_max_capped = portal_static_point_max_capped
    c.portal_table_max_capped = portal_table_max_capped



def extend_portal_routing(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    overlay_fields = c.overlay_fields
    portal_static_point_max_capped = c.portal_static_point_max_capped
    portal_table_max_capped = c.portal_table_max_capped


    # --- Portal routing (teleport pads as directed graph edges) -------------
    # Appended at the very tail (past every door/switch block) so no existing
    # scratch offset shifts. Live tables parallel portal_table: dest coords +
    # has-dest flags copied per match by load_portals from the static tables;
    # node bindings computed per match by bind_portal_nodes (nearest graph
    # node to each pad / destination); bot_portal_target latches a per-bot pad
    # final-approach (portal idx+1, 0 = none). The block from
    # portal_dest_table through pw_spill is contiguous and dumped whole by the
    # R-snapshot `proute` chunk.
    if portal_table_max_capped > 0:
        proute_base = max(
            [f.end for f in fields] + [f.end for f in overlay_fields]
        )
        proute_base = (proute_base + 7) & ~7
        overlay_fields.extend([
            ScratchField('portal_dest_table', proute_base,
                         portal_table_max_capped * 8,
                         'portal-route: float[2] teleport destination per live pad'),
            ScratchField('portal_has_dest',
                         proute_base + portal_table_max_capped * 8,
                         portal_table_max_capped * 4,
                         'portal-route: 1 = this pad has a build-time-resolved destination'),
            ScratchField('portal_node',
                         proute_base + portal_table_max_capped * 12,
                         portal_table_max_capped * 4,
                         'portal-route: nearest graph node to each pad source (-1 = unbound)'),
            ScratchField('portal_dest_node',
                         proute_base + portal_table_max_capped * 16,
                         portal_table_max_capped * 4,
                         'portal-route: nearest graph node to each pad destination (-1 = unbound)'),
            ScratchField('bot_portal_target',
                         proute_base + portal_table_max_capped * 20,
                         MAX_BOT_SLOTS * 4,
                         'portal-route: per-bot latched pad approach (portal idx+1, 0 = none)'),
            ScratchField('bot_portal_cd',
                         proute_base + portal_table_max_capped * 20 + MAX_BOT_SLOTS * 4,
                         MAX_BOT_SLOTS * 4,
                         'portal-route: per-bot post-teleport wander-entry cooldown (thinks)'),
            ScratchField('bot_pad_try',
                         proute_base + portal_table_max_capped * 20 + MAX_BOT_SLOTS * 8,
                         MAX_BOT_SLOTS * 4,
                         'portal-route: per-bot pad-press patience (watchdog timeout cycles while latched)'),
        ])
        proute_off = proute_base + portal_table_max_capped * 20 + MAX_BOT_SLOTS * 12
        overlay_fields.extend([
            ScratchField('route_portal_hop', proute_off + 0x00, 0x04,
                         'portal-route: ctf_next_hop output — winning pad idx+1 this arrival (0 = node hop)'),
            ScratchField('portal_wander_chance', proute_off + 0x04, 0x04,
                         'portal-route: RNG(0..99) < this enters an adjacent pad while roaming (0 = off)'),
            ScratchField('portal_jump_sq', proute_off + 0x08, 0x04,
                         'portal-route: per-think move d^2 above which the bot counts as teleported (float)'),
            ScratchField('tp_jump_d2', proute_off + 0x0C, 0x04,
                         'portal-route: per-think position-delta d^2 spill (teleport-jump detect)'),
            ScratchField('pw_spill', proute_off + 0x10, 0x04,
                         'portal-route: loop-index spill surviving RNG/wp_find_nearest calls'),
            ScratchField('portal_veto_radius_sq', proute_off + 0x14, 0x04,
                         'portal-route: post-teleport heading veto — lookahead-to-pad d^2 bubble (float)'),
        ])
        proute_off += 0x18
        if portal_static_point_max_capped > 0:
            overlay_fields.extend([
                ScratchField('portal_static_dests', proute_off,
                             portal_static_point_max_capped * 8,
                             'portal-route: static float[2] destination table (parallel to portal_static_points)'),
                ScratchField('portal_static_hasdest',
                             proute_off + portal_static_point_max_capped * 8,
                             portal_static_point_max_capped * 4,
                             'portal-route: static has-destination dwords (parallel to portal_static_points)'),
            ])
            proute_off += portal_static_point_max_capped * 12
        overlay_fields.append(ScratchField(
            'tag_proute', proute_off, 0x10,
            'diag: portal-routing state dump chunk tag',
        ))


