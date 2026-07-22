"""Combat-strafe (dodge weave) state.

Appended at the tail. ``bot_enemy_dx/dy`` are stamped by the fire
detour's pick_target alongside the per-bot ``bot_enemy_near`` flag and
only consumed while that (per-frame-fresh) flag is set, so staleness
needs no clearing. ``strafe_tmp`` receives the GAIN immediate at emit
time (x87 cannot load immediates)."""

from .model import ScratchField


def extend_fight(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    overlay_fields = c.overlay_fields

    base = max(f.end for f in fields + overlay_fields)
    base = (base + 0xF) & ~0xF

    overlay_fields.extend([
        ScratchField('bot_enemy_dx', base, MAX_BOT_SLOTS * 4,
                     'fight: per-bot vector to the picked enemy, x (float; valid while bot_enemy_near)'),
        ScratchField('bot_enemy_dy', base + MAX_BOT_SLOTS * 4, MAX_BOT_SLOTS * 4,
                     'fight: per-bot vector to the picked enemy, y'),
        ScratchField('strafe_tmp', base + MAX_BOT_SLOTS * 8, 0x04,
                     'fight: strafe-gain immediate staging (x87 has no imm loads)'),
        ScratchField('frame_tick', base + MAX_BOT_SLOTS * 8 + 0x04, 0x04,
                     'fight: TRUE per-frame tick (page flip); frame_counter is per bot THINK'),
    ])
