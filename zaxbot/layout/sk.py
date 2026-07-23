"""Salvage King mineral/bin/pile tables + goody-pursuit state."""

from .model import ScratchField


def extend_sk(c):
    sk_mineral_table_max = c.sk_mineral_table_max
    sk_bin_table_max = c.sk_bin_table_max
    sk_static_map_max = c.sk_static_map_max
    sk_static_mineral_max = c.sk_static_mineral_max
    sk_static_bin_max = c.sk_static_bin_max
    sk_map_name_slot = c.sk_map_name_slot
    sk_pile_table_max = c.sk_pile_table_max
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    overlay_fields = c.overlay_fields
    overlay_vertex_max_capped = c.overlay_vertex_max_capped
    sk_pile_max_capped = c.sk_pile_max_capped


    # --- Salvage King (SK) layer ---------------------------------------------
    # Appended at the very tail so no existing scratch offset shifts. The block
    # from sk_routing_active through tag_skstate is CONTIGUOUS and dumped whole
    # by the R-snapshot `skstate` chunk (tests pin the ordering); the static
    # pack tables and the BFS distance rows sit after the tag and are excluded
    # (mirror of seek_dist / drop_dist). Live tables are per match: minerals +
    # this map's bins (indexed by TEAM id == authored bin number - 1 == the SK
    # bot team id botidx); sk_ore_dist is the per-match MULTI-SOURCE mineral
    # field (every mineral-bearing node seeded at distance 0); sk_bin_dist is
    # one bfs_run row per authored bin, team-major.
    sk_mineral_max_capped = max(0, sk_mineral_table_max)
    sk_bin_max_capped     = max(0, sk_bin_table_max)
    sk_pile_max_capped    = max(0, sk_pile_table_max)
    if (sk_mineral_max_capped > 0 and sk_bin_max_capped > 0
            and overlay_vertex_max_capped > 0):
        sk_off = max([f.end for f in fields] + [f.end for f in overlay_fields])
        sk_off = (sk_off + 7) & ~7

        def _sk_field(name, size, desc):
            nonlocal sk_off
            overlay_fields.append(ScratchField(name, sk_off, size, desc))
            sk_off += size

        _sk_field('sk_routing_active', 0x04,
                  'sk: master runtime gate (mode==SK with graph + SK data this match)')
        _sk_field('sk_mineral_count', 0x04, 'sk: live mineral anchors this map')
        _sk_field('sk_bin_count', 0x04, 'sk: authored bins this map (== MaxPlayers)')
        _sk_field('sk_return_lo', 0x04,
                  'sk: RETURN-threshold roll lower bound (per-bot roll in [lo, hi])')
        _sk_field('sk_return_hi', 0x04,
                  'sk: RETURN-threshold roll upper bound')
        _sk_field('sk_def_ore', 0x04,
                  'sk: resolved "Ore Deposits" item-def ptr (per match; 0 = unresolved)')
        _sk_field('sk_def_crystal', 0x04,
                  'sk: resolved "Crystals" item-def ptr (per match; 0 = unresolved)')
        _sk_field('sk_spill', 0x04, 'sk: loop index spill surviving engine calls')
        _sk_field('sk_carry_tmp', 0x04, 'sk: carried-mineral count spill')
        _sk_field('sk_pile_next', 0x04, 'sk: pile ring-table write cursor')
        _sk_field('sk_pile_pursue_radius_sq', 0x04, 'sk: pile opportunistic divert radius^2 (float)')
        _sk_field('sk_pile_reached_radius_sq', 0x04, 'sk: pile divert arrival radius^2 (float)')
        _sk_field('sk_pile_ttl', 0x04, 'sk: pile entry lifetime in frames (ring TTL seed)')
        _sk_field('sk_mineral_table', sk_mineral_max_capped * 8,
                  'sk: live mineral anchor positions (float[2] each)')
        _sk_field('sk_mineral_node', sk_mineral_max_capped * 4,
                  'sk: nearest graph node per mineral (-1 = unbound)')
        _sk_field('sk_bin_table', sk_bin_max_capped * 8,
                  'sk: bin center per TEAM id (float[2]; only valid slots meaningful)')
        _sk_field('sk_bin_valid', sk_bin_max_capped * 4,
                  'sk: 1 = this team id has an authored bin on this map')
        _sk_field('sk_bin_node', sk_bin_max_capped * 4,
                  'sk: nearest graph node per bin (-1 = unbound)')
        # Per-bot state: contiguous so load_sk clears it with one rep stosd
        # and the R chunk dumps it in one block (8 parallel u32[16] arrays).
        _sk_field('bot_sk_return', MAX_BOT_SLOTS * 4,
                  'sk: per-bot RETURN-phase latch (1 until the deposit empties the load)')
        _sk_field('bot_sk_carry', MAX_BOT_SLOTS * 4,
                  'sk: per-bot last computed carried-mineral count (diagnostic)')
        _sk_field('bot_sk_dep_try', MAX_BOT_SLOTS * 4,
                  'sk: per-bot deposit press-patience cycles used')
        _sk_field('bot_pile_target', MAX_BOT_SLOTS * 4,
                  'sk: per-bot latched pile divert (pile idx+1, 0 = none)')
        _sk_field('bot_pile_cd', MAX_BOT_SLOTS * 4,
                  'sk: per-bot pile divert cooldown (thinks)')
        _sk_field('bot_pile_try', MAX_BOT_SLOTS * 4,
                  'sk: per-bot pile press-patience cycles used')
        _sk_field('bot_pile_best', MAX_BOT_SLOTS * 4,
                  'sk: per-bot pile divert min dsq (float; FLT_MAX parked)')
        _sk_field('bot_sk_thresh', MAX_BOT_SLOTS * 4,
                  'sk: per-bot rolled RETURN threshold (0 = unrolled sentinel; re-rolled per banked run)')
        _sk_field('sk_pile_valid', sk_pile_max_capped * 4,
                  'sk: pile ring slot TTL countdown (frames; 0 = empty/expired)')
        _sk_field('sk_pile_pos', sk_pile_max_capped * 8,
                  'sk: pile ring slot position (float[2], from the drop-action detour)')
        _sk_field('tag_skstate', 0x10, 'diag: SK state dump chunk tag')
        # Cold data after the tag: static pack + BFS rows (excluded from the
        # R chunk like seek_dist / drop_dist).
        _sk_field('sk_static_map_count', 0x04, 'sk: packed SK map records')
        _sk_field('sk_static_maps',
                  max(0, sk_static_map_max) * (max(0, sk_map_name_slot) + 16),
                  'sk: map name + (mineral_count, mineral_first, bin_count, bin_first)')
        _sk_field('sk_static_minerals', max(0, sk_static_mineral_max) * 8,
                  'sk: all maps\' mineral anchors (float[2] each, parse order)')
        _sk_field('sk_static_bins', max(0, sk_static_bin_max) * 12,
                  'sk: all maps\' bins (x f32, y f32, team u32)')
        _sk_field('sk_ore_dist', overlay_vertex_max_capped * 4,
                  'sk: multi-source mineral BFS field (0 = mineral-bearing node)')
        _sk_field('sk_bin_dist', sk_bin_max_capped * overlay_vertex_max_capped * 4,
                  'sk: per-bin BFS rows, team-major (row = team * VMAX)')

    c.sk_pile_max_capped = sk_pile_max_capped



