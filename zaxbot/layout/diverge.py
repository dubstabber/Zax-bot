"""Per-node movement-divergence levels (1-3) + per-bot offset state.

Appended at the very tail so no existing offset shifts. ``wp_node_level``
holds one byte per authored node (1 = strict edge-hug follow, 2 = loose,
3 = free; see ``config/movement.py``). It is default-filled with 1s at
build time and re-filled by ``wp_load`` on every match change (v2 .zwpt
carries the bytes; a v1 file or a fresh map defaults to all-1s), so the
per-bot state below needs NO df90 clear — ``bot_div_node`` stores the
rolled-for node **+1** (0 = never rolled) and the steer path re-rolls the
moment it mismatches ``current_wp``, which self-heals across respawns,
graph reloads and match changes.

``wp_lvl_radius_sq`` / ``wp_lvl_offset_max`` are 4-entry tables indexed by
the level byte masked to 0..3 (slot 0 mirrors level 1 as corrupt-byte
defense). ``wp_lvl_glyphs`` packs the overlay's line-drawn digit labels:
4 entries x GLYPH_STRIDE, each ``u32 seg_count`` + up to GLYPH_MAX_SEGS
segments of 4 floats (dx1,dy1,dx2,dy2 relative to the node center);
entry 0 has count 0 so a masked corrupt level draws nothing.
"""

from .model import ScratchField

GLYPH_MAX_SEGS = 5
GLYPH_STRIDE = 4 + GLYPH_MAX_SEGS * 16   # u32 count + 5 segs x 4 floats


def extend_diverge(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    overlay_fields = c.overlay_fields
    vmax = c.overlay_vertex_max_capped

    if not vmax:
        return

    base = max(f.end for f in fields + overlay_fields)
    base = (base + 0xF) & ~0xF
    off = base

    def field(name, size, note):
        nonlocal off
        f = ScratchField(name, off, size, note)
        off += size
        return f

    overlay_fields.extend([
        field('wp_node_level', vmax,
              'diverge: per-node follow level byte (1 strict / 2 loose / '
              '3 free); default 1, persisted in .zwpt v2'),
        field('wp_lvl_radius_sq', 0x10,
              'diverge: arrival radius^2 float[4] indexed by level&3 '
              '(slot 0 = level-1 default)'),
        field('wp_lvl_offset_max', 0x10,
              'diverge: per-bot offset max px u32[4] indexed by level&3'),
        field('wp_lvl_glyphs', 4 * GLYPH_STRIDE,
              'diverge: overlay digit glyph segments per level '
              '(count + dx1,dy1,dx2,dy2 x5, node-relative)'),
        field('bot_div_node', MAX_BOT_SLOTS * 4,
              'diverge: node+1 the per-bot offset was rolled for (0 = none)'),
        field('bot_div_x', MAX_BOT_SLOTS * 4,
              'diverge: per-bot lateral offset x (float px)'),
        field('bot_div_y', MAX_BOT_SLOTS * 4,
              'diverge: per-bot lateral offset y (float px)'),
        field('wp_div_tmp', 0x04,
              'diverge: fild staging for the RNG offset roll'),
        field('ov_glyph_base', 0x08,
              'diverge: overlay glyph pass screen-space node base float[2]'),
        field('ov_glyph_n', 0x04,
              'diverge: overlay glyph pass remaining-segment counter'),
    ])
