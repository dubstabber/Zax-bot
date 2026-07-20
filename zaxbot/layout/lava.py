"""Lava census/query temps + proactive-avoidance knobs."""

from .model import ScratchField


def extend_lava(c):
    overlay_fields = c.overlay_fields
    div_base = c.div_base
    div_stride = c.div_stride
    plasma_tmp_base = c.plasma_tmp_base

    # --- Lava census/query temps (tail; not dumped) -----------------------
    # plasma_grid is the grid object ptr that plasma_get / plasma_census
    # operate on (footprint @plasma+0x08 or heat @plasma+0x2C6C); plasma_cn_*
    # accumulate the whole-grid nonzero census the snapshot copies into
    # plasma_diag. Anchored after the last per-bot block so it never collides.
    plasma_tmp_base = div_base + 0x18 + 5 * div_stride
    overlay_fields.extend([
        ScratchField('plasma_grid',     plasma_tmp_base + 0x00, 0x04,
                     'lava: grid object ptr for plasma_get/plasma_census'),
        ScratchField('plasma_cn_count', plasma_tmp_base + 0x04, 0x04,
                     'lava census: nonzero-cell count for the grid in plasma_grid'),
        ScratchField('plasma_cn_max',   plasma_tmp_base + 0x08, 0x04,
                     'lava census: max cell value seen'),
        ScratchField('plasma_cn_first', plasma_tmp_base + 0x0C, 0x04,
                     'lava census: first nonzero tile (tx<<16 | ty), 0xFFFFFFFF=none'),
        # Full per-tile heat-grid snapshot (row-major bytes, tw*th <= 0x800),
        # filled by plasma_dump_heat and dumped via the 'pheat' chunk so the
        # whole lava layout + value distribution is visible in one R-press.
        ScratchField('plasma_heatmap',  plasma_tmp_base + 0x10, 0x800,
                     'lava diag: per-tile heat bytes (row-major, tw wide)'),
        # --- Proactive lava-avoidance knobs (static, packed by static_data) +
        # per-call veto temps. Live after the heatmap in the tail.
        ScratchField('lava_avoid_enabled',  plasma_tmp_base + 0x810, 0x04,
                     'lava: master enable (0 = no proactive veto)'),
        ScratchField('lava_heat_threshold', plasma_tmp_base + 0x814, 0x04,
                     'lava: heat value (0..255) at/above which a tile is lava'),
        ScratchField('lava_lookahead_px',   plasma_tmp_base + 0x818, 0x04,
                     'lava: world-px lookahead distance along heading (float)'),
        ScratchField('lava_sweep_step',     plasma_tmp_base + 0x81C, 0x04,
                     'lava: heading sweep step (float radians; from LAVA_SWEEP_STEP_DEG)'),
        ScratchField('lava_veto_angle',     plasma_tmp_base + 0x820, 0x04,
                     'lava veto: candidate heading (float radians, per-call temp)'),
        ScratchField('lava_veto_cos',       plasma_tmp_base + 0x824, 0x04,
                     'lava veto: cos(candidate) (per-call temp)'),
        ScratchField('lava_veto_sin',       plasma_tmp_base + 0x828, 0x04,
                     'lava veto: sin(candidate) (per-call temp)'),
        ScratchField('lava_k',              plasma_tmp_base + 0x82C, 0x04,
                     'lava veto: sweep iteration counter (per-call temp)'),
        ScratchField('lava_dbg_heat',       plasma_tmp_base + 0x830, 0x04,
                     'lava diag: heat value is_plasma_at read (post-warm); dumped as plasma diag[19]'),
        # Reactive lava flee (health-damage -> reverse heading). Static knobs;
        # the per-bot flee countdown reuses the dormant bot_wander_ticks field.
        ScratchField('lava_flee_enabled',   plasma_tmp_base + 0x834, 0x04,
                     'lava flee: master enable (0 = no reactive flee)'),
        ScratchField('lava_flee_frames',    plasma_tmp_base + 0x838, 0x04,
                     'lava flee: frames to reverse heading after health damage'),
        # Edge-following (hug the prev->current connection line, vital on narrow
        # lava corridors). Knob + per-call FPU temps for the segment projection.
        ScratchField('wp_edge_follow_enabled', plasma_tmp_base + 0x83C, 0x04,
                     'follow: 1 = steer toward a look-ahead point ON the edge line; 0 = straight at node'),
        ScratchField('wp_seg_x',             plasma_tmp_base + 0x840, 0x04,
                     'follow: edge segment dx (current.x - prev.x), per-call temp'),
        ScratchField('wp_seg_y',             plasma_tmp_base + 0x844, 0x04,
                     'follow: edge segment dy (current.y - prev.y), per-call temp'),
        ScratchField('wp_tp',                plasma_tmp_base + 0x848, 0x04,
                     'follow: clamped look-ahead param along the edge, per-call temp'),
        ScratchField('overlay_cull_min_x',   plasma_tmp_base + 0x84C, 0x04,
                     'overlay: screen-space cull min x (float)'),
        ScratchField('overlay_cull_max_x',   plasma_tmp_base + 0x850, 0x04,
                     'overlay: screen-space cull max x (float)'),
        ScratchField('overlay_cull_min_y',   plasma_tmp_base + 0x854, 0x04,
                     'overlay: screen-space cull min y (float)'),
        ScratchField('overlay_cull_max_y',   plasma_tmp_base + 0x858, 0x04,
                     'overlay: screen-space cull max y (float)'),
    ])

    c.plasma_tmp_base = plasma_tmp_base