def extend_goody(c):
    item_table_max = c.item_table_max
    item_static_map_max = c.item_static_map_max
    item_static_point_max = c.item_static_point_max
    item_map_name_slot = c.item_map_name_slot
    item_categories = c.item_categories
    fields = c.fields
    overlay_fields = c.overlay_fields
    overlay_vertex_max_capped = c.overlay_vertex_max_capped
    sk_pile_max_capped = c.sk_pile_max_capped


    # --- Generalized GOODY pursuit (graph-routed piles + filler items) -------
    # Appended at the very tail. The block from item_routing_active through
    # sk_pile_node is CONTIGUOUS and dumped whole by the R-snapshot `goody`
    # chunk; the static pack + BFS fields sit after the tag (excluded, like
    # every other dist field). item_dist is one multi-source row per filler
    # CATEGORY (health/energy/shield), built once per match — fillers respawn
    # in place, no presence tracking; sk_pile_dist is the multi-source row
    # over the live pile ring's bound nodes, rebuilt event-driven via
    # sk_pile_dirty (registration / TTL expiry / grab).
    item_table_max_capped = max(0, item_table_max)
    item_cats_capped      = max(0, item_categories)
    if (item_table_max_capped > 0 and item_cats_capped > 0
            and overlay_vertex_max_capped > 0):
        gd_off = max([f.end for f in fields] + [f.end for f in overlay_fields])
        gd_off = (gd_off + 7) & ~7

        def _gd_field(name, size, desc):
            nonlocal gd_off
            overlay_fields.append(ScratchField(name, gd_off, size, desc))
            gd_off += size

        _gd_field('item_routing_active', 0x04,
                  'goody: 1 = filler-item fields built this match (any mode)')
        _gd_field('item_count', 0x04, 'goody: live filler anchors this map')
        _gd_field('goody_tx', 0x04, 'goody: resolved pursuit target x (float, per think)')
        _gd_field('goody_ty', 0x04, 'goody: resolved pursuit target y (float, per think)')
        _gd_field('goody_node', 0x04, 'goody: resolved target\'s bound graph node (-1)')
        _gd_field('goody_idx', 0x04, 'goody: resolved target index (pile slot / item idx)')
        _gd_field('goody_scan_rad', 0x04,
                  'goody: nearest-scan radius^2 input (float bits; FLT_MAX = unlimited)')
        _gd_field('goody_scan_cat', 0x04,
                  'goody: nearest-item-scan category filter (-1 = any)')
        _gd_field('item_pursue_radius_sq', 0x04,
                  'goody: filler-item opportunistic divert radius^2 (float)')
        _gd_field('goody_direct_radius_sq', 0x04,
                  'goody: straight-steer (direct phase) radius^2, piles + items (float)')
        _gd_field('goody_abandon_radius_sq', 0x04,
                  'goody: silently unlatch beyond this d^2 (float)')
        _gd_field('weapon_pursue_radius_sq', 0x04,
                  'goody: weapon-pickup priority divert radius^2 (float; larger '
                  'than the filler radius — arming up is worth a longer walk)')
        _gd_field('weapon_chance_max', 0x04,
                  'goody: weapon-roll chance at d=0 (float 0..100; scales '
                  'linearly to 0 at the radius edge)')
        if sk_pile_max_capped > 0:
            _gd_field('sk_pile_dirty', 0x04,
                      'goody: 1 = pile set changed, rebuild sk_pile_dist next flip')
            _gd_field('sk_pile_node', sk_pile_max_capped * 4,
                      'goody: nearest graph node per pile ring slot (-1 = unbound)')
        _gd_field('tag_goody', 0x10, 'diag: goody pursuit dump chunk tag')
        # Cold data after the tag.
        _gd_field('item_static_map_count', 0x04, 'goody: packed filler map records')
        _gd_field('item_static_maps',
                  max(0, item_static_map_max) * (max(0, item_map_name_slot) + 8),
                  'goody: map name + (item_count, item_first)')
        _gd_field('item_static_points', max(0, item_static_point_max) * 12,
                  'goody: all maps\' filler anchors (x f32, y f32, category u32)')
        _gd_field('item_table', item_table_max_capped * 8,
                  'goody: live filler anchor positions (float[2] each)')
        _gd_field('item_cat', item_table_max_capped * 4,
                  'goody: live category per anchor (0 health / 1 energy / '
                  '2 shield / 3 weapon)')
        _gd_field('item_node', item_table_max_capped * 4,
                  'goody: nearest graph node per filler (-1 = unbound)')
        _gd_field('item_dist', item_cats_capped * overlay_vertex_max_capped * 4,
                  'goody: per-category multi-source BFS rows (row = cat * VMAX)')
        if sk_pile_max_capped > 0:
            _gd_field('sk_pile_dist', overlay_vertex_max_capped * 4,
                      'goody: multi-source BFS row over live pile nodes (event-rebuilt)')


