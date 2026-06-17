import hashlib
import os
import struct
import unittest

import zax_patch
from zaxbot import config as cfg
from zaxbot.asm import Asm, le32
from zaxbot.build import build_patched_image as build_section_image
from zaxbot.layout import ScratchField, ScratchLayout, build_scratch_layout
from zaxbot.pe import PEImage, RawBytePatch, RelocationPatch
from zaxbot.patch_manifest import apply_patches
from zaxbot.static_data import pack_tag, write_bot_name_tables, write_static_scratch_data


def rel_target(image, va):
    off = image.va_to_offset(va)
    rel = struct.unpack_from('<i', image.data, off + 1)[0]
    return va + 5 + rel


class AsmTests(unittest.TestCase):
    def test_rel32_label_and_absolute_va_fixups(self):
        a = Asm(0x1000)
        a.call_lbl('target')
        a.jmp_va(0x2000)
        a.label('target')
        a.raw(b'\xC3')

        self.assertEqual(a.link(), bytes.fromhex('e805000000e9f60f0000c3'))

    def test_absolute_label_fixup(self):
        a = Asm(0x710000)
        a.imm32_lbl('target')
        a.raw(b'\x90')
        a.label('target')
        a.raw(b'\xC3')

        self.assertEqual(a.link()[:4], le32(0x710005))


class PEImageTests(unittest.TestCase):
    def setUp(self):
        with open(zax_patch.BAK, 'rb') as f:
            self.original = bytearray(f.read())
        self.image = PEImage(bytearray(self.original), zax_patch.IMAGE_BASE)

    def test_va_to_file_offset_uses_section_table(self):
        self.assertEqual(
            self.image.va_to_offset(zax_patch.HOOK_SITE_VA),
            zax_patch.HOOK_SITE_VA - 0x401000 + 0x1000,
        )

    def test_enabled_patch_original_bytes_match_backup(self):
        for patch in zax_patch.ENABLED_PATCHES:
            with self.subTest(patch=patch.name):
                self.image.expect(patch.va, patch.original)


class ScratchLayoutTests(unittest.TestCase):
    def test_current_layout_has_expected_anchor_offsets(self):
        # Build the full production layout (overlay/waypoint tables included)
        # so the AI-movement and waypoint fields are present to assert.
        layout = build_scratch_layout(
            zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA + zax_patch.SCRATCH_OFF,
            zax_patch.NEW_SECTION_SIZE - zax_patch.SCRATCH_OFF,
            zax_patch.NUM_BOT_NAMES,
            zax_patch.NAME_SLOT_SIZE,
            zax_patch.NAME_SLOT_ASCII,
            cfg.WEAPON_SPEEDS_MAX,
            force_bot_ammo_max=cfg.FORCE_BOT_AMMO_MAX,
            force_bot_ammo_slot_size=cfg.FORCE_BOT_AMMO_SLOT_SIZE,
            overlay_vertex_max=cfg.OVERLAY_VERTEX_MAX,
            overlay_edge_max=cfg.OVERLAY_EDGE_MAX,
        )

        self.assertEqual(layout.off('msg'), 0x30)
        self.assertEqual(layout.off('bot_participants'), 0x180)
        self.assertEqual(layout.off('tmp_idx'), 0x7FC)
        self.assertEqual(layout.off('bot_names'), 0x900)
        self.assertEqual(layout.off('bot_names_ascii'), 0xB80)
        self.assertEqual(layout.off('force_bot_item_name'), 0x1614)
        self.assertEqual(layout.off('force_bot_ammo_count'), 0x1654)
        self.assertEqual(layout.off('force_bot_ammo_names'), 0x1658)

        # Per-bot AI block: the two waypoint-follow nav fields must be the last
        # two entries and contiguous (detour_df90's clear + the -1 init of
        # current_wp/prev_wp rely on this), and must not collide with the
        # overlay region anchored at 0x2000.
        self.assertEqual(layout.off('bot_current_wp'), 0x1D60)
        self.assertEqual(layout.off('bot_prev_wp'), 0x1DA0)
        self.assertEqual(layout.off('bot_wp_try'), 0x1DE0)
        self.assertEqual(
            layout.off('bot_prev_wp'),
            layout.off('bot_current_wp') + 16 * 4,
        )
        # Waypoint-follow knobs sit right after hazard_flee_frames.
        self.assertEqual(layout.off('wp_follow_enabled'), 0x1FDC)
        self.assertEqual(layout.off('wp_reached_radius_sq'), 0x1FE0)
        self.assertLessEqual(layout.field('wp_diag_data').end, 0x2080)

        self.assertLessEqual(layout.used_size, zax_patch.NEW_SECTION_SIZE - zax_patch.SCRATCH_OFF)

    def test_layout_rejects_overlaps_and_overflow(self):
        with self.assertRaises(ValueError):
            ScratchLayout(0x1000, 0x20, [
                ScratchField('a', 0x00, 0x10),
                ScratchField('b', 0x08, 0x10),
            ])

        with self.assertRaises(ValueError):
            ScratchLayout(0x1000, 0x20, [ScratchField('a', 0x10, 0x20)])


