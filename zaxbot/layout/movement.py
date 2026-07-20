"""Bot movement / wander per-bot AI block + plasma-map globals."""

from .model import AI_PERBOT_FIELDS, AI_PERBOT_FIELD_COUNT, BOT_STATE_FIELDS, ScratchField


def extend_movement(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    AI_HAZARD_CAP = c.AI_HAZARD_CAP
    ai_off = c.ai_off

    # --- Bot movement / wander state (DM-only first pass) -------------------
    # Per-bot fields (MAX_BOT_SLOTS * 4 each) live at 0x1A60+. They are NOT
    # appended to BOT_STATE_FIELDS because the per-call fire/aim block uses
    # `bot_state_end + N` relative offsets while `host_part` is anchored at
    # absolute 0x2F0 — adding to BOT_STATE_FIELDS would push cand_pos onto
    # host_part. Standalone fields here avoid that shift entirely.
    #
    # frame_counter is incremented once per movement detour invocation; the
    # item-scan stagger uses it to spread cost across frames. hazard_table is
    # a packed array of (x:float, y:float, radius_sq:float), populated once
    # per match by detour_df90 -> scan_hazards.
    AI_BASE       = 0x1A60
    AI_STRIDE     = MAX_BOT_SLOTS * 4                # 0x40 per per-bot field
    # The field list, the "last three are the nav indices" invariant, and the
    # df90/snapshot count-coupling are documented on the module-level
    # AI_PERBOT_FIELDS / AI_PERBOT_FIELD_COUNT so the two consumers derive their
    # sizes from the constant instead of hardcoding 15.
    ai_off = AI_BASE
    for ai_name, ai_note in AI_PERBOT_FIELDS:
        fields.append(ScratchField(ai_name, ai_off, AI_STRIDE, ai_note))
        ai_off += AI_STRIDE
    # Scalars and hazard cache (hazard_table is 32 entries × 12 B).
    AI_HAZARD_CAP = 32
    fields.extend([
        ScratchField('frame_counter', ai_off,        0x04, 'movement: per-detour frame tick'),
        ScratchField('hazard_count',  ai_off + 0x04, 0x04, 'movement: live hazard_table entries'),
        ScratchField(
            'hazard_table',
            ai_off + 0x08,
            AI_HAZARD_CAP * 12,
            'movement: (x, y, radius_sq) float triples; populated by detour_df90',
        ),
        # Movement static knobs (packed at build time by static_data).
        ScratchField('movement_enabled',          ai_off + 0x08 + AI_HAZARD_CAP * 12,         0x04,
                     'movement: master enable flag (0 = original zero-vector behavior)'),
        ScratchField('wander_target_radius',      ai_off + 0x0C + AI_HAZARD_CAP * 12,         0x04,
                     'movement: ±radius for random target picks (float)'),
        ScratchField('wander_target_timeout',     ai_off + 0x10 + AI_HAZARD_CAP * 12,         0x04,
                     'movement: frame timeout before re-rolling target'),
        ScratchField('stuck_frames_threshold',    ai_off + 0x14 + AI_HAZARD_CAP * 12,         0x04,
                     'movement: stuck-frames count that forces retarget'),
        ScratchField('stuck_delta_sq',            ai_off + 0x18 + AI_HAZARD_CAP * 12,         0x04,
                     'movement: float² threshold for "didn\'t move"'),
        ScratchField('item_attractor_radius_sq',  ai_off + 0x1C + AI_HAZARD_CAP * 12,         0x04,
                     'attractor: float² reach for pickup attractor'),
        ScratchField('item_attractor_weight',     ai_off + 0x20 + AI_HAZARD_CAP * 12,         0x04,
                     'attractor: blend weight (float)'),
        ScratchField('item_scan_interval',        ai_off + 0x24 + AI_HAZARD_CAP * 12,         0x04,
                     'attractor: frames between pickup scans per bot'),
        ScratchField('hazard_repulsion_radius_sq', ai_off + 0x28 + AI_HAZARD_CAP * 12,        0x04,
                     'hazard: float² reach for repulsion'),
        ScratchField('hazard_repulsion_weight',   ai_off + 0x2C + AI_HAZARD_CAP * 12,         0x04,
                     'hazard: blend weight (float)'),
        ScratchField('hazard_default_radius_sq',  ai_off + 0x30 + AI_HAZARD_CAP * 12,         0x04,
                     'hazard: per-entity bubble radius² (float)'),
        ScratchField('bot_move_speed',            ai_off + 0x34 + AI_HAZARD_CAP * 12,         0x04,
                     'movement: per-frame velocity magnitude (float)'),
        ScratchField('hazard_flee_frames',        ai_off + 0x38 + AI_HAZARD_CAP * 12,         0x04,
                     'reactive: frames to commit to flee target after damage'),
        # Waypoint-following knobs (static, packed at build time by static_data).
        ScratchField('wp_follow_enabled',         ai_off + 0x3C + AI_HAZARD_CAP * 12,         0x04,
                     'follow: master enable flag (0 = original random wander)'),
        ScratchField('wp_reached_radius_sq',      ai_off + 0x40 + AI_HAZARD_CAP * 12,         0x04,
                     'follow: arrival radius² for advancing to the next node (float)'),
        ScratchField('wp_edge_lookahead',         ai_off + 0x44 + AI_HAZARD_CAP * 12,         0x04,
                     'follow: edge-steer look-ahead as a fraction of edge length (float, ~0.15)'),
        # Per-tick scratch (used inside the bot_movement detour).
        ScratchField('move_tmp_pos',              ai_off + 0x48 + AI_HAZARD_CAP * 12,         0x08,
                     'movement: scratch (x, y) for sub_4FB0A0 reads'),
        # Waypoint-diagnostic raw-dword scratch. ``wp_compute`` populates
        # eight contiguous u32 fields:
        #   [+0x00] MGR ptr        (dword_713F14 content)
        #   [+0x04] WM ptr         (dword_6C2080 content; MGR == WM at runtime)
        #   [+0x08] LV ptr         (mgr.vtbl[0x184]() — NOT a CLayer; junk
        #                           at +0x134 confirmed in earlier R dump)
        #   [+0x0C] WPM ptr        ([LV + 0x134]; kept for comparison)
        #   [+0x10] char count     ([WM + 0x294])
        #   [+0x14] layer_arr ptr  ([WM + 0x2BC] — array of layers per
        #                           sub_4F1050 disasm; count at +0x2C0
        #                           reads 1 in MP, so element 0 is the
        #                           active layer)
        #   [+0x18] LAY ptr        ([layer_arr + 0] — the active CLayer;
        #                           this is what sub_4ECA80 stores the
        #                           CWayPointMap on)
        #   [+0x1C] WPM_REAL ptr   ([LAY + 0x134] — the actual CWayPointMap
        #                           if our hypothesis is right)
        # Two raw-bytes chunks accompany this struct in the snapshot:
        # ``wp_lv`` (0x200 bytes from LV) for offline post-mortem of the
        # vtbl[0x184] object, and ``wp_lay`` (0x200 bytes from LAY) to
        # confirm the CLayer hypothesis and locate the CWayPointMap.
        ScratchField('wp_diag_data',              ai_off + 0x50 + AI_HAZARD_CAP * 12,         0x20,
                     'waypoint diag: 8 raw u32 fields populated by wp_compute'),
        # Off-graph recovery knobs (static, packed at build time). Live in the
        # gap between wp_diag_data (ends +0x70) and OVERLAY_BASE (0x2080).
        ScratchField('wp_progress_timeout',       ai_off + 0x70 + AI_HAZARD_CAP * 12,         0x04,
                     'follow: frames of no-progress-toward-target before recover (int)'),
        ScratchField('wp_relocate_frames',        ai_off + 0x74 + AI_HAZARD_CAP * 12,         0x04,
                     'follow: repurposed dormant slot; stuck-near-node arrival radius^2 (float)'),
        # Wall-slide angle step (radians) added to the emitted movement angle
        # per deflection ramp step when a bot is wedged against geometry. See
        # detours/bot_movement.py (the node-to-node follower + wall-slide).
        ScratchField('wp_slide_turn_step',        ai_off + 0x78 + AI_HAZARD_CAP * 12,         0x04,
                     'follow: wall-slide angle step per ramp (float radians)'),
        # --- Lava (plasma) detection globals (NOT per-bot). Captured once per
        # match by scan_plasma; queried per-frame by is_plasma_at. plasma_map is
        # the vtable-validated CPlasmaTileMap* (0 on non-plasma maps => no-op).
        # plasma_qx/qy are is_plasma_at's world-coord inputs; plasma_tx/ty its
        # idiv temps. plasma_diag is the R-snapshot pin buffer (20 u32 slots):
        #   [0] LAY  [1] *(LAY+0x7C)  [2] *(LAY+0x40)  [3] chosen(=plasma_map)
        #   [4] tilepx  [5] tw  [6] th  [7] host_x  [8] host_y
        #   [9] host_tx [10] host_ty [11] footprint@host  [12] heat@host
        #   [13] fp_count [14] heat_count [15] fp_max [16] heat_max
        #   [17] fp_first(tx<<16|ty) [18] heat_first  [19] spare
        # The census counts (whole-grid nonzero cells) disambiguate which grid
        # (footprint @+0x08 vs heat/elevation @+0x2C6C) marks damaging lava,
        # robustly to the fire animation and to the host's exact tile.
        ScratchField('plasma_map',   ai_off + 0x7C + AI_HAZARD_CAP * 12, 0x04,
                     'lava: vtable-validated CPlasmaTileMap* (0 if no plasma map)'),
        ScratchField('plasma_qx',    ai_off + 0x80 + AI_HAZARD_CAP * 12, 0x04,
                     'lava: is_plasma_at input world x (int)'),
        ScratchField('plasma_qy',    ai_off + 0x84 + AI_HAZARD_CAP * 12, 0x04,
                     'lava: is_plasma_at input world y (int)'),
        ScratchField('plasma_tx',    ai_off + 0x88 + AI_HAZARD_CAP * 12, 0x04,
                     'lava: is_plasma_at tile-x idiv temp'),
        ScratchField('plasma_ty',    ai_off + 0x8C + AI_HAZARD_CAP * 12, 0x04,
                     'lava: is_plasma_at tile-y idiv temp'),
        ScratchField('plasma_diag',  ai_off + 0x90 + AI_HAZARD_CAP * 12, 0x50,
                     'lava diag: 20 u32 pin slots (see comment); dumped by the plasma chunk'),
    ])

    c.AI_HAZARD_CAP = AI_HAZARD_CAP
    c.ai_off = ai_off

