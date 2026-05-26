"""Named layout for the writable scratch area inside .zaxbot."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScratchField:
    name: str
    offset: int
    size: int
    note: str = ''

    @property
    def end(self):
        return self.offset + self.size


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


def build_scratch_layout(base_va, scratch_size, num_bot_names, name_slot_size, name_slot_ascii):
    return ScratchLayout(base_va, scratch_size, [
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
        ScratchField('aidesc', 0x068, 0x04),
        ScratchField('botmode', 0x06C, 0x04),
        ScratchField('hostchar', 0x070, 0x04),
        ScratchField('aicomp', 0x074, 0x04),
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
        ScratchField('bot_controller', 0x128, 0x04),
        ScratchField('bot_dx', 0x12C, 0x04),
        ScratchField('bot_dy', 0x130, 0x04),
        ScratchField('bot_pos', 0x134, 0x08),
        ScratchField('host_pos', 0x13C, 0x08),
        ScratchField('active_bot_slot', 0x150, 0x04),
        ScratchField('max_players', 0x154, 0x04),
        ScratchField('cur_players', 0x158, 0x04),
        ScratchField('msg_full', 0x160, 0x20),
        ScratchField('bot_participants', 0x180, 0x40),
        ScratchField('bot_indices', 0x1C0, 0x40),
        ScratchField('bot_chars', 0x200, 0x40),
        ScratchField('bot_controllers', 0x240, 0x40),
        # Fire/aim scratch (per-detour-call working state).
        ScratchField('cand_pos', 0x280, 0x08, 'fire/aim: per-candidate position'),
        ScratchField('cand_tmp', 0x288, 0x04, 'fire/aim: cand char ptr across helpers'),
        ScratchField('curr_dist_sq', 0x28C, 0x04, 'fire/aim: current cand d^2'),
        ScratchField('best_target', 0x290, 0x04, 'fire/aim: winning char ptr'),
        ScratchField('best_dist_sq', 0x294, 0x04, 'fire/aim: winning d^2'),
        ScratchField('best_dx', 0x298, 0x04, 'fire/aim: winning dx for angle'),
        ScratchField('best_dy', 0x29C, 0x04, 'fire/aim: winning dy for angle'),
        ScratchField('bot_slot_tmp', 0x2A0, 0x04, 'fire/aim: firing bot slot index'),
        ScratchField('cand_idx', 0x2A4, 0x04, 'fire/aim: outer loop counter'),
        ScratchField('our_team_tmp', 0x2A8, 0x04, 'fire/aim: bot team id, -1 = no CTF filter'),
        ScratchField('bot_char_tmp', 0x2AC, 0x04, 'fire/aim: firing bot char ptr'),
        # Team-cache tables (populated at spawn, read by fire/aim).
        ScratchField('bot_team', 0x2B0, 0x40, 'fire/aim: per-slot bot team id (-1=unset)'),
        ScratchField('host_part', 0x2F0, 0x04, 'fire/aim: cached host participant ptr (team read live from +0x14)'),
        # Per-name color tables. bot_colors holds (color1, color2) dword pairs
        # parallel to BOT_NAMES; picked_name_idx preserves the RNG-picked
        # name index so the color lookup at spawn time uses the same row
        # the on-screen name was picked from.
        ScratchField('bot_colors', 0x300, num_bot_names * 8, 'per-name (u32 c1, u32 c2)'),
        ScratchField('picked_name_idx', 0x3A0, 0x04, 'idx into bot_names / bot_colors'),
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
        ScratchField('tag_crashvec', 0x7E8, 0x10),
        ScratchField('tmp_idx', 0x7FC, 0x04),
        ScratchField('cap_dp_edi', 0x800, 0x04),
        ScratchField('my_queue_slot', 0x804, 0x04),
        ScratchField('synthetic_player_id', 0x808, 0x04),
        ScratchField('phase_b_in_flight', 0x80C, 0x04),
        ScratchField('bot_names', 0x900, num_bot_names * name_slot_size),
        ScratchField('bot_names_ascii', 0xB80, num_bot_names * name_slot_ascii),
    ])

