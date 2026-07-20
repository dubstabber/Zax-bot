"""Pickup registration table + (dormant) stage-2 divert state."""

from .model import ScratchField, ScratchLayout


def extend_pickup_table(c):
    pickup_table_max = c.pickup_table_max
    overlay_color_size = c.overlay_color_size
    overlay_fields = c.overlay_fields
    wp_io_off = c.wp_io_off
    pickup_base = c.pickup_base
    pickup_table_max_capped = c.pickup_table_max_capped

    # --- Proximity-pickup tracking (item-grab feature, stage 1) ------------
    # Placed after the dynamic overlay/IO region so growing OVERLAY_VERTEX_MAX
    # / EDGE_MAX never collides (and ScratchLayout.validate() would catch it
    # if it ever did). pickup_table is a flat (x:float, y:float) array rebuilt
    # every frame by detour_53DA40 (the per-pickup CPickupAI update). world_frame
    # is bumped once per frame by the page-flip detour; pickup_register does a
    # lazy reset of the table on the first registration after world_frame
    # changes, so any reader (overlay now, bot AI later) always sees a complete
    # frame's worth. overlay_pickup_color is a CColor rebuilt each frame by
    # sub_53F010 (orange markers).
    pickup_base = wp_io_off + 0x200
    pickup_table_max_capped = max(0, pickup_table_max)
    overlay_fields.extend([
        ScratchField('pickup_register_enabled', pickup_base + 0x00, 0x04,
                     'pickup: master enable for pickup self-registration'),
        ScratchField('world_frame',             pickup_base + 0x04, 0x04,
                     'pickup: per-frame counter (bumped by page-flip detour)'),
        ScratchField('pickup_last_frame',       pickup_base + 0x08, 0x04,
                     'pickup: world_frame value at the last table reset'),
        ScratchField('pickup_count',            pickup_base + 0x0C, 0x04,
                     'pickup: live entries in pickup_table this frame'),
        ScratchField('pickup_reg_tmp',          pickup_base + 0x10, 0x08,
                     'pickup: float[2] scratch for sub_4FB0A0 entity-pos reads'),
        ScratchField('overlay_pickup_color',    pickup_base + 0x18, overlay_color_size,
                     'overlay: detected-pickup CColor (rebuilt per-frame)'),
    ])
    if pickup_table_max_capped > 0:
        overlay_fields.append(ScratchField(
            'pickup_table', pickup_base + 0x28,
            pickup_table_max_capped * 8,
            'pickup: float[2] per detected pickup (world coords)',
        ))

    c.pickup_base = pickup_base
    c.pickup_table_max_capped = pickup_table_max_capped



def extend_pickup_divert(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    overlay_fields = c.overlay_fields
    pickup_base = c.pickup_base
    pickup_table_max_capped = c.pickup_table_max_capped
    div_base = c.div_base
    div_stride = c.div_stride

    # --- Stage-2 pickup-divert state (after the table so it follows it) ----
    # Five static knobs (packed by static_data) + five per-bot u32 arrays
    # (indexed by slot like the other AI per-bot fields). Placed outside the
    # constrained 15-field AI block; cleared on respawn by detour_542360.
    div_base = pickup_base + 0x28 + pickup_table_max_capped * 8
    div_stride = MAX_BOT_SLOTS * 4
    overlay_fields.extend([
        ScratchField('pickup_divert_enabled',    div_base + 0x00, 0x04,
                     'pickup divert: master enable flag'),
        ScratchField('pickup_divert_radius_sq',  div_base + 0x04, 0x04,
                     'pickup divert: trigger radius² (float)'),
        ScratchField('pickup_reached_radius_sq', div_base + 0x08, 0x04,
                     'pickup divert: arrival radius² (float)'),
        ScratchField('pickup_cooldown_frames',   div_base + 0x0C, 0x04,
                     'pickup divert: post-grab cooldown (frames)'),
        ScratchField('pickup_divert_timeout',    div_base + 0x10, 0x04,
                     'pickup divert: max frames per divert (backstop)'),
        ScratchField('pickup_divert_avoid_damage', div_base + 0x14, 0x04,
                     'pickup divert: react to char+0x7C damage (lava) — abandon divert'),
        ScratchField('pickup_cd',         div_base + 0x18 + 0 * div_stride, div_stride,
                     'pickup divert: per-bot cooldown counter'),
        ScratchField('pickup_div_active', div_base + 0x18 + 1 * div_stride, div_stride,
                     'pickup divert: per-bot diverting flag (0/1)'),
        ScratchField('pickup_div_x',      div_base + 0x18 + 2 * div_stride, div_stride,
                     'pickup divert: per-bot latched target x (float)'),
        ScratchField('pickup_div_y',      div_base + 0x18 + 3 * div_stride, div_stride,
                     'pickup divert: per-bot latched target y (float)'),
        ScratchField('pickup_div_try',    div_base + 0x18 + 4 * div_stride, div_stride,
                     'pickup divert: per-bot divert-frame counter'),
    ])

    c.div_base = div_base
    c.div_stride = div_stride

