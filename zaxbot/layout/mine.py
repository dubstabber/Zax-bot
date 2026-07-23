"""Proximity-mine live state: TTL ring + per-bot placement cooldowns.

Appended at the very tail so no existing offset shifts. The block keeps
three contiguous runs (tests pin the invariant):

1. CLEARED run (``mine_def_key`` .. ``mine_pos``) — zeroed by
   ``load_mine`` on every match change with one rep-stosd.
2. STATIC knobs (``mine_avoid_radius_sq`` / ``mine_spacing_sq`` /
   ``mine_place_chance``) — packed at build time by static_data and NOT
   cleared (they'd be lost; chance stays live-tunable).
3. ``tag_mines`` then the per-call temps — temps are excluded from the
   R-snapshot dump.

The ``mreg_*`` temps belong to the ``detour_5AB9B0`` registration detour,
which runs INSIDE ``mine_tick``'s own ``sub_5AB9B0`` call — it must not
share ``mine_tmp_*`` (those are live across that call)."""

from .model import ScratchField


def extend_mine(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    overlay_fields = c.overlay_fields
    mine_max = c.mine_table_max

    if not mine_max:
        return
    assert (mine_max & (mine_max - 1)) == 0, 'mine ring needs power-of-two size'

    base = max(f.end for f in fields + overlay_fields)
    base = (base + 0xF) & ~0xF
    off = base

    def field(name, size, note):
        nonlocal off
        f = ScratchField(name, off, size, note)
        off += size
        return f

    overlay_fields.extend([
        # --- cleared run (load_mine rep-stosd zeroes mine_def_key..mine_pos)
        field('mine_def_key', 0x04,
              'mine: per-match "Proximity Mine" item-def key (0 = unresolved)'),
        field('mine_sec_key', 0x04,
              'mine: "Secondary" inventory-group key (per match)'),
        field('mine_ring_next', 0x04, 'mine: ring write cursor'),
        field('mine_place_count', 0x04,
              'diag: successful bot placements this match'),
        field('mine_reg_count', 0x04,
              'diag: ring registrations this match (bot + host-human)'),
        field('bot_mine_cd', MAX_BOT_SLOTS * 4,
              'mine: per-bot frames until the next placement attempt'),
        field('mine_ttl', mine_max * 4,
              'mine: per-slot TTL countdown (0 = empty/expired)'),
        field('mine_owner', mine_max * 4,
              'mine: per-slot owner bot slot (-1 = host human/unknown)'),
        field('mine_pos', mine_max * 8, 'mine: per-slot float[2] position'),
        # --- static knobs (build-time packed; NOT cleared per match)
        field('mine_avoid_radius_sq', 0x04,
              'mine: veto bubble d^2 (float, static from cfg)'),
        field('mine_spacing_sq', 0x04,
              'mine: min placement spacing d^2 (float, static from cfg)'),
        field('mine_place_chance', 0x04,
              'mine: placement roll threshold 0..100 (static from cfg)'),
        # --- diag tag, then per-call temps (excluded from the dump)
        field('tag_mines', 0x10, 'diag: mine state dump chunk tag'),
        field('mine_tmp_slot', 0x04, 'mine: mine_tick loop slot spill'),
        field('mine_tmp_char', 0x04, 'mine: mine_tick bot char spill'),
        field('mine_tmp_cnt', 0x04, 'mine: mine_tick pre-fire round count'),
        field('mine_tmp_id', 0x04, 'mine: mine_tick group-iterate item id'),
        field('mine_spill', 0x04, 'mine: mine_tick inventory spill'),
        field('mreg_char', 0x04, 'mine: detour_5AB9B0 char spill'),
        field('mreg_item', 0x04, 'mine: detour_5AB9B0 inv/item spill'),
        field('mine_placing_slot', 0x04,
              'mine: mine_tick->detour_5AB9B0 owner handshake (slot+1; 0 = '
              'no bot placement in flight — the human path). Sits in the '
              'temps region: NOT in the per-match clear (0 is the idle '
              'value anyway) and zero at process start.'),
    ])
