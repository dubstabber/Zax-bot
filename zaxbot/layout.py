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


def build_scratch_layout(
    base_va,
    scratch_size,
    num_bot_names,
    name_slot_size,
    name_slot_ascii,
    weapon_speeds_max,
    force_bot_ammo_max=0,
    force_bot_ammo_slot_size=0,
    overlay_vertex_max=0,
    overlay_edge_max=0,
    pickup_table_max=0,
    portal_table_max=0,
    portal_static_map_max=0,
    portal_static_point_max=0,
    portal_map_name_slot=0,
    scan_entities_max=0,
    flag_table_max=0,
    flag_static_map_max=0,
    flag_static_point_max=0,
    flag_map_name_slot=0,
    flag_route_max=0,
    flag_entity_slots=2,
    door_table_max=0,
    door_static_map_max=0,
    door_static_point_max=0,
    door_map_name_slot=0,
    door_entity_slots=3,
    door_opener_table_max=0,
    door_opener_static_max=0,
    switch_table_max=0,
    switch_pair_max=0,
    switch_static_map_max=0,
    switch_static_point_max=0,
    switch_static_pair_max=0,
    switch_map_name_slot=0,
):
    BOT_STATE_BASE = 0x180
    MAX_BOT_SLOTS = 16
    bot_state_fields, bot_state_end = _bot_state_block(BOT_STATE_BASE, MAX_BOT_SLOTS)

    fields = [
        ScratchField('hdr', 0x000, 0x08, 'WriteFile header [src_va, len]'),
        ScratchField('dummy', 0x008, 0x04, 'WriteFile bytes-written sink'),
        ScratchField('fn', 0x010, 0x20, 'zax_dump.bin filename'),
        ScratchField('msg', 0x030, 0x20, 'spawn confirmation message'),
        ScratchField('cap_dpmgr', 0x050, 0x04),
        ScratchField('cap_a2', 0x054, 0x04),
        ScratchField('botp', 0x058, 0x04),
        ScratchField('botidx', 0x05C, 0x04),
        ScratchField('logbyte', 0x060, 0x04),
        ScratchField('botchar', 0x064, 0x04),
        ScratchField('botmode', 0x06C, 0x04),
        ScratchField('menu_state', 0x078, 0x04),
        ScratchField('menu_mode', 0x07C, 0x04),
        ScratchField('stepfn', 0x080, 0x10, 'zax_step.log filename'),
        ScratchField('chosen_team', 0x090, 0x04),
        ScratchField('diag_dumped', 0x094, 0x04),
        ScratchField('forced_mode', 0x098, 0x04, '0xFF=auto-detect, 0/1/2=force DM/CTF/SK'),
        ScratchField('prompt_dm', 0x0A0, 0x20),
        ScratchField('prompt_ctf', 0x0C0, 0x20),
        ScratchField('prompt_sk', 0x0E0, 0x20),
        ScratchField('max_for_mode', 0x100, 0x0C),
        ScratchField('prompts_table', 0x110, 0x0C),
        ScratchField('snap_counter', 0x11C, 0x04),
        ScratchField('fire_range_sq', 0x124, 0x04),
        ScratchField('bot_dx', 0x12C, 0x04),
        ScratchField('bot_dy', 0x130, 0x04),
        ScratchField('bot_pos', 0x134, 0x08),
        ScratchField('active_bot_slot', 0x150, 0x04),
        ScratchField('max_players', 0x154, 0x04),
        ScratchField('cur_players', 0x158, 0x04),
        ScratchField('msg_full', 0x160, 0x20),
    ]
    # Contiguous per-bot state block. Each field is `MAX_BOT_SLOTS * 4` bytes
    # and is indexed by slot from ASM as `[<field>_va + slot*4]`. Adding a
    # new per-bot AI field (target cache, path node, etc.) means appending
    # to BOT_STATE_FIELDS — no scratch-offset bookkeeping needed.
    fields.extend(bot_state_fields)
    # Per-call fire/aim working state lives right after the bot-state block;
    # `host_part` keeps its old absolute offset (0x2F0) so we don't churn the
    # one engine-facing pointer that any future tool might want to grep for.
    fields.extend([
        ScratchField('cand_pos', bot_state_end + 0x00, 0x08, 'fire/aim: per-candidate position'),
        ScratchField('cand_tmp', bot_state_end + 0x08, 0x04, 'fire/aim: cand char ptr across helpers'),
        ScratchField('curr_dist_sq', bot_state_end + 0x0C, 0x04, 'fire/aim: current cand d^2'),
        ScratchField('best_target', bot_state_end + 0x10, 0x04, 'fire/aim: winning char ptr'),
        ScratchField('best_dist_sq', bot_state_end + 0x14, 0x04, 'fire/aim: winning d^2'),
        ScratchField('best_dx', bot_state_end + 0x18, 0x04, 'fire/aim: winning dx for angle'),
        ScratchField('best_dy', bot_state_end + 0x1C, 0x04, 'fire/aim: winning dy for angle'),
        ScratchField('bot_slot_tmp', bot_state_end + 0x20, 0x04, 'fire/aim: firing bot slot index'),
        ScratchField('cand_idx', bot_state_end + 0x24, 0x04, 'fire/aim: outer loop counter'),
        ScratchField('our_team_tmp', bot_state_end + 0x28, 0x04, 'fire/aim: bot team id, -1 = no CTF filter'),
        ScratchField('bot_char_tmp', bot_state_end + 0x2C, 0x04, 'fire/aim: firing bot char ptr'),
        ScratchField('host_part', 0x2F0, 0x04, 'fire/aim: cached host participant ptr (team read live from +0x14)'),
        # Leading-shot fields live in the 12-byte gap between host_part and
        # bot_colors. Kept out of the bot_state_end + N block so host_part
        # stays at its grep-stable 0x2F0 absolute offset.
        ScratchField('best_vx',    0x2F4, 0x04, 'fire/aim: winning target velocity x (for lead)'),
        ScratchField('best_vy',    0x2F8, 0x04, 'fire/aim: winning target velocity y (for lead)'),
        ScratchField('proj_speed', 0x2FC, 0x04, 'fire/aim: projectile speed (from cfg.PROJECTILE_SPEED)'),
        # Per-fire-call scratch for apply_lead's quadratic intercept solver.
        # Holds the coefficients of a*t² + b*t + c = 0 plus the discriminant
        # so the asm can branch on disc<0 / a>=0 via integer sign-bit tests
        # instead of FPU compares (much cleaner control flow). quad_c holds
        # the *muzzle-adjusted* squared distance (|d|² - muzzle²) so both the
        # discriminant and the citardauq numerator use the corrected value.
        ScratchField('quad_a',    0x3D0, 0x04, 'apply_lead: |v|² - proj_speed² (quad solver)'),
        ScratchField('quad_b',    0x3D4, 0x04, 'apply_lead: 2*(d·v) - 2*muzzle*p (quad solver, muzzle-adjusted)'),
        ScratchField('quad_disc', 0x3D8, 0x04, 'apply_lead: b² - 4ac (quad solver discriminant)'),
        ScratchField('quad_c',    0x3E4, 0x04, 'apply_lead: |d|² - muzzle² (muzzle-adjusted c)'),
        # Build-time constants for muzzle-offset compensation. muzzle_sq is
        # MUZZLE_OFFSET² packed once so the per-fire asm doesn't have to
        # re-square at runtime.
        ScratchField('muzzle_offset', 0x3E8, 0x04, 'apply_lead: muzzle spawn distance from bot center (px)'),
        ScratchField('muzzle_sq',     0x3EC, 0x04, 'apply_lead: muzzle_offset² (packed at build time)'),
        # Threshold for the per-shot lead-randomization roll. bot_fire_aim
        # calls sub_55C4E0(RNG, 0, 99) and skips apply_lead when the roll
        # >= this threshold. Encoded as int(cfg.LEAD_PROBABILITY * 100), so
        # 0 = never lead, 100 = always lead, 50 = coin-flip.
        ScratchField('lead_threshold', 0x3F0, 0x04, 'bot_fire_aim: lead-randomization threshold (0..100)'),
    ])
    fields.extend([
        # Per-name color tables. bot_colors holds (color1, color2) dword pairs
        # parallel to BOT_NAMES; picked_name_idx preserves the RNG-picked
        # name index so the color lookup at spawn time uses the same row
        # the on-screen name was picked from.
        ScratchField('bot_colors', 0x300, num_bot_names * 8, 'per-name (u32 c1, u32 c2)'),
        ScratchField('picked_name_idx', 0x3A0, 0x04, 'idx into bot_names / bot_colors'),
        # Per-match "this name is taken" bitmap, parallel to BOT_NAMES.
        # Byte == 0 means the slot is free, != 0 means claimed by a live bot.
        # Cleared by detour_df90 when cap_a2 flips (new match).
        ScratchField('used_names', 0x3A8, num_bot_names, 'per-name claimed flag (byte)'),
        ScratchField('thdr', 0x700, 0x04),
        ScratchField('thdr_tag', 0x704, 0x10),
        ScratchField('thdr_src_va', 0x714, 0x04),
        ScratchField('thdr_len', 0x718, 0x04),
        ScratchField('saved_src_va', 0x71C, 0x04),
        ScratchField('snap_idx', 0x720, 0x04),
        ScratchField('snap_count', 0x724, 0x04),
        ScratchField('snap_arr', 0x728, 0x04),
        ScratchField('tag_snap_marker', 0x730, 0x10),
        ScratchField('tag_part', 0x740, 0x10),
        ScratchField('tag_char', 0x750, 0x10),
        ScratchField('tag_worldmgr', 0x760, 0x10),
        ScratchField('tag_dpmgr', 0x770, 0x10),
        ScratchField('tag_idx_nbhd', 0x780, 0x10),
        ScratchField('tag_stats', 0x790, 0x10),
        ScratchField('tag_cstr', 0x7A0, 0x10),
        ScratchField('tag_mgr_root', 0x7B0, 0x10),
        ScratchField('tag_session', 0x7C0, 0x10),
        ScratchField('stats_tmp', 0x7D0, 0x04),
        ScratchField('cstr_tmp', 0x7D4, 0x04),
        ScratchField('tag_charptr', 0x7D8, 0x10),
        ScratchField('tmp_idx', 0x7FC, 0x04),
        ScratchField('cap_dp_edi', 0x800, 0x04),
        ScratchField('my_queue_slot', 0x804, 0x04),
        ScratchField('synthetic_player_id', 0x808, 0x04),
        ScratchField('phase_b_in_flight', 0x80C, 0x04),
        # Diagnostic tags for the bot-AI scratch dump. ai_fire covers the
        # per-call fire/aim region (best_target through proj_speed); ai_pos
        # covers prev_pos_table + cand_vx/cand_vy; weapon_info dumps the
        # current weapon's vtable + projectile prototype + applied speed.
        ScratchField('tag_ai_fire',     0x810, 0x10),
        ScratchField('tag_ai_pos',      0x820, 0x10),
        ScratchField('tag_weapon_info', 0x830, 0x10),
        ScratchField('tag_host_weapon', 0x840, 0x10),
        ScratchField('tag_pc2_weapon',  0x850, 0x10),
        ScratchField('tag_host_wpn_bytes', 0x860, 0x10),
        ScratchField('tag_pc2_wpn_bytes',  0x870, 0x10),
        ScratchField('tag_ai_move',        0x880, 0x10, 'diag: bot wander/stuck/attractor state'),
        ScratchField('tag_hazard',         0x890, 0x10, 'diag: cached hazard table'),
        ScratchField('tag_wp_diag',        0x8A0, 0x10, 'diag: waypoint probe summary (8 u32)'),
        ScratchField('tag_wp_lv',          0x8B0, 0x10, 'diag: raw bytes from vtbl[0x184] result'),
        ScratchField('tag_wp_lay',         0x8C0, 0x10, 'diag: raw bytes from active CLayer'),
        ScratchField('tag_wp_map',         0x8D0, 0x10, 'diag: raw bytes from CWayPointMap'),
        ScratchField('tag_plasma_diag',    0x8E0, 0x10, 'diag: plasma-map pin buffer (scan_plasma output)'),
        ScratchField('tag_pheat',          0x8F0, 0x10, 'diag: full per-tile heat-grid map'),
        ScratchField('bot_names', 0x900, num_bot_names * name_slot_size),
        ScratchField('bot_names_ascii', 0xB80, num_bot_names * name_slot_ascii),
        # Per-bot, per-char-slot last-seen position cache for the lead-shot
        # velocity estimate. The engine does not expose a live velocity field
        # on player characters (the CEntityMovable +0xE8/+0xEC fields stay 0),
        # so we fingerprint velocity as (curr_pos - prev_pos) across two
        # consecutive pick_target calls for THIS BOT. Keying on the bot slot
        # (not just cand_idx) is required because multiple bots fire in the
        # same frame: a shared table would let bot N+1 see prev_pos already
        # overwritten by bot N's call this frame, collapsing the delta to 0.
        # Indexed as table[bot_slot * 16 + cand_idx]; 16 × 16 entries × 8B.
        # Zero-initialised; first-visit delta is suppressed by the asm.
        ScratchField('prev_pos_table', 0xCC0, 16 * 16 * 8, 'fire/aim: per-(bot,cand) last-seen pos (16×16×2 floats)'),
        ScratchField('cand_vx',        0x14C0, 0x04, 'fire/aim: current cand velocity x (delta vs prev)'),
        ScratchField('cand_vy',        0x14C4, 0x04, 'fire/aim: current cand velocity y (delta vs prev)'),
        # Per-weapon projectile-speed dispatch (see hook/weapon_speed.py).
        ScratchField('default_proj_speed', 0x14C8, 0x04, 'weapon: cfg.PROJECTILE_SPEED fallback (static)'),
        ScratchField('is_hitscan',         0x14CC, 0x04, 'weapon: 1 if current weapon has no projectile prototype'),
        ScratchField('primary_hash',       0x14D0, 0x04, 'weapon: cached hash of "Primary" slot (0 = uninit)'),
        ScratchField('inv_tmp',            0x14D4, 0x04, 'weapon: inventory ptr scratch across sub_523DF0 call'),
        ScratchField('current_weapon_obj', 0x14D8, 0x04, 'weapon: diagnostic — last weapon object ptr'),
        ScratchField('current_proto_va',   0x14DC, 0x04, 'weapon: diagnostic — last inventory item-definition ptr'),
        # Diagnostic fields read by compute_proj_speed's def-field fallback;
        # placed contiguously with current_weapon_obj/current_proto_va so a
        # single widened weapon_info snapshot chunk captures all four runtime
        # values plus the build-time speed_scale constant in one shot.
        ScratchField('current_proto_model_va', 0x14E0, 0x04, 'weapon: diagnostic — [def+0x20] projectile CModel*, 0 if hitscan'),
        ScratchField('proto_speed_raw',        0x14E4, 0x04, 'weapon: diagnostic — [proto+0x60] raw pixels/sec from def'),
        ScratchField('speed_scale',            0x14E8, 0x04, 'weapon: cfg.SPEED_SCALE — multiplier from pixels/sec to per-call units'),
        ScratchField('force_item_def_idx', 0x14EC, 0x04, 'spawn: temp resolved inventory item-definition index'),
        # weapon_table: (item_def_va u32, speed float) pairs + terminating 0 entry.
        # Sized to fit WEAPON_SPEEDS_MAX rows plus the sentinel.
        ScratchField('weapon_table',       0x14F0, (weapon_speeds_max + 1) * 8,
                     'weapon: (item_def_va, speed) lookup + 0-VA sentinel'),
        # Host-side diagnostic — snapshot writes these by running the weapon
        # lookup chain on worldmgr.charArray[0]. Lets the user discover valid
        # item ids by picking up a weapon and pressing R.
        ScratchField('host_weapon_obj',    0x15FC, 0x04, 'host weapon diag: weapon object ptr'),
        ScratchField('host_proto_va',      0x1600, 0x04, 'host weapon diag: inventory item-definition ptr'),
        ScratchField('host_item_id',       0x1604, 0x04, 'host weapon diag: Primary slot item id'),
        # Parallel diagnostic for PC2 (charArray[1]) — lets us compare a real
        # remote client's weapon layout against the synthetic-DP bot's.
        ScratchField('pc2_weapon_obj',     0x1608, 0x04, 'pc2 weapon diag: weapon object ptr'),
        ScratchField('pc2_proto_va',       0x160C, 0x04, 'pc2 weapon diag: inventory item-definition ptr'),
        ScratchField('pc2_item_id',        0x1610, 0x04, 'pc2 weapon diag: Primary slot item id'),
        ScratchField('force_bot_item_name', 0x1614, 0x40, 'spawn: ASCII inventory item name to force-equip; NUL disables'),
    ])
    # Battery + ammo top-up list applied to the bot when
    # force_bot_item_name is set. force_bot_ammo_count holds the live length;
    # force_bot_ammo_names is a flat array of (slot_size)-byte ASCII slots.
    # Both fields are optional — callers that don't need them pass max=0 and
    # the layout omits them entirely.
    if force_bot_ammo_max > 0 and force_bot_ammo_slot_size > 0:
        fields.extend([
            ScratchField('force_bot_ammo_count', 0x1654, 0x04, 'spawn: live count of force_bot_ammo_names entries'),
            ScratchField(
                'force_bot_ammo_names',
                0x1658,
                force_bot_ammo_max * force_bot_ammo_slot_size,
                'spawn: ASCII ammo-item names handed to the bot when force-equip is on',
            ),
        ])
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
    # --- Waypoint overlay state -------------------------------------------
    # Renderable waypoint set baked from cfg.OVERLAY_WAYPOINTS / EDGES at
    # build time. Capacity ceilings are passed in so detours/overlay.py can
    # iterate by the LIVE count fields without growing the section per
    # waypoint set. Anchored at 0x2000 to keep a clear visual gap from the
    # bot-AI scratch (ends near 0x1F4C) and survive future field churn.
    OVERLAY_BASE = 0x2080
    overlay_color_size = 16          # CColor struct (BGRA + palette idx + flags)
    overlay_vertex_stride = 8        # float[2] per vertex
    overlay_edge_stride   = 4        # u16[2] per edge
    overlay_vertex_max_capped = max(0, overlay_vertex_max)
    overlay_edge_max_capped   = max(0, overlay_edge_max)

    overlay_fields = [
        ScratchField('overlay_enabled',       OVERLAY_BASE + 0x00, 0x04,
                     'overlay: master enable flag (0 = skip detour body)'),
        ScratchField('overlay_vertex_color',  OVERLAY_BASE + 0x04, overlay_color_size,
                     'overlay: vertex CColor; rebuilt each frame by sub_53F010'),
        ScratchField('overlay_edge_color',    OVERLAY_BASE + 0x14, overlay_color_size,
                     'overlay: edge CColor; rebuilt each frame by sub_53F010'),
        ScratchField('overlay_vertex_radius', OVERLAY_BASE + 0x24, 0x04,
                     'overlay: oval radius (float, world-space pixels)'),
        ScratchField('overlay_vertex_aspect', OVERLAY_BASE + 0x28, 0x04,
                     'overlay: oval y/x aspect (float; 1.0 = circle)'),
        ScratchField('overlay_vertex_count',  OVERLAY_BASE + 0x2C, 0x04,
                     'overlay: live count <= overlay_vertex_max'),
        ScratchField('overlay_edge_count',    OVERLAY_BASE + 0x30, 0x04,
                     'overlay: live count <= overlay_edge_max'),
        ScratchField('overlay_renderer_tmp',  OVERLAY_BASE + 0x34, 0x04,
                     'overlay: cached renderer ptr for inner-loop reuse'),
        # Per-frame screen-edge camera read from the host's tracker layer
        # (`layer+0xC0/0xC4` floats), used to pre-transform world coords
        # to screen coords before passing to the engine. ``overlay_cam_ok``
        # is a sentinel: 0 means lookup failed and we should skip drawing.
        ScratchField('overlay_cam_x',         OVERLAY_BASE + 0x38, 0x04,
                     'overlay: screen-edge cam x (float, world coord of screen left)'),
        ScratchField('overlay_cam_y',         OVERLAY_BASE + 0x3C, 0x04,
                     'overlay: screen-edge cam y (float, world coord of screen top)'),
        ScratchField('overlay_cam_ok',        OVERLAY_BASE + 0x40, 0x04,
                     'overlay: 1 if cam_x/y are valid, 0 = skip draw'),
        ScratchField('overlay_tmp_p1',        OVERLAY_BASE + 0x44, 0x08,
                     'overlay: float[2] screen p1 for line/oval draw'),
        ScratchField('overlay_tmp_p2',        OVERLAY_BASE + 0x4C, 0x08,
                     'overlay: float[2] screen p2 for line draw'),
        # Waypoint-editor state. wp_selected_idx is the "cursor": index into
        # overlay_vertices of the currently-selected node, or 0xFFFFFFFF for
        # no selection. Auto-set to the new node by wp_drop and to the nearest
        # node by wp_select; consumed by wp_drop (auto-edge source) and by the
        # overlay draw pass (highlight render). wp_scratch is 8 bytes of
        # per-call scratch for the position read used by wp_drop/select/delete.
        ScratchField('wp_selected_idx',       OVERLAY_BASE + 0x54, 0x04,
                     'waypoint edit: selected node index (0xFFFFFFFF = none)'),
        ScratchField('wp_scratch',            OVERLAY_BASE + 0x58, 0x08,
                     'waypoint edit: float[2] for sub_4FB0A0 host-pos reads'),
        ScratchField('overlay_selected_color', OVERLAY_BASE + 0x60, overlay_color_size,
                     'overlay: selected-vertex CColor (rebuilt per-frame)'),
        ScratchField('wp_snap_radius_sq',     OVERLAY_BASE + 0x70, 0x04,
                     'waypoint edit: snap radius² (float, world units)'),
    ]
    # Vertex / edge tables start at +0x80 (after the per-frame scratch
    # above, including waypoint-editor state) so growing the fix-up state
    # doesn't shift the tables.
    OVERLAY_TABLE_OFF = 0x80
    if overlay_vertex_max_capped > 0:
        overlay_fields.append(ScratchField(
            'overlay_vertices', OVERLAY_BASE + OVERLAY_TABLE_OFF,
            overlay_vertex_max_capped * overlay_vertex_stride,
            'overlay: float[2] per vertex (world coords)',
        ))
    if overlay_edge_max_capped > 0:
        overlay_edge_off = OVERLAY_BASE + OVERLAY_TABLE_OFF + overlay_vertex_max_capped * overlay_vertex_stride
        overlay_fields.append(ScratchField(
            'overlay_edges', overlay_edge_off,
            overlay_edge_max_capped * overlay_edge_stride,
            'overlay: (u16 i, u16 j) per edge; indices into overlay_vertices',
        ))

    # --- Waypoint save/load state (lives after the vertex/edge tables) ----
    # Filename buffer holds the dynamically-built "waypoints/<map>.zwpt"
    # path (resolved per save/load from MAP_NAME_CSTRING_VA). The static
    # prefix / suffix / dir-name strings are initialised from cfg and copied
    # by the asm into the buffer. wp_file_header is the 16-byte staging
    # area for the file's magic+version+counts; wp_io_count is the
    # lpNumberOfBytesTransferred receiver for ReadFile/WriteFile calls.
    wp_io_off = OVERLAY_BASE + OVERLAY_TABLE_OFF
    if overlay_vertex_max_capped > 0:
        wp_io_off += overlay_vertex_max_capped * overlay_vertex_stride
    if overlay_edge_max_capped > 0:
        wp_io_off += overlay_edge_max_capped * overlay_edge_stride
    overlay_fields.extend([
        ScratchField('wp_filename_buf', wp_io_off + 0x00, 0x100,
                     'waypoint io: dynamically-built file path'),
        ScratchField('wp_file_header',  wp_io_off + 0x100, 0x10,
                     'waypoint io: 16B header staging (magic+version+counts)'),
        ScratchField('wp_io_count',     wp_io_off + 0x110, 0x04,
                     'waypoint io: lpNumberOfBytesTransferred for Read/WriteFile'),
        ScratchField('wp_dir_static',   wp_io_off + 0x120, 0x20,
                     'waypoint io: static "waypoints" dir name for CreateDirectoryA'),
        ScratchField('wp_prefix_static', wp_io_off + 0x140, 0x20,
                     'waypoint io: static "waypoints/" path prefix'),
        ScratchField('wp_suffix_static', wp_io_off + 0x160, 0x10,
                     'waypoint io: static ".zwpt" path suffix'),
        ScratchField('wp_msg_saved',    wp_io_off + 0x170, 0x20,
                     'waypoint io: on-screen msg shown after save'),
        ScratchField('wp_msg_loaded',   wp_io_off + 0x190, 0x20,
                     'waypoint io: on-screen msg shown after auto-load'),
        ScratchField('wp_msg_nomap',    wp_io_off + 0x1B0, 0x20,
                     'waypoint io: on-screen msg shown when map name is empty'),
        ScratchField('wp_msg_failed',   wp_io_off + 0x1D0, 0x20,
                     'waypoint io: on-screen msg shown on save/load failure'),
    ])
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
    # --- Teleport/portal overlay data -------------------------------------
    # The live portal_table is populated once per match by load_portals from
    # the compact build-time static table (parsed from Data.dat). Kept after
    # the existing overlay/lava tail so all older scratch offsets remain
    # stable. Map entries are fixed-size:
    #   name[PORTAL_MAP_NAME_SLOT] | count u32 | first_point_index u32
    portal_table_max_capped = max(0, portal_table_max)
    portal_static_map_max_capped = max(0, portal_static_map_max)
    portal_static_point_max_capped = max(0, portal_static_point_max)
    portal_name_slot_capped = max(0, portal_map_name_slot)
    portal_map_stride = portal_name_slot_capped + 8
    portal_base = plasma_tmp_base + 0x860
    overlay_fields.extend([
        ScratchField('portal_count', portal_base + 0x00, 0x04,
                     'portal: live entries in portal_table for active map'),
        ScratchField('overlay_portal_color', portal_base + 0x04, overlay_color_size,
                     'overlay: detected-portal CColor (rebuilt per-frame)'),
    ])
    portal_static_base = portal_base + 0x14
    if portal_table_max_capped > 0:
        overlay_fields.append(ScratchField(
            'portal_table', portal_static_base,
            portal_table_max_capped * 8,
            'portal: float[2] per detected teleport destination (world coords)',
        ))
        portal_static_base += portal_table_max_capped * 8
    overlay_fields.extend([
        ScratchField('portal_static_map_count', portal_static_base + 0x00, 0x04,
                     'portal: build-time static map table count'),
        ScratchField('portal_static_point_count', portal_static_base + 0x04, 0x04,
                     'portal: build-time static point table count'),
    ])
    portal_static_base += 0x08
    if portal_static_map_max_capped > 0 and portal_map_stride > 8:
        overlay_fields.append(ScratchField(
            'portal_static_maps', portal_static_base,
            portal_static_map_max_capped * portal_map_stride,
            'portal: static map records (name/count/first point)',
        ))
        portal_static_base += portal_static_map_max_capped * portal_map_stride
    if portal_static_point_max_capped > 0:
        overlay_fields.append(ScratchField(
            'portal_static_points', portal_static_base,
            portal_static_point_max_capped * 8,
            'portal: static float[2] point table parsed from Data.dat',
        ))
        portal_static_base += portal_static_point_max_capped * 8
    # Per-call scratch (float[2]) for the teleport-portal detour's sub_4FB0A0
    # source-position read (detours/portal_register.py). Appended at the very
    # tail of the portal block so no existing portal/overlay offset shifts.
    if portal_table_max_capped > 0:
        overlay_fields.append(ScratchField(
            'portal_reg_tmp', portal_static_base, 0x08,
            'portal: float[2] scratch for sub_4FB0A0 teleport source-pos read',
        ))
        portal_static_base += 0x08

    # --- World entity scanner (detours/entity_scan.py) --------------------
    # Loop state + result table for scan_entities (the general spatial-grid
    # walk). All per-call scratch; lives at the very tail so nothing else
    # shifts. scan_table is `scan_entities_max` records of 16 bytes each:
    #   (entity_ptr u32, x f32, y f32, flags u32).
    tail_off = portal_static_base
    if scan_entities_max > 0:
        scan_base = portal_static_base
        tail_off = scan_base + 0x30 + scan_entities_max * 16
        overlay_fields.extend([
            ScratchField('scan_class_desc', scan_base + 0x00, 0x04,
                         'scan: class descriptor to match (0 = collect every entity)'),
            ScratchField('scan_count',      scan_base + 0x04, 0x04,
                         'scan: live entries written to scan_table'),
            ScratchField('scan_visit_id',   scan_base + 0x08, 0x04,
                         'scan: this-scan visit id (mirrors engine dword_622200 dedup)'),
            ScratchField('scan_ncells',     scan_base + 0x0C, 0x04,
                         'scan: rows*cols cell count (capped)'),
            ScratchField('scan_cells',      scan_base + 0x10, 0x04,
                         'scan: grid cells array base'),
            ScratchField('scan_cellidx',    scan_base + 0x14, 0x04,
                         'scan: outer cell-loop index'),
            ScratchField('scan_list',       scan_base + 0x18, 0x04,
                         'scan: current cell entity-pointer array'),
            ScratchField('scan_cnt',        scan_base + 0x1C, 0x04,
                         'scan: current cell entity count (capped)'),
            ScratchField('scan_k',          scan_base + 0x20, 0x04,
                         'scan: inner entity-loop index'),
            ScratchField('scan_cur_ent',    scan_base + 0x24, 0x04,
                         'scan: current entity ptr (survives helper calls)'),
            ScratchField('scan_tmp_pos',    scan_base + 0x28, 0x08,
                         'scan: float[2] for sub_4FB0A0 entity-pos reads'),
            ScratchField('scan_table',      scan_base + 0x30, scan_entities_max * 16,
                         'scan: (ptr, x, y, flags) records collected by scan_entities'),
        ])
        # Per-portal active-state (scan_portal_active). portal_active is the
        # output (1 = nearest pad entity is Active); portal_best_dist is the
        # per-portal nearest-distance tracker (per-call temp); portal_scan_count
        # is the page-flip re-scan countdown. Sized to PORTAL_TABLE_MAX.
        if portal_table_max_capped > 0:
            pa_base = scan_base + 0x30 + scan_entities_max * 16
            overlay_fields.extend([
                ScratchField('portal_active',     pa_base + 0x00, portal_table_max_capped * 4,
                             'portal: per-pad active flag (1 = nearest entity has the Active bit)'),
                ScratchField('portal_best_dist',  pa_base + portal_table_max_capped * 4,
                             portal_table_max_capped * 4,
                             'portal: per-pad nearest-entity d^2 tracker (scan_portal_active temp)'),
                ScratchField('portal_scan_count', pa_base + portal_table_max_capped * 8, 0x04,
                             'portal: page-flip re-scan countdown for scan_portal_active'),
                ScratchField('scan_d2', pa_base + portal_table_max_capped * 8 + 0x04, 0x04,
                             'portal: float d^2 temp for the nearest-pad compare'),
                ScratchField('portal_entity', pa_base + portal_table_max_capped * 8 + 0x08,
                             portal_table_max_capped * 4,
                             'portal: the matched (nearest) entity ptr per pad (diag / direct read)'),
            ])
            tail_off = pa_base + portal_table_max_capped * 12 + 0x08

    # --- CTF flag overlay data --------------------------------------------
    # Mirrors the portal static-table block. The live flag_table is populated
    # once per match by load_flags from the compact build-time static table
    # (parsed from Data.dat). Placed at the very tail so no existing scratch
    # offset shifts. Map entries are fixed-size:
    #   name[FLAG_MAP_NAME_SLOT] | count u32 | first_point_index u32
    flag_table_max_capped = max(0, flag_table_max)
    flag_static_map_max_capped = max(0, flag_static_map_max)
    flag_static_point_max_capped = max(0, flag_static_point_max)
    flag_name_slot_capped = max(0, flag_map_name_slot)
    flag_map_stride = flag_name_slot_capped + 8
    flag_base = tail_off
    overlay_fields.extend([
        ScratchField('flag_count', flag_base + 0x00, 0x04,
                     'flag: live entries in flag_table for active map'),
        ScratchField('overlay_flag_color', flag_base + 0x04, overlay_color_size,
                     'overlay: detected-flag CColor (rebuilt per-frame)'),
        ScratchField('flag_entity_match_radius_sq', flag_base + 0x14, 0x04,
                     'flag: max d^2 for matching live entities to a flag anchor'),
        ScratchField('flag_home_tick_radius_sq', flag_base + 0x18, 0x04,
                     'flag: max d^2 for force-ticking home flag entities'),
        ScratchField('flag_evt_present', flag_base + 0x1C, 0x04,
                     'flag: value (0/1) the activate/deactivate event detours write to flag_present'),
    ])
    flag_static_base = flag_base + 0x20
    if flag_table_max_capped > 0:
        overlay_fields.append(ScratchField(
            'flag_table', flag_static_base,
            flag_table_max_capped * 8,
            'flag: float[2] per CTF flag home base (world coords)',
        ))
        flag_static_base += flag_table_max_capped * 8
        overlay_fields.append(ScratchField(
            'flag_team', flag_static_base,
            flag_table_max_capped * 4,
            'flag: team tag per live flag_table entry (0=Blue, 1=Red)',
        ))
        flag_static_base += flag_table_max_capped * 4
        flag_entity_slots_capped = max(1, flag_entity_slots)
        overlay_fields.append(ScratchField(
            'flag_entity', flag_static_base,
            flag_table_max_capped * flag_entity_slots_capped * 4,
            'flag: live entity ptrs matched exactly at each flag anchor (checker/marker/flag)',
        ))
        flag_static_base += flag_table_max_capped * flag_entity_slots_capped * 4
        overlay_fields.append(ScratchField(
            'flag_present', flag_static_base,
            flag_table_max_capped * 4,
            'flag: 1 iff the expected exact-anchor flag/base entity pair is matched',
        ))
        flag_static_base += flag_table_max_capped * 4
    overlay_fields.extend([
        ScratchField('flag_static_map_count', flag_static_base + 0x00, 0x04,
                     'flag: build-time static map table count'),
        ScratchField('flag_static_point_count', flag_static_base + 0x04, 0x04,
                     'flag: build-time static point table count'),
    ])
    flag_static_base += 0x08
    if flag_static_map_max_capped > 0 and flag_map_stride > 8:
        overlay_fields.append(ScratchField(
            'flag_static_maps', flag_static_base,
            flag_static_map_max_capped * flag_map_stride,
            'flag: static map records (name/count/first point)',
        ))
        flag_static_base += flag_static_map_max_capped * flag_map_stride
    if flag_static_point_max_capped > 0:
        overlay_fields.append(ScratchField(
            'flag_static_points', flag_static_base,
            flag_static_point_max_capped * 8,
            'flag: static float[2] point table parsed from Data.dat',
        ))
        flag_static_base += flag_static_point_max_capped * 8
        overlay_fields.append(ScratchField(
            'flag_static_team', flag_static_base,
            flag_static_point_max_capped * 4,
            'flag: static team tag (DWORD per point, parallel to flag_static_points)',
        ))
        flag_static_base += flag_static_point_max_capped * 4

    # --- CTF flag routing (BFS path field over the waypoint graph) --------
    # Precomputed once per match by build_flag_routes (from detour_df90) and
    # consumed by ctf_next_hop at each bot node arrival. flag_dist[i][node] =
    # hop distance from flag base i's nearest node to `node` (0xFFFFFFFF =
    # unreachable / no graph). BFS/routing fields are global; bfs_* are
    # transient load-time scratch for the BFS itself.
    flag_route_max_capped = max(0, flag_route_max)
    overlay_vertex_max_capped = max(0, overlay_vertex_max)
    if flag_route_max_capped > 0 and overlay_vertex_max_capped > 0:
        route_base = flag_static_base
        overlay_fields.extend([
            ScratchField('flag_routing_active', route_base + 0x00, 0x04,
                         'flag-route: 1 iff CTF + graph + flags + routes built this match'),
            ScratchField('route_cur', route_base + 0x04, 0x04,
                         'flag-route: ctf_next_hop spill of current node idx'),
            ScratchField('bfs_head', route_base + 0x08, 0x04,
                         'flag-route: BFS queue head (load-time)'),
            ScratchField('bfs_tail', route_base + 0x0C, 0x04,
                         'flag-route: BFS queue tail (load-time)'),
            ScratchField('bfs_u', route_base + 0x10, 0x04,
                         'flag-route: BFS current node u (load-time)'),
            ScratchField('bfs_du', route_base + 0x14, 0x04,
                         'flag-route: BFS dist of u (load-time)'),
            ScratchField('bfs_disti', route_base + 0x18, 0x04,
                         'flag-route: BFS dist-array base for the base being built'),
            ScratchField('bfr_i', route_base + 0x1C, 0x04,
                         'flag-route: build_flag_routes outer base-loop index (survives wp_find_nearest+BFS)'),
            ScratchField('route_carry', route_base + 0x20, 0x04,
                         'flag-route: ctf_next_hop carry flag spill (survives sub_4267E0/sub_425290)'),
            ScratchField('route_goal_flag', route_base + 0x24, 0x04,
                         'flag-route: ctf_pick_goal output = goal flag index (home if carrying, else enemy; -1 = none)'),
            ScratchField('route_missing_policy', route_base + 0x28, MAX_BOT_SLOTS * 4,
                         'flag-route: per-bot missing-flag policy (0 unset, 1 search, 2 wait/base-route)'),
            ScratchField('route_missing_goal', route_base + 0x28 + MAX_BOT_SLOTS * 4, MAX_BOT_SLOTS * 4,
                         'flag-route: per-bot goal index that route_missing_policy applies to'),
            ScratchField('bot_route_suspend', route_base + 0x28 + MAX_BOT_SLOTS * 8, MAX_BOT_SLOTS * 4,
                         'flag-route: per-bot frames of routing suspension (roam after a routed wedge)'),
            ScratchField('route_block_hits', route_base + 0x28 + MAX_BOT_SLOTS * 12, MAX_BOT_SLOTS * 4,
                         'flag-route: per-bot count of routed hops forced off the marked failed edge'),
        ])
        route_tail = route_base + 0x28 + MAX_BOT_SLOTS * 16
        overlay_fields.extend([
            ScratchField('route_epoch', route_tail + 0x00, 0x04,
                         'flag-route: bumped when the open-door BFS field is rebuilt; a routed bot whose stored epoch differs re-acquires so ctf_next_hop re-runs against the new field'),
            ScratchField('bot_route_epoch', route_tail + 0x04, MAX_BOT_SLOTS * 4,
                         'flag-route: per-bot last route_epoch re-evaluated under (mid-life door-change reroute trigger)'),
        ])
        route_tail += 0x04 + MAX_BOT_SLOTS * 4
        overlay_fields.extend([
            ScratchField('ctf_score_block', route_tail + 0x00, 0x04,
                         'ctf-score: 1 suppresses CGiveTeamAPointAction award'),
            ScratchField('ctf_score_team', route_tail + 0x04, 0x04,
                         'ctf-score: score recipient team from CGiveTeamAPointAction+8'),
            ScratchField('ctf_score_target_def', route_tail + 0x08, 0x04,
                         'ctf-score: own Red/Blue flag item-definition id for score recipient'),
            ScratchField('ctf_score_gid', route_tail + 0x0C, 0x04,
                         'ctf-score: Multiplayer Flag inventory group id'),
            ScratchField('ctf_score_inv', route_tail + 0x10, 0x04,
                         'ctf-score: inventory ptr across inventory helper calls'),
        ])
        route_tail += 0x14
        overlay_fields.append(ScratchField(
            'flag_route_node', route_tail, flag_route_max_capped * 4,
            'flag-route: nearest graph node to each routed flag base (goal node)',
        ))
        route_tail += flag_route_max_capped * 4
        overlay_fields.append(ScratchField(
            'flag_dist', route_tail,
            flag_route_max_capped * overlay_vertex_max_capped * 4,
            'flag-route: per-base BFS hop-distance field (FLAG_ROUTE_MAX x vertex_max dwords)',
        ))
        route_tail += flag_route_max_capped * overlay_vertex_max_capped * 4
        overlay_fields.append(ScratchField(
            'bfs_queue', route_tail, overlay_vertex_max_capped * 4,
            'flag-route: BFS FIFO of node indices (load-time transient)',
        ))
        route_tail += overlay_vertex_max_capped * 4
        flag_static_base = route_tail

    # --- Door detection tables ---------------------------------------------
    # Mirrors the portal/flag static-table blocks. The live door_table is
    # populated once per match by load_doors from the compact build-time
    # static table (parsed from Data.dat); door_blocked[] is refreshed by the
    # periodic grid walk (scan_portal_active) reading each anchored entity's
    # SOLID flag. Placed at the very tail so no existing scratch offset shifts.
    door_table_max_capped = max(0, door_table_max)
    door_static_map_max_capped = max(0, door_static_map_max)
    door_static_point_max_capped = max(0, door_static_point_max)
    door_name_slot_capped = max(0, door_map_name_slot)
    door_opener_table_max_capped = max(0, door_opener_table_max)
    door_opener_static_max_capped = max(0, door_opener_static_max)
    # Door map records carry TWO (count, first) pairs: points and openers.
    door_map_stride = door_name_slot_capped + 16
    if door_table_max_capped > 0:
        door_base = flag_static_base
        overlay_fields.extend([
            ScratchField('door_count', door_base + 0x00, 0x04,
                         'door: live entries in door_table for active map'),
            ScratchField('overlay_door_color', door_base + 0x04, overlay_color_size,
                         'overlay: detected-door CColor (rebuilt per-frame)'),
            ScratchField('door_match_radius_sq', door_base + 0x14, 0x04,
                         'door: max d^2 for matching a grid entity to a door anchor'),
            ScratchField('door_wedge_radius_sq', door_base + 0x18, 0x04,
                         'door: max d^2 bot-to-door when latching a wedge to a blocked door'),
            ScratchField('door_tmp_d2', door_base + 0x1C, 0x04,
                         'door: per-call d^2 temp (door_capture_wedge)'),
            ScratchField('door_tmp_best', door_base + 0x20, 0x04,
                         'door: per-call best-d^2 tracker (door_capture_wedge)'),
            ScratchField('door_table', door_base + 0x24,
                         door_table_max_capped * 8,
                         'door: float[2] per door center (world coords, active map)'),
            ScratchField('door_blocked', door_base + 0x24 + door_table_max_capped * 8,
                         door_table_max_capped * 4,
                         'door: 1 = a SOLID entity sits on this anchor (door closed)'),
            ScratchField('route_block_door',
                         door_base + 0x24 + door_table_max_capped * 12,
                         MAX_BOT_SLOTS * 4,
                         'door: per-bot door idx the failed-edge marker wedged against (-1 none)'),
        ])
        door_static_base = door_base + 0x24 + door_table_max_capped * 12 + MAX_BOT_SLOTS * 4
        overlay_fields.extend([
            ScratchField('door_static_map_count', door_static_base + 0x00, 0x04,
                         'door: build-time static map table count'),
            ScratchField('door_static_point_count', door_static_base + 0x04, 0x04,
                         'door: build-time static point table count'),
        ])
        door_static_base += 0x08
        if door_static_map_max_capped > 0 and door_map_stride > 8:
            overlay_fields.append(ScratchField(
                'door_static_maps', door_static_base,
                door_static_map_max_capped * door_map_stride,
                'door: static map records (name/count/first point)',
            ))
            door_static_base += door_static_map_max_capped * door_map_stride
        if door_static_point_max_capped > 0:
            overlay_fields.append(ScratchField(
                'door_static_points', door_static_base,
                door_static_point_max_capped * 8,
                'door: static float[2] point table parsed from Data.dat',
            ))
            door_static_base += door_static_point_max_capped * 8
            overlay_fields.append(ScratchField(
                'door_static_flags', door_static_base,
                door_static_point_max_capped,
                'door: per-static-point flag byte (bit0 = has ANY authored opener)',
            ))
            door_static_base += door_static_point_max_capped
        if door_opener_static_max_capped > 0:
            overlay_fields.append(ScratchField(
                'door_static_openers', door_static_base,
                door_opener_static_max_capped * 16,
                'door: static (x f32, y f32, door_idx u32, team_mask u32) opener records',
            ))
            door_static_base += door_opener_static_max_capped * 16
        if door_opener_table_max_capped > 0:
            overlay_fields.extend([
                ScratchField('door_opener_count', door_static_base, 0x04,
                             'door: live opener records for the active map'),
                ScratchField('door_opener', door_static_base + 0x04,
                             door_opener_table_max_capped * 16,
                             'door: live (x, y, door_idx, team_mask) bot-usable opener records'),
                ScratchField('door_flags', door_static_base + 0x04 + door_opener_table_max_capped * 16,
                             door_table_max_capped,
                             'door: live per-door flag byte (bit0 = has ANY authored opener)'),
                ScratchField('edge_pass',
                             door_static_base + 0x04 + door_opener_table_max_capped * 16
                             + door_table_max_capped,
                             max(1, overlay_edge_max_capped),
                             'door: per-edge byte — bits0-1 team0 / bits2-3 team1 from-i/from-j closed-door traversability'),
                ScratchField('cnh_blk',
                             door_static_base + 0x04 + door_opener_table_max_capped * 16
                             + door_table_max_capped + max(1, overlay_edge_max_capped),
                             0x04,
                             'door: per-edge blocked-door spill (bfs_run / ctf_next_hop temp)'),
                ScratchField('door_mask_i',
                             door_static_base + 0x08 + door_opener_table_max_capped * 16
                             + door_table_max_capped + max(1, overlay_edge_max_capped),
                             0x04,
                             'door: active from-i edge_pass mask (1 << team*2), set per bfs_run/next-hop'),
                ScratchField('door_mask_j',
                             door_static_base + 0x0C + door_opener_table_max_capped * 16
                             + door_table_max_capped + max(1, overlay_edge_max_capped),
                             0x04,
                             'door: active from-j edge_pass mask (2 << team*2)'),
            ])
            door_static_base += (0x10 + door_opener_table_max_capped * 16
                                 + door_table_max_capped + max(1, overlay_edge_max_capped))

        # --- Per-frame door state + door-aware routing -------------------
        # door_entity[] is the anchor-entity cache maintained by the periodic
        # grid walk; door_refresh_state re-reads the cached entities' SOLID
        # bit every frame (state must not be coupled to the FPS-dependent
        # scan interval). edge_door[] is the static per-match edge->door
        # adjacency; flag_dist_open is the second BFS field that skips
        # closed-door edges (rebuilt on door_dirty, debounced).
        door_entity_slots_capped = max(1, door_entity_slots)
        door_dyn_base = door_static_base
        overlay_fields.extend([
            ScratchField('door_entity', door_dyn_base,
                         door_table_max_capped * door_entity_slots_capped * 4,
                         'door: cached non-character entity ptrs at each door anchor'),
            ScratchField('door_dirty',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4,
                         0x04, 'door: 1 = door_blocked[] changed since last open-field rebuild'),
            ScratchField('door_rebuild_cd',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x04,
                         0x04, 'door: frames until the next open-field rebuild is allowed'),
            ScratchField('route_use_open',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x08,
                         0x04, 'door: 1 = ctf_next_hop scanning the open field (skip blocked edges)'),
            ScratchField('bfs_start',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x0C,
                         0x04, 'door: BFS start node spill (bfs_run input)'),
            ScratchField('bfs_skip',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x10,
                         0x04, 'door: 1 = bfs_run skips edges crossing blocked doors'),
            ScratchField('bed_len2',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x14,
                         0x04, 'door: build_edge_doors |seg|^2 temp'),
            ScratchField('bed_rx',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x18,
                         0x04, 'door: build_edge_doors (door - P).x temp'),
            ScratchField('bed_ry',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x1C,
                         0x04, 'door: build_edge_doors (door - P).y temp'),
            ScratchField('bed_d2',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x20,
                         0x04, 'door: build_edge_doors point-segment d^2 temp'),
            ScratchField('bed_best',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x24,
                         0x04, 'door: build_edge_doors best-d^2 tracker'),
            ScratchField('door_edge_radius_sq',
                         door_dyn_base + door_table_max_capped * door_entity_slots_capped * 4 + 0x28,
                         0x04, 'door: max d^2 door-to-edge-segment for edge_door adjacency'),
        ])
        door_dyn_base += door_table_max_capped * door_entity_slots_capped * 4 + 0x2C
        if overlay_edge_max_capped > 0:
            overlay_fields.append(ScratchField(
                'edge_door', door_dyn_base, overlay_edge_max_capped * 4,
                'door: per-edge nearest door idx within DOOR_EDGE_RADIUS (-1 = none)',
            ))
            door_dyn_base += overlay_edge_max_capped * 4
        if flag_route_max_capped > 0 and overlay_vertex_max_capped > 0:
            # TEAM-MAJOR: row for (team, base) = (team*FLAG_ROUTE_MAX + base).
            # Two fields because closed-door traversability is per-team
            # (same-team-conditional walk-up doors).
            overlay_fields.append(ScratchField(
                'flag_dist_open', door_dyn_base,
                2 * flag_route_max_capped * overlay_vertex_max_capped * 4,
                'door: per-team BFS hop-distance fields gating closed-door edges directionally',
            ))
            door_dyn_base += 2 * flag_route_max_capped * overlay_vertex_max_capped * 4
        # R-snapshot tags for the door-state diagnostic chunks (the fixed tag
        # block at 0x730..0x8FF is full). Only present on door-enabled builds;
        # the static writer and snapshot emitter both skip absent tag fields.
        for tag_field in ('tag_door_cnt', 'tag_door_blk', 'tag_door_ent',
                          'tag_door_dyn', 'tag_edge_door', 'tag_edge_pass',
                          'tag_rstate'):
            overlay_fields.append(ScratchField(
                tag_field, door_dyn_base, 0x10,
                'diag: door-state dump chunk tag',
            ))
            door_dyn_base += 0x10

        # --- Switch tables (CollideTriggerAI bump switches) ----------------
        # Mirrors the door static/live split. Live tables are per-match copies
        # (load_switches); pairs bind switch idx -> door idx in the SAME map's
        # door_table order (u32 = switch_idx | door_idx << 16). Nested inside
        # the door block because pair door indices are meaningless without the
        # door tables.
        switch_table_max_capped = max(0, switch_table_max)
        switch_pair_max_capped = max(0, switch_pair_max)
        switch_static_map_max_capped = max(0, switch_static_map_max)
        switch_static_point_max_capped = max(0, switch_static_point_max)
        switch_static_pair_max_capped = max(0, switch_static_pair_max)
        switch_name_slot_capped = max(0, switch_map_name_slot)
        switch_map_stride = switch_name_slot_capped + 16
        if switch_table_max_capped > 0:
            sw_base = door_dyn_base
            overlay_fields.extend([
                ScratchField('switch_count', sw_base + 0x00, 0x04,
                             'switch: live entries in switch_table for active map'),
                ScratchField('overlay_switch_color', sw_base + 0x04, overlay_color_size,
                             'overlay: detected-switch CColor (rebuilt per-frame)'),
                ScratchField('switch_pair_count', sw_base + 0x14, 0x04,
                             'switch: live (switch, door) pair records for active map'),
                ScratchField('switch_table', sw_base + 0x18,
                             switch_table_max_capped * 8,
                             'switch: float[2] per switch center (world coords, active map)'),
            ])
            sw_off = sw_base + 0x18 + switch_table_max_capped * 8
            if switch_pair_max_capped > 0:
                overlay_fields.append(ScratchField(
                    'switch_pairs', sw_off, switch_pair_max_capped * 4,
                    'switch: live u32 pair records (switch_idx | door_idx << 16)',
                ))
                sw_off += switch_pair_max_capped * 4
            switch_flags_padded = (switch_table_max_capped + 3) & ~3
            overlay_fields.append(ScratchField(
                'switch_flags', sw_off, switch_table_max_capped,
                'switch: live per-switch class byte (door_data.SWITCH_FLAG_*)',
            ))
            sw_off += switch_flags_padded
            overlay_fields.extend([
                ScratchField('switch_static_map_count', sw_off + 0x00, 0x04,
                             'switch: build-time static map table count'),
                ScratchField('switch_static_point_count', sw_off + 0x04, 0x04,
                             'switch: build-time static point table count'),
            ])
            sw_off += 0x08
            if switch_static_map_max_capped > 0 and switch_map_stride > 16:
                overlay_fields.append(ScratchField(
                    'switch_static_maps', sw_off,
                    switch_static_map_max_capped * switch_map_stride,
                    'switch: static map records (name | switch cnt/first | pair cnt/first)',
                ))
                sw_off += switch_static_map_max_capped * switch_map_stride
            if switch_static_point_max_capped > 0:
                overlay_fields.append(ScratchField(
                    'switch_static_points', sw_off,
                    switch_static_point_max_capped * 8,
                    'switch: static float[2] center table parsed from Data.dat',
                ))
                sw_off += switch_static_point_max_capped * 8
                if switch_static_pair_max_capped > 0:
                    overlay_fields.append(ScratchField(
                        'switch_static_pairs', sw_off,
                        switch_static_pair_max_capped * 4,
                        'switch: static u32 pair records (switch_idx | door_idx << 16)',
                    ))
                    sw_off += switch_static_pair_max_capped * 4
                switch_static_flags_padded = (switch_static_point_max_capped + 3) & ~3
                overlay_fields.append(ScratchField(
                    'switch_static_flags', sw_off,
                    switch_static_point_max_capped,
                    'switch: per-static-switch class byte (door_data.SWITCH_FLAG_*)',
                ))
                sw_off += switch_static_flags_padded
            overlay_fields.append(ScratchField(
                'tag_switch', sw_off, 0x10,
                'diag: switch-table dump chunk tag',
            ))
            sw_off += 0x10

            # --- Switch-seek routing state -----------------------------
            # Per-team (2 entries each): a bot whose goal is open-field
            # unreachable (or far cheaper through a closed door) requests a
            # seek; the page-flip eval picks the best viable door-opening
            # switch (paired door blocked, node bound, best full-field score
            # toward the requester's goal), BFS-fills seek_dist rooted at its
            # node with this team's door gating, and activates. ctf_next_hop
            # then descends seek_dist; at the switch node the follower
            # final-approaches the switch center to BUMP it.
            overlay_fields.append(ScratchField(
                'switch_node', sw_off, switch_table_max_capped * 4,
                'seek: nearest graph node per live switch (-1 = unbound)',
            ))
            sw_off += switch_table_max_capped * 4
            for name, desc in (
                ('seek_active',   'seek: [team] active switch idx+1 (0 = none)'),
                ('seek_node',     'seek: [team] graph node of the active switch'),
                ('seek_pending',  'seek: [team] 1 = a bot requested an eval'),
                ('seek_req_node', 'seek: [team] requesting bot\'s node'),
                ('seek_req_goal', 'seek: [team] requesting bot\'s goal base idx'),
                ('seek_tried',    'seek: [team] eval-round tried-candidate bitmask'),
                ('seek_fail',     'seek: [team] timeout blacklist bitmask (cleared on rebuild)'),
                ('seek_timer',    'seek: [team] frames before the active seek expires'),
                ('seek_best',     'seek: [team] eval-round best candidate idx+1 (0 = none)'),
                ('seek_best_score', 'seek: [team] best combined walk+goal score so far'),
                ('seek_req_open', 'seek: [team] requester open-field dist to goal (-1 unreachable) — activation benefit bar'),
            ):
                overlay_fields.append(ScratchField(name, sw_off, 0x08, desc))
                sw_off += 0x08
            overlay_fields.append(ScratchField(
                'bot_seek', sw_off, MAX_BOT_SLOTS * 4,
                'seek: per-bot 1 = descending the seek field this leg',
            ))
            sw_off += MAX_BOT_SLOTS * 4
            # Candidate-index spill for switch_seek_eval. MUST NOT be bfs_u:
            # bfs_run uses bfs_u as its dequeued-node scratch and overwrites
            # it, which mis-attributed every eval result to a NODE id (live
            # R-dump: tried-bit 14 / best 47 on a 2-switch map).
            overlay_fields.append(ScratchField(
                'seek_eval_s', sw_off, 0x04,
                'seek: eval candidate index spill (survives bfs_run)',
            ))
            sw_off += 0x04
            overlay_fields.append(ScratchField(
                'bot_door_patience', sw_off, MAX_BOT_SLOTS * 4,
                'door: per-bot count of progress-timeouts bypassed while wedged at a closed door',
            ))
            sw_off += MAX_BOT_SLOTS * 4
            if overlay_vertex_max_capped > 0:
                overlay_fields.append(ScratchField(
                    'seek_dist', sw_off,
                    2 * overlay_vertex_max_capped * 4,
                    'seek: per-team BFS hop field rooted at the active switch node',
                ))
                sw_off += 2 * overlay_vertex_max_capped * 4
            overlay_fields.append(ScratchField(
                'tag_seek', sw_off, 0x10,
                'diag: switch-seek state dump chunk tag',
            ))
            sw_off += 0x10

    fields.extend(overlay_fields)
    return ScratchLayout(base_va, scratch_size, fields)
