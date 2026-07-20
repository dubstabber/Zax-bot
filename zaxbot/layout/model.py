"""Named layout for the writable scratch area inside .zaxbot."""

from dataclasses import dataclass


# Per-bot state: one parallel u32 array per field, indexed by bot slot
# ([0, MAX_BOT_SLOTS)). All arrays live in a single contiguous block in
# scratch (see ``_bot_state_block`` below) so adding new AI fields is a
# matter of appending an entry here rather than juggling offsets in
# ``build_scratch_layout``.
#
# The exposed scratch field name is ``bot_<key>``; existing ASM that reads
# ``layout.va('bot_team')`` etc. keeps working unchanged.
#
# Order matters insofar as it determines the contiguous offsets one helper
# can derive from another (e.g. ``bot_controllers`` lives at the same
# stride exactly one array length past ``bot_indices``, which lets
# ``walk_controller`` write through a fixed delta after a bot_indices
# scan hit — see ``bot_indices_to_controllers`` in that module).
BOT_STATE_FIELDS = (
    ('participants', 'synthetic DP participant ptrs (per bot slot)'),
    ('indices',      'per-bot mgr+0x290 char-array index'),
    ('chars',        'cached char ptrs (for fire/aim hot path)'),
    ('controllers',  'walking-controller ptrs (re-captured on respawn)'),
    ('team',         'team id (CTF); -1 sentinel = unset'),
)


# Per-bot AI nav/movement state: one parallel u32 array per entry, indexed by
# bot slot ([0, MAX_BOT_SLOTS)). Unlike BOT_STATE_FIELDS these are NOT appended
# to that block (the per-call fire/aim region uses `bot_state_end + N` offsets
# while `host_part` is anchored at absolute 0x2F0); they live in their own
# contiguous region (AI_BASE+) so a single rep-stosd in detour_df90 can clear
# them on match change and a single snapshot chunk can dump them.
#
# Two consumers DERIVE their sizes from AI_PERBOT_FIELD_COUNT rather than a
# hardcoded literal, so appending a field here stays consistent automatically:
#   - detours/df90_match_change.py clears AI_PERBOT_FIELD_COUNT * MAX_BOT_SLOTS
#     dwords on match change.
#   - hook/snapshot.py dumps AI_PERBOT_FIELD_COUNT * MAX_BOT_SLOTS * 4 bytes in
#     its `ai_move` chunk.
#
# INVARIANT (enforced by tests/test_patcher.py): the last three entries MUST
# stay bot_current_wp / bot_prev_wp / bot_wp_try in that order — df90 re-stamps
# the final two index arrays to -1 (a 0 would falsely claim "latched on vertex
# 0" and skip the cold-acquire), and the follower relies on wp_try being last.
#
# Several earlier fields are DORMANT relics of the removed random-wander/
# attractor/flee pipeline, repurposed IN PLACE by bot_movement.py (rather than
# renamed/removed) to keep these offsets stable. The repurposing map:
#   bot_wander_x/y     -> block-vector diagnostic mirror (ai_move idx0/idx1)
#   bot_pickup_x_cache -> bot_last_char (respawn detection)
#   bot_pickup_y_cache -> wp_best_dsq (min dsq-to-node)
#   bot_pickup_valid   -> failed_edge_marker (packed failed edge to avoid)
#   bot_flee_ticks     -> slide_turn (wall-slide deflection ramp)
AI_PERBOT_FIELDS = (
    ('bot_wander_x',        'DORMANT/diag: mirrored controller block.x (float)'),
    ('bot_wander_y',        'DORMANT/diag: mirrored controller block.y (float)'),
    ('bot_wander_ticks',    'DORMANT: was wander target timer'),
    ('bot_last_x',          'stuck: last-tick x (float)'),
    ('bot_last_y',          'stuck: last-tick y (float)'),
    ('bot_stuck_count',     'stuck: frames with delta < STUCK_DELTA_SQ'),
    ('bot_last_item_scan',  'force-tick: bot_ticked 0/1 engine, 2 recovery tick'),
    ('bot_pickup_x_cache',  'follow: bot_last_char (respawn detection)'),
    ('bot_pickup_y_cache',  'follow: wp_best_dsq (min dsq-to-node)'),
    ('bot_pickup_valid',    'follow: packed failed edge marker; avoid retrying blocked edge'),
    ('bot_last_damage',     'follow: reactive cur_damage tracker (pickup-divert hazard avoid)'),
    ('bot_flee_ticks',      'follow: slide_turn (wall-slide deflection ramp)'),
    # --- Waypoint-following per-bot nav state. MUST stay the last three
    # entries (see INVARIANT above). ai_move dump indices 12/13/14.
    # NOTE: bot_route_suspend (per-bot, flag-route block) lives with
    # route_missing_policy/goal, NOT here — growing this block would push the
    # ai_off-relative tail into the overlay region anchored at 0x2080.
    ('bot_current_wp',      'follow: current target vertex idx, -1 = none (idx12)'),
    ('bot_prev_wp',         'follow: previous vertex idx, -1 = not latched (idx13)'),
    ('bot_wp_try',          'follow: frames since last node arrival; escape past WP_TRY (idx14)'),
)
AI_PERBOT_FIELD_COUNT = len(AI_PERBOT_FIELDS)


@dataclass(frozen=True)
class ScratchField:
    name: str
    offset: int
    size: int
    note: str = ''

    @property
    def end(self):
        return self.offset + self.size


def _bot_state_block(base_off, max_bot_slots):
    """Emit a ``ScratchField`` for each BOT_STATE_FIELDS entry at sequential
    offsets starting at ``base_off``. Returns ``(fields, end_off)``."""
    fields = []
    off = base_off
    size = max_bot_slots * 4
    for key, note in BOT_STATE_FIELDS:
        fields.append(ScratchField(f'bot_{key}', off, size, note))
        off += size
    return fields, off


class ScratchLayout:
    def __init__(self, base_va, size, fields):
        self.base_va = base_va
        self.size = size
        self.fields = tuple(fields)
        self._by_name = {field.name: field for field in self.fields}
        if len(self._by_name) != len(self.fields):
            raise ValueError('duplicate scratch field name')
        self.validate()

    def validate(self):
        for field in self.fields:
            if field.offset < 0 or field.size <= 0:
                raise ValueError(f'invalid scratch field {field.name}: {field}')
            if field.end > self.size:
                raise ValueError(
                    f'scratch field {field.name} exceeds scratch area: '
                    f'0x{field.end:x} > 0x{self.size:x}'
                )

        prev = None
        for field in sorted(self.fields, key=lambda f: f.offset):
            if prev and field.offset < prev.end:
                raise ValueError(
                    f'scratch fields overlap: {prev.name} 0x{prev.offset:x}..0x{prev.end:x} '
                    f'and {field.name} 0x{field.offset:x}..0x{field.end:x}'
                )
            prev = field

    def has_field(self, name):
        return name in self._by_name

    def field(self, name):
        return self._by_name[name]

    def off(self, name):
        return self.field(name).offset

    def va(self, name):
        return self.base_va + self.off(name)

    def write(self, section, scratch_off, name, data):
        field = self.field(name)
        if len(data) > field.size:
            raise AssertionError(
                f'{name} does not fit scratch field: {len(data)} bytes > {field.size}'
            )
        start = scratch_off + field.offset
        section[start:start + len(data)] = data

    @property
    def used_size(self):
        return max((field.end for field in self.fields), default=0)

