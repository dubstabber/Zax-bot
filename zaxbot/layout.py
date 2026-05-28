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
    AI_PERBOT_FIELDS = (
        ('bot_wander_x',        'wander: random target x (float)'),
        ('bot_wander_y',        'wander: random target y (float)'),
        ('bot_wander_ticks',    'wander: frames left on current target'),
        ('bot_last_x',          'stuck: last-tick x (float)'),
        ('bot_last_y',          'stuck: last-tick y (float)'),
        ('bot_stuck_count',     'stuck: frames with delta < STUCK_DELTA_SQ'),
        ('bot_last_item_scan',  'attractor: frame_counter at last pickup scan'),
        ('bot_pickup_x_cache',  'attractor: cached pickup target x (float)'),
        ('bot_pickup_y_cache',  'attractor: cached pickup target y (float)'),
        ('bot_pickup_valid',    'attractor: 1 if cache has a live target'),
        ('bot_last_damage',     'reactive: last-tick [char+0x7C] cur_damage float'),
        ('bot_flee_ticks',      'reactive: frames left committed to flee target'),
    )
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
        # Per-tick scratch (used inside the bot_movement detour).
        ScratchField('move_tmp_pos',              ai_off + 0x3C + AI_HAZARD_CAP * 12,         0x08,
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
        ScratchField('wp_diag_data',              ai_off + 0x44 + AI_HAZARD_CAP * 12,         0x20,
                     'waypoint diag: 8 raw u32 fields populated by wp_compute'),
    ])
    # --- Waypoint overlay state -------------------------------------------
    # Renderable waypoint set baked from cfg.OVERLAY_WAYPOINTS / EDGES at
    # build time. Capacity ceilings are passed in so detours/overlay.py can
    # iterate by the LIVE count fields without growing the section per
    # waypoint set. Anchored at 0x2000 to keep a clear visual gap from the
    # bot-AI scratch (ends near 0x1F4C) and survive future field churn.
    OVERLAY_BASE = 0x2000
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
    fields.extend(overlay_fields)
    return ScratchLayout(base_va, scratch_size, fields)