class StaticDataTests(unittest.TestCase):
    def test_pack_tag_pads_and_rejects_long_tags(self):
        self.assertEqual(pack_tag('snap', 8), b'snap\x00\x00\x00\x00')
        with self.assertRaises(AssertionError):
            pack_tag('too-long', 4)

    def test_bot_name_tables_are_parallel_utf16_and_ascii(self):
        layout = build_scratch_layout(
            zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA + zax_patch.SCRATCH_OFF,
            zax_patch.NEW_SECTION_SIZE - zax_patch.SCRATCH_OFF,
            1,
            zax_patch.NAME_SLOT_SIZE,
            zax_patch.NAME_SLOT_ASCII,
            cfg.WEAPON_SPEEDS_MAX,
        )
        section = bytearray(zax_patch.NEW_SECTION_SIZE)

        write_bot_name_tables(
            section,
            zax_patch.SCRATCH_OFF,
            layout,
            ['Apex'],
            zax_patch.NAME_SLOT_SIZE,
            zax_patch.NAME_SLOT_ASCII,
        )

        wide_off = zax_patch.SCRATCH_OFF + layout.off('bot_names')
        ascii_off = zax_patch.SCRATCH_OFF + layout.off('bot_names_ascii')
        self.assertEqual(section[wide_off:wide_off + 10], b'A\x00p\x00e\x00x\x00\x00\x00')
        self.assertEqual(section[ascii_off:ascii_off + 5], b'Apex\x00')

    def test_static_scratch_writer_sets_key_tables(self):
        layout = build_scratch_layout(
            zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA + zax_patch.SCRATCH_OFF,
            zax_patch.NEW_SECTION_SIZE - zax_patch.SCRATCH_OFF,
            zax_patch.NUM_BOT_NAMES,
            zax_patch.NAME_SLOT_SIZE,
            zax_patch.NAME_SLOT_ASCII,
            cfg.WEAPON_SPEEDS_MAX,
        )
        section = bytearray(zax_patch.NEW_SECTION_SIZE)

        write_static_scratch_data(
            section,
            zax_patch.SCRATCH_OFF,
            layout,
            dump_filename=zax_patch.DUMP_FILENAME,
            dump_msg=zax_patch.DUMP_MSG,
            step_filename=zax_patch.STEP_FILENAME,
            full_msg=zax_patch.FULL_MSG,
            dump_magic=zax_patch.DUMP_MAGIC,
            dump_tag_len=zax_patch.DUMP_TAG_LEN,
            bot_names=zax_patch.BOT_NAMES,
            name_slot_size=zax_patch.NAME_SLOT_SIZE,
            name_slot_ascii=zax_patch.NAME_SLOT_ASCII,
            bot_colors=zax_patch.BOT_COLORS,
            prompt_dm_va=layout.va('prompt_dm'),
            prompt_ctf_va=layout.va('prompt_ctf'),
            prompt_sk_va=layout.va('prompt_sk'),
            weapon_speeds=[(0x12345678, 12.5)],
            force_bot_item_name=b'Missile Launcher\x00',
        )

        msg_off = zax_patch.SCRATCH_OFF + layout.off('msg')
        tag_off = zax_patch.SCRATCH_OFF + layout.off('tag_part')
        name_off = zax_patch.SCRATCH_OFF + layout.off('bot_names_ascii')
        weapon_off = zax_patch.SCRATCH_OFF + layout.off('weapon_table')
        force_name_off = zax_patch.SCRATCH_OFF + layout.off('force_bot_item_name')
        self.assertEqual(section[msg_off:msg_off + len(zax_patch.DUMP_MSG)], zax_patch.DUMP_MSG)
        self.assertEqual(section[tag_off:tag_off + 8], b'part[X]\x00')
        self.assertEqual(section[name_off:name_off + 8], b'Crusher\x00')
        self.assertEqual(section[weapon_off:weapon_off + 8], struct.pack('<If', 0x12345678, 12.5))
        self.assertEqual(section[force_name_off:force_name_off + 17], b'Missile Launcher\x00')


