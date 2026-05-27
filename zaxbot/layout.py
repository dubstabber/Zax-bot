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
    return ScratchLayout(base_va, scratch_size, fields)
