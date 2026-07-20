"""Roam switch wander-bump state."""

from .model import ScratchField


def extend_switch_wander(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    overlay_fields = c.overlay_fields


    # --- Roam switch wander-bump --------------------------------------------
    # Appended at the very tail (after the dropped-flag block) so no existing
    # scratch offset shifts. bot_switch_target latches a per-bot final
    # approach at a switch center, rolled by switch_wander_check when a
    # ROAMING bot arrives at a switch's bound node with >=1 paired door
    # blocked; bot_switch_snap holds the blocked-paired-door census at latch
    # time (a change = the bump fired). The block from bot_switch_target
    # through sww_census is CONTIGUOUS and dumped whole by the R-snapshot
    # `swander` chunk. Gated on the switch block's presence via its fields
    # (switch_table_max_capped is unbound on switchless builds).
    if any(f.name == 'switch_node' for f in overlay_fields):
        sww_base = max(
            [f.end for f in fields] + [f.end for f in overlay_fields]
        )
        sww_base = (sww_base + 7) & ~7
        overlay_fields.extend([
            ScratchField('bot_switch_target', sww_base,
                         MAX_BOT_SLOTS * 4,
                         'swander: per-bot latched switch bump approach (switch idx+1, 0 = none)'),
            ScratchField('bot_switch_cd',
                         sww_base + MAX_BOT_SLOTS * 4,
                         MAX_BOT_SLOTS * 4,
                         'swander: per-bot re-roll cooldown (thinks; armed after any bump attempt)'),
            ScratchField('bot_switch_try',
                         sww_base + MAX_BOT_SLOTS * 8,
                         MAX_BOT_SLOTS * 4,
                         'swander: per-bot press-patience cycles used while latched'),
            ScratchField('bot_switch_snap',
                         sww_base + MAX_BOT_SLOTS * 12,
                         MAX_BOT_SLOTS * 4,
                         'swander: blocked-paired-door census at latch time (change = bump fired)'),
            ScratchField('switch_wander_chance',
                         sww_base + MAX_BOT_SLOTS * 16, 0x04,
                         'swander: RNG(0..99) < this bumps an adjacent blocked switch while roaming (0 = off)'),
            ScratchField('sww_spill',
                         sww_base + MAX_BOT_SLOTS * 16 + 0x04, 0x04,
                         'swander: matched-switch / bind-loop index spill surviving engine calls'),
            ScratchField('sww_census',
                         sww_base + MAX_BOT_SLOTS * 16 + 0x08, 0x04,
                         'swander: blocked-paired-door census spill (roll time; copied to bot_switch_snap on latch)'),
            ScratchField('tag_swander',
                         sww_base + MAX_BOT_SLOTS * 16 + 0x0C, 0x10,
                         'diag: switch wander-bump dump chunk tag'),
        ])