class PatcherTests(unittest.TestCase):
    def test_patch_manifest_names_and_targets_are_valid(self):
        names = [patch.name for patch in zax_patch.ENABLED_PATCHES]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn('sub_5693A0 waypoint overlay', names)
        self.assertNotIn('sub_53DA40 pickup registration', names)

        _, info = zax_patch.build_hook(zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA)
        for patch in zax_patch.ENABLED_PATCHES:
            with self.subTest(patch=patch.name):
                if isinstance(patch, RelocationPatch):
                    self.assertIn(patch.target_key, info)
                    self.assertGreaterEqual(patch.length, 5)
                    self.assertIn(patch.kind, {'call', 'jmp'})
                elif isinstance(patch, RawBytePatch):
                    self.assertGreater(len(patch.replacement), 0)
                    self.assertLessEqual(len(patch.replacement), len(patch.original))
                else:
                    self.fail(f'unhandled patch type: {type(patch).__name__}')

    def test_build_patched_image_is_deterministic(self):
        data1, info1, raw_off1, applied1 = zax_patch.build_patched_image(zax_patch.BAK)
        data2, info2, raw_off2, applied2 = zax_patch.build_patched_image(zax_patch.BAK)

        self.assertEqual(data1, data2)
        self.assertEqual(info1, info2)
        self.assertEqual(raw_off1, raw_off2)
        self.assertEqual(applied1, applied2)

    def test_generic_builder_matches_zax_wrapper(self):
        wrapped_data, wrapped_info, wrapped_raw_off, wrapped_applied = zax_patch.build_patched_image(zax_patch.BAK)

        generic = build_section_image(
            zax_patch.BAK,
            zax_patch.IMAGE_BASE,
            zax_patch.ZAXBOT_SECTION,
            zax_patch.build_hook,
            zax_patch.ENABLED_PATCHES,
        )

        self.assertEqual(generic.data, wrapped_data)
        self.assertEqual(generic.info, wrapped_info)
        self.assertEqual(generic.raw_off, wrapped_raw_off)
        self.assertEqual(generic.applied, wrapped_applied)
        self.assertEqual(generic.section_va_abs, zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA)

    def test_added_section_and_patch_targets(self):
        data, info, raw_off, applied = zax_patch.build_patched_image(zax_patch.BAK)
        image = PEImage(bytearray(data), zax_patch.IMAGE_BASE)

        self.assertEqual(raw_off, 0x231000)
        self.assertEqual(len(data), os.path.getsize(zax_patch.BAK) + zax_patch.NEW_SECTION_SIZE)

        section = next(s for s in image.sections if s.name == b'.zaxbot')
        self.assertEqual(section.virtual_address, zax_patch.NEW_SECTION_VA)
        self.assertEqual(section.raw_pointer, raw_off)
        self.assertEqual(section.raw_size, zax_patch.NEW_SECTION_SIZE)

        for patch in zax_patch.ENABLED_PATCHES:
            with self.subTest(patch=patch.name):
                off = image.va_to_offset(patch.va)
                if isinstance(patch, RelocationPatch):
                    expected_opcode = b'\xE8' if patch.kind == 'call' else b'\xE9'
                    self.assertEqual(image.data[off:off + 1], expected_opcode)
                    self.assertEqual(rel_target(image, patch.va), info[patch.target_key])
                    if patch.length > 5:
                        self.assertEqual(
                            image.data[off + 5:off + patch.length],
                            b'\x90' * (patch.length - 5),
                        )
                    self.assertEqual(image.data[off:off + patch.length], applied[patch.name])
                elif isinstance(patch, RawBytePatch):
                    self.assertEqual(
                        image.data[off:off + len(patch.replacement)],
                        patch.replacement,
                    )
                    self.assertEqual(applied[patch.name], patch.replacement)
                else:
                    self.fail(f'unhandled patch type: {type(patch).__name__}')

    def test_apply_patches_helper_matches_manifest(self):
        with open(zax_patch.BAK, 'rb') as f:
            data = bytearray(f.read())
        image = PEImage(data, zax_patch.IMAGE_BASE)
        section_bytes, info = zax_patch.build_hook(zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA)
        image.append_section(
            zax_patch.NEW_SECTION_NAME,
            zax_patch.NEW_SECTION_VA,
            zax_patch.NEW_SECTION_SIZE,
            zax_patch.NEW_SECTION_SIZE,
            zax_patch.SECTION_CHARACTERS,
            section_bytes,
        )

        applied = apply_patches(image, zax_patch.ENABLED_PATCHES, info)

        self.assertEqual(set(applied), {patch.name for patch in zax_patch.ENABLED_PATCHES})
        self.assertEqual(applied['WM_KEYDOWN hook'][:1], b'\xE8')
        self.assertEqual(applied['DP poll capture'][:1], b'\xE9')


