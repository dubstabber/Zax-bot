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
            pickup_table_max=cfg.PICKUP_TABLE_MAX,
            portal_table_max=cfg.PORTAL_TABLE_MAX,
            portal_static_map_max=cfg.PORTAL_STATIC_MAP_MAX,
            portal_static_point_max=cfg.PORTAL_STATIC_POINT_MAX,
            portal_map_name_slot=cfg.PORTAL_MAP_NAME_SLOT,
            scan_entities_max=cfg.SCAN_ENTITIES_MAX,
            flag_table_max=cfg.FLAG_TABLE_MAX,
            flag_static_map_max=cfg.FLAG_STATIC_MAP_MAX,
            flag_static_point_max=cfg.FLAG_STATIC_POINT_MAX,
            flag_map_name_slot=cfg.FLAG_MAP_NAME_SLOT,
            flag_route_max=cfg.FLAG_ROUTE_MAX,
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
        self.assertFalse(layout.has_field('ft_vel_x'))
        self.assertFalse(layout.has_field('ft_last_x'))

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


class PortalDataTests(unittest.TestCase):
    def test_hydro_vengence_portals_are_extracted_from_data_dat(self):
        from zaxbot.portal_data import resolve_portal_data

        maps = dict(resolve_portal_data())
        self.assertEqual(
            maps['Levels/Multiplayer/CTF/Hydro Vengence.zax'],
            (
                (280.6666666666667, 1847.3333333333333),
                (432.6666666666667, 263.3333333333333),
                (850.6666666666666, 206.0),
                (922.3333333333334, 1885.0),
            ),
        )

    def test_jungle_ruins_script_teleporters_are_extracted(self):
        # Jungle Ruins authors its teleporters as Exit Action ->
        # CMultipleActionsAction -> Action=CTeleportAction whose New Location
        # ("Upper"/"Lower") does NOT resolve to a Level Part name. The old
        # destination-match gate silently dropped exactly this script/event-
        # driven shape; pin the two source pads so the relaxed parser keeps
        # surfacing conditional portals. Confirmed against the live runtime pads
        # (~(1297,2109) and (1614,1431) registered when teleporting in-game).
        from zaxbot.portal_data import resolve_portal_data

        maps = dict(resolve_portal_data())
        self.assertEqual(
            maps['Levels/Multiplayer/DeathMatch/Jungle Ruins.zax'],
            ((1259.5, 2105.0), (1610.25, 1445.0)),
        )

    def test_portal_data_is_multiplayer_scoped(self):
        # The runtime consumer (load_portals) and the overlay are MP-gated, and
        # the fixed scratch table only fits the multiplayer maps, so the build-
        # time parse must stay scoped to them.
        from zaxbot.portal_data import resolve_portal_data

        maps = dict(resolve_portal_data())
        self.assertTrue(maps, 'expected at least the shipped MP portal maps')
        self.assertTrue(all('Multiplayer' in name for name in maps))

    def test_ctf_flag_spawns_are_extracted_from_data_dat(self):
        # Each CTF map authors two flag-base anchors ("Red Flag Spawn" /
        # "Blue Flag Spawn"); pin Hydro Vengence's pair (verified against the
        # live runtime map name "Levels/Multiplayer/CTF/...zax").
        from zaxbot.flag_data import resolve_flag_data

        maps = dict(resolve_flag_data())
        # Each anchor carries its team tag (Red=1, Blue=0) so the runtime maps a
        # bot's own team to its HOME base regardless of file order.
        self.assertEqual(
            maps['Levels/Multiplayer/CTF/Hydro Vengence.zax'],
            ((431.0, 2502.0, 1), (486.0, 1074.0, 0)),
        )

    def test_flag_data_is_multiplayer_scoped_and_two_per_map(self):
        # load_flags powers CTF objective routing. Do not restrict this to the
        # /CTF/ folder: live testing showed CTF mode running on Hydroplant
        # Bouncefest, whose runtime map path is under /DeathMatch/ but whose
        # Red/Blue flag anchors are valid.
        from zaxbot.flag_data import resolve_flag_data

        maps = dict(resolve_flag_data())
        self.assertTrue(maps, 'expected at least the shipped MP flag maps')
        self.assertTrue(all('/Multiplayer/' in name for name in maps))
        self.assertIn('Levels/Multiplayer/DeathMatch/Hydroplant Bouncefest.zax', maps)
        self.assertTrue(all(len(points) == 2 for points in maps.values()))
        # Every map ships exactly one Blue (team 0) and one Red (team 1) base.
        for points in maps.values():
            self.assertEqual(sorted(team for _, _, team in points), [0, 1])

    def test_door_positions_are_extracted_from_data_dat(self):
        # Doors are Level Part=CEntityAnimated blocks carrying Activity=CDoorAI.
        # Pin the per-map counts against the IDA-side census (door-runtime-model
        # notes, 2026-07-16) so a parser regression that starts over- or
        # under-matching blocks is caught immediately.
        from zaxbot.door_data import resolve_door_data

        maps = dict(resolve_door_data())
        expected_counts = {
            'Levels/Multiplayer/CTF/Battle on the Ice.zax': 2,
            'Levels/Multiplayer/CTF/Curse of the Temple.zax': 186,
            'Levels/Multiplayer/CTF/Doom ship.zax': 29,
            'Levels/Multiplayer/CTF/Temple Melee.zax': 17,
            'Levels/Multiplayer/CTF/Torture Chamber.zax': 43,
            'Levels/Multiplayer/DeathMatch/Hydroplant Bouncefest.zax': 4,
            'Levels/Multiplayer/DeathMatch/Jungle Ruins.zax': 6,
            'Levels/Multiplayer/DeathMatch/Temple Deathgrip.zax': 26,
            'Levels/Multiplayer/Greed/Corridor of Suffering.zax': 16,
            'Levels/Multiplayer/Greed/The Foundry.zax': 4,
        }
        self.assertEqual({name: len(points) for name, points in maps.items()},
                         expected_counts)
        self.assertTrue(all('/Multiplayer/' in name for name in maps))
        # Static scratch capacity must cover the shipped data with headroom.
        self.assertLessEqual(len(maps), cfg.DOOR_STATIC_MAP_MAX)
        self.assertLessEqual(sum(len(p) for p in maps.values()),
                             cfg.DOOR_STATIC_POINT_MAX)
        self.assertLessEqual(max(len(p) for p in maps.values()),
                             cfg.DOOR_TABLE_MAX)

    def test_switch_topology_is_extracted_from_data_dat(self):
        # Collide switches (Activity=CollideTriggerAI) — the bumpable wall
        # switches. Pin per-map (switch count, open/toggle pair count)
        # against the 2026-07-18 census. Every shipped MP switch is a
        # player-bump repeatable trigger; the pairs bind each door-opening
        # switch to the door instances its COpen/CToggleDoorAction targets
        # resolve to (Torture Chamber's 4 pillar togglers cover all 43
        # pillar doors — the switch-seek routing foundation).
        from zaxbot.door_data import (
            resolve_door_topology,
            SWITCH_FLAG_OPENS_DOORS, SWITCH_FLAG_PLAYER_BUMP,
            SWITCH_FLAG_CANNED, SWITCH_FLAG_TOGGLE,
        )

        maps = {m.map_name: m for m in resolve_door_topology() if m.switches}
        counts = {name.split('/')[-1]: (len(m.switches), len(m.switch_pairs))
                  for name, m in maps.items()}
        self.assertEqual(counts, {
            'Battle on the Ice.zax': (2, 2),
            'Curse of the Temple.zax': (10, 158),
            'Doom ship.zax': (4, 27),
            'Hydro Vengence.zax': (2, 0),
            'Temple Melee.zax': (6, 6),
            'Torture Chamber.zax': (4, 43),
            'Hydroplant Bouncefest.zax': (4, 4),
            'Jungle Ruins.zax': (15, 3),
            'Caves of Gold.zax': (14, 0),
            'Cold Crucible.zax': (8, 0),
            'Cold Sweat.zax': (3, 0),
            'Corridor of Suffering.zax': (8, 12),
            'Jungle Madness.zax': (6, 0),
            'Molten Ice.zax': (3, 0),
            'The Foundry.zax': (19, 0),
            'Underground Frenzy.zax': (8, 0),
        })
        # Every shipped MP switch is bumpable by players (the whole premise
        # of bots firing switches by steering into them).
        for m in maps.values():
            for (_x, _y, fl) in m.switches:
                self.assertTrue(fl & SWITCH_FLAG_PLAYER_BUMP, m.map_name)
        # Torture Chamber: all 4 are door-TOGGLERS covering all 43 doors.
        tc = maps['Levels/Multiplayer/CTF/Torture Chamber.zax']
        for (_x, _y, fl) in tc.switches:
            self.assertTrue(fl & SWITCH_FLAG_OPENS_DOORS)
            self.assertTrue(fl & SWITCH_FLAG_TOGGLE)
        self.assertEqual(sorted({di for _si, di in tc.switch_pairs}),
                         list(range(43)))
        # The Greed 'Bin NN' deposit switches classify as CANNED, not doors.
        foundry = maps['Levels/Multiplayer/Greed/The Foundry.zax']
        self.assertTrue(any(fl & SWITCH_FLAG_CANNED
                            for (_x, _y, fl) in foundry.switches))
        # Static scratch capacity must cover the shipped data with headroom.
        self.assertLessEqual(len(maps), cfg.SWITCH_STATIC_MAP_MAX)
        total_switches = sum(len(m.switches) for m in maps.values())
        total_pairs = sum(len(m.switch_pairs) for m in maps.values())
        self.assertLessEqual(total_switches, cfg.SWITCH_STATIC_POINT_MAX)
        self.assertLessEqual(total_pairs, cfg.SWITCH_STATIC_PAIR_MAX)
        self.assertLessEqual(max(len(m.switches) for m in maps.values()),
                             cfg.SWITCH_TABLE_MAX)
        self.assertLessEqual(max(len(m.switch_pairs) for m in maps.values()),
                             cfg.SWITCH_PAIR_MAX)

    def test_switch_seek_scenarios_on_shipped_graphs(self):
        # Offline simulation of the emitted switch-seek algorithm on the
        # shipped waypoint graphs + parsed door/switch topology, with all
        # doors CLOSED (authored default). Physical door semantics — exact
        # for these scenarios (Torture pillars have no bot-usable opener;
        # the Battle on the Ice team doors are impassable for the tested
        # wrong team). Mirrors: seek gate (open unreachable OR
        # full+GAIN < open), viability (team-gated BFS from the switch node
        # reaches the requester), and the DETOUR score
        # seek_walk + full_dist(switch -> goal) with best-of-round activation.
        import struct as _struct
        from pathlib import Path
        from zaxbot.door_data import resolve_door_topology, SWITCH_FLAG_OPENS_DOORS
        from zaxbot.flag_data import resolve_flag_data

        def load_graph(map_name):
            p = (Path(__file__).resolve().parents[1] / 'waypoints'
                 / (map_name.replace('/', '_') + '.zwpt'))
            d = p.read_bytes()
            magic, _ver, vc, ec = _struct.unpack('<4sIII', d[:16])
            self.assertEqual(magic, b'ZWPT')
            verts = [_struct.unpack('<ff', d[16 + i*8:24 + i*8]) for i in range(vc)]
            eoff = 16 + vc*8
            edges = []
            for e in range(ec):
                w = _struct.unpack('<I', d[eoff + e*4:eoff + e*4 + 4])[0]
                edges.append((w & 0xFFFF, w >> 16))
            return verts, edges

        def seg_d2(px, py, ax, ay, bx, by):
            vx, vy = bx - ax, by - ay
            wx, wy = px - ax, py - ay
            L2 = vx*vx + vy*vy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, (wx*vx + wy*vy) / L2))
            ddx, ddy = px - (ax + t*vx), py - (ay + t*vy)
            return ddx*ddx + ddy*ddy

        def bfs(verts, edges, edge_door, blocked, start, skip):
            dist = [-1] * len(verts)
            dist[start] = 0
            q = [start]
            while q:
                u = q.pop(0)
                for e, (i, j) in enumerate(edges):
                    if skip and edge_door[e] is not None and blocked[edge_door[e]]:
                        continue
                    v = j if i == u else (i if j == u else None)
                    if v is None or dist[v] != -1:
                        continue
                    dist[v] = dist[u] + 1
                    q.append(v)
            return dist

        def nearest(verts, x, y):
            return min(range(len(verts)),
                       key=lambda k: (verts[k][0]-x)**2 + (verts[k][1]-y)**2)

        def run(map_key, start_team, goal_team, opened=()):
            topo = {m.map_name.split('/')[-1]: m for m in resolve_door_topology()}
            m = topo[map_key]
            full_name = [n for n, _ in resolve_flag_data() if map_key in n][0]
            verts, edges = load_graph(full_name)
            flags = {n.split('/')[-1]: pts for n, pts in resolve_flag_data()}
            blocked = [True] * len(m.doors)
            for s in opened:
                for (si, d) in m.switch_pairs:
                    if si == s:
                        blocked[d] = False
            edge_door = [None] * len(edges)
            for e, (i, j) in enumerate(edges):
                best, bd = None, cfg.DOOR_EDGE_RADIUS_SQ
                for di, (dx, dy) in enumerate(m.doors):
                    d2 = seg_d2(dx, dy, *verts[i], *verts[j])
                    if d2 < bd:
                        bd, best = d2, di
                edge_door[e] = best
            base_node = {t: nearest(verts, x, y) for (x, y, t) in flags[map_key]}
            start, goal = base_node[start_team], base_node[goal_team]
            full = bfs(verts, edges, edge_door, blocked, goal, False)
            open_ = bfs(verts, edges, edge_door, blocked, goal, True)
            gate = open_[start] == -1 or (
                full[start] != -1
                and full[start] + cfg.SWITCH_SEEK_SHORTCUT_GAIN < open_[start])
            if not gate:
                return ('no-gate', full[start], open_[start])
            sw_nodes = [nearest(verts, x, y) for (x, y, _f) in m.switches]
            best, best_score = None, None
            for s, (_x, _y, fl) in enumerate(m.switches):
                if not (fl & SWITCH_FLAG_OPENS_DOORS):
                    continue
                if not any(blocked[d] for (si, d) in m.switch_pairs if si == s):
                    continue
                if full[sw_nodes[s]] == -1:
                    continue
                seek = bfs(verts, edges, edge_door, blocked, sw_nodes[s], True)
                if seek[start] == -1:
                    continue
                score = seek[start] + full[sw_nodes[s]]
                if best is None or score < best_score:
                    best, best_score = s, score
            return ('seek', best, best_score)

        # Torture Chamber: a blue bot sealed in its base by the closed pillar
        # walls picks its OWN base toggler (switches to the enemy base are
        # sealed away), 3-hop walk; after that group opens, the route to the
        # red base is fully open and the gate stops firing.
        self.assertEqual(run('Torture Chamber.zax', 0, 1), ('seek', 0, 8))
        self.assertEqual(run('Torture Chamber.zax', 0, 1, opened=(0,))[0], 'no-gate')
        self.assertEqual(run('Torture Chamber.zax', 1, 0), ('seek', 1, 7))
        # Battle on the Ice: a red bot at the BLUE base heading home (the
        # live-reported scenario) picks the blue-door switch INSIDE the blue
        # base 1 hop away — not the red-door switch across the map — because
        # the detour score weighs the walk to the switch.
        self.assertEqual(run('Battle on the Ice.zax', 0, 1), ('seek', 1, 21))
        # A red attacker from its own base picks the near red-door switch
        # (harmless 2-hop detour; once bumped its door opens, the candidate
        # filter excludes it, and the next round picks the blue-door switch).
        self.assertEqual(run('Battle on the Ice.zax', 1, 0), ('seek', 0, 23))

    def test_door_opener_topology_is_extracted(self):
        # Openers drive DIRECTIONAL closed-door passability: bot-usable
        # walk-in triggers only (touching/pass-through, authored active;
        # collide switches / spawn triggers / relays / timers excluded).
        from zaxbot.door_data import (
            resolve_door_topology, DOOR_FLAG_HAS_ANY_OPENER,
        )

        topo = {m.map_name: m for m in resolve_door_topology()}
        # Hydroplant's one-way doors: exactly one arming walk-in trigger per
        # door (the "Inside get out poly" volumes, both teams); every door has
        # SOME authored opener so none is treated as bump-open.
        hydro = topo['Levels/Multiplayer/DeathMatch/Hydroplant Bouncefest.zax']
        self.assertEqual(len(hydro.doors), 4)
        self.assertEqual(sorted(o[2] for o in hydro.openers), [0, 1, 2, 3])
        self.assertTrue(all(o[3] == 0x3 for o in hydro.openers))
        self.assertTrue(all(f & DOOR_FLAG_HAS_ANY_OPENER for f in hydro.flags))
        # Torture Chamber's pillar walls are toggled by CollideTriggerAI wall
        # switches (CToggleDoorAction) — every door HAS an authored opener,
        # none of them bot-usable: impassable while closed, no bump-open.
        torture = topo['Levels/Multiplayer/CTF/Torture Chamber.zax']
        self.assertEqual(len(torture.doors), 43)
        self.assertEqual(torture.openers, ())
        self.assertTrue(all(f & DOOR_FLAG_HAS_ANY_OPENER for f in torture.flags))
        # Doom ship doors are their own walk-up triggers (Door Name=$trigger)
        # wrapped in a same-team conditional: each self-opener binds to its own
        # door instance and is restricted to one team. The 'lights #1-13#'
        # template targets must expand so the light walls are NOT bump-open.
        doom = topo['Levels/Multiplayer/CTF/Doom ship.zax']
        self.assertEqual(len(doom.openers), 4)
        for (ox, oy, di, mask) in doom.openers:
            self.assertEqual((ox, oy), doom.doors[di])
            self.assertIn(mask, (0x1, 0x2))
        self.assertTrue(all(f & DOOR_FLAG_HAS_ANY_OPENER for f in doom.flags))
        # Temple Deathgrip's timer-cycled spikes use '#1-6#' templates too.
        grip = topo['Levels/Multiplayer/DeathMatch/Temple Deathgrip.zax']
        self.assertTrue(all(f & DOOR_FLAG_HAS_ANY_OPENER for f in grip.flags))
        self.assertEqual(grip.openers, ())
        # Capacity guards for the static scratch tables.
        self.assertLessEqual(sum(len(m.openers) for m in topo.values()),
                             cfg.DOOR_OPENER_STATIC_MAX)
        self.assertLessEqual(max(len(m.openers) for m in topo.values()),
                             cfg.DOOR_OPENER_TABLE_MAX)


class PatcherTests(unittest.TestCase):
    def test_patch_manifest_names_and_targets_are_valid(self):
        names = [patch.name for patch in zax_patch.ENABLED_PATCHES]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn('sub_5693A0 waypoint overlay', names)
        self.assertIn('sub_53DA40 pickup registration', names)
        self.assertIn('sub_4C29F0 CActivateAction apply flag-home event', names)
        self.assertIn('sub_4C2D60 CDeactivateAction apply flag-away event', names)
        self.assertIn('sub_5A9960 CTF score home-flag guard', names)
        # The flag-use guard was removed: the drop-on-death canned script uses
        # the same CUseInventoryItemAction, so a home-flag guard there blocked
        # legitimate flag drops whenever both flags were out.
        self.assertNotIn('sub_5B3100 CTF flag-use home-flag guard', names)

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

    SECTION_SHA256 = '77a701339a00e8c535bae364ff2cd3a04c33947248c12e57f7073df1b1e92a3a'
    HOOK_ENTRY_SIZE = 28512

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
