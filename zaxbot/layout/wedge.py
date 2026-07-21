"""Wedge-cluster escape + fight-stall state."""

from .model import ScratchField


def extend_wedge(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    overlay_fields = c.overlay_fields
    overlay_vertex_max_capped = c.overlay_vertex_max_capped


    # --- Wedge-cluster escape + fight-stall state (2026-07-20) --------------
    # Appended at the very tail so no existing offset shifts. wpfn_excl feeds
    # wp_find_nearest_ex (the wedge HARD RESET acquires the nearest node
    # OUTSIDE the wedge cluster); bot_wedge_cycles counts consecutive
    # recovery actions without an arrival; bot_enemy_near is stamped per
    # frame by the fire detour's pick_target so the movement watchdog can
    # tell a fight stall from geometry. tag_wedge names the R-snapshot chunk
    # that dumps the whole block.
    if overlay_vertex_max_capped > 0:
        wedge_off = max(f.end for f in fields + overlay_fields)
        wedge_off = (wedge_off + 0xF) & ~0xF
        overlay_fields.extend([
            ScratchField('wpfn_excl', wedge_off, 0x10,
                         'nav: 4 node ids wp_find_nearest_ex skips (hard reset writes all 4; -1 = none)'),
            ScratchField('wpfn_tmp', wedge_off + 0x10, 0x04,
                         'nav: FLT_MAX staging for the wp_find_nearest_ex best seed'),
            ScratchField('bot_wedge_cycles', wedge_off + 0x14, MAX_BOT_SLOTS * 4,
                         'nav: consecutive wedge-recovery actions without an arrival; hard reset at WP_WEDGE_RESET_CYCLES'),
            ScratchField('bot_enemy_near', wedge_off + 0x54, MAX_BOT_SLOTS * 4,
                         'fight: 1 = pick_target saw an enemy within FIGHT_STALL_RADIUS_SQ this frame'),
            ScratchField('flag_give_block_count', wedge_off + 0x94, 0x04,
                         'ctf: cumulative duplicate-flag gives suppressed by detour_5B4DA0 (diag; in the wedge R-chunk)'),
            ScratchField('tag_wedge', wedge_off + 0x98, 0x10,
                         'diag: wedge/fight state dump chunk tag'),
        ])