class GoldenSectionTests(unittest.TestCase):
    """Byte-identity tripwire for the emitted .zaxbot section.

    A refactor that is meant to PRESERVE behavior must keep this SHA green.
    A failure here means the emitted bytes changed — which is exactly what you
    want to know. If the change is INTENTIONAL, regenerate the pinned values:

        python3 -c "import hashlib, zax_patch; \\
            s, i = zax_patch.build_hook(zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA); \\
            print(hashlib.sha256(s).hexdigest(), i['hook_entry_size'])"
    """

    SECTION_SHA256 = '76366ab9f6a5e2935fe55bad11886c4cf97010803ede3ddaee74fdc1740d6f7c'
    HOOK_ENTRY_SIZE = 15703

    def test_zaxbot_section_is_byte_identical(self):
        section, info = zax_patch.build_hook(
            zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA
        )
        self.assertEqual(hashlib.sha256(section).hexdigest(), self.SECTION_SHA256)
        self.assertEqual(info['hook_entry_size'], self.HOOK_ENTRY_SIZE)


class AiPerBotBlockInvariantTests(unittest.TestCase):
    """Guards the per-bot AI scratch block whose size is consumed by both
    ``detours/df90_match_change.py`` (the match-change clear) and
    ``hook/snapshot.py`` (the ``ai_move`` dump). Both derive their counts from
    ``layout.AI_PERBOT_FIELD_COUNT``; these tests pin the value and the ordering
    so appending a field there can't silently desync a consumer."""

    def test_field_count_is_pinned(self):
        from zaxbot.layout import AI_PERBOT_FIELDS, AI_PERBOT_FIELD_COUNT
        self.assertEqual(AI_PERBOT_FIELD_COUNT, len(AI_PERBOT_FIELDS))
        # Bump this (and the golden SHA) deliberately when you add an AI field —
        # the failure is the reminder to re-check df90 + snapshot consumers.
        self.assertEqual(AI_PERBOT_FIELD_COUNT, 15)

    def test_last_three_fields_are_nav_indices(self):
        # df90 re-stamps the final two arrays to -1 and the follower relies on
        # wp_try being the trailing field; this order is load-bearing.
        from zaxbot.layout import AI_PERBOT_FIELDS
        self.assertEqual(
            [name for name, _ in AI_PERBOT_FIELDS[-3:]],
            ['bot_current_wp', 'bot_prev_wp', 'bot_wp_try'],
        )

    def test_block_is_contiguous_at_stride(self):
        # The single rep-stosd clear and the single snapshot chunk both assume
        # the fields are physically contiguous at MAX_BOT_SLOTS*4 spacing.
        from zaxbot.layout import AI_PERBOT_FIELDS, build_scratch_layout
        layout = build_scratch_layout(
            zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA + zax_patch.SCRATCH_OFF,
            zax_patch.NEW_SECTION_SIZE - zax_patch.SCRATCH_OFF,
            zax_patch.NUM_BOT_NAMES,
            zax_patch.NAME_SLOT_SIZE,
            zax_patch.NAME_SLOT_ASCII,
            cfg.WEAPON_SPEEDS_MAX,
            overlay_vertex_max=cfg.OVERLAY_VERTEX_MAX,
            overlay_edge_max=cfg.OVERLAY_EDGE_MAX,
        )
        names = [name for name, _ in AI_PERBOT_FIELDS]
        stride = cfg.MAX_BOT_SLOTS * 4
        first = layout.off(names[0])
        for i, name in enumerate(names):
            self.assertEqual(layout.off(name), first + i * stride)


if __name__ == '__main__':
    unittest.main()
