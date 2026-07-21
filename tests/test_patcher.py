import hashlib
import os
import struct
import unittest
from pathlib import Path

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

    def test_portal_routes_resolve_hydro_destinations(self):
        # Hydro Vengence's four pads carry CRelocateAction warps whose New
        # Location ("warm 1/2", "cold 1/2") resolves to positioned Level Parts
        # — the destinations that turn each pad into a directed routing edge.
        # Pin them, and pin the pairing invariant: each destination lands in
        # the OTHER arena right next to the paired return pad, so the pads
        # form two two-way links between the arenas.
        from zaxbot.portal_data import resolve_portal_routes

        maps = dict(resolve_portal_routes())
        hydro = maps['Levels/Multiplayer/CTF/Hydro Vengence.zax']
        self.assertEqual(
            [dest for _src, dest in hydro],
            [(425.0, 291.0), (271.0, 1875.0), (946.0, 1928.0), (824.0, 176.0)],
        )
        srcs = [src for src, _dest in hydro]
        for si, (_src, dest) in enumerate(hydro):
            near = min(range(len(srcs)),
                       key=lambda k: (srcs[k][0]-dest[0])**2 + (srcs[k][1]-dest[1])**2)
            self.assertNotEqual(near, si, 'pad teleports onto itself')
            d2 = (srcs[near][0]-dest[0])**2 + (srcs[near][1]-dest[1])**2
            self.assertLess(d2, 50.0 * 50.0, 'exit not adjacent to a return pad')
        # Jungle Ruins' script teleporters target runtime-resolved entities
        # ("Upper"/"Lower") — no static destination, detect/wander-only.
        jungle = maps['Levels/Multiplayer/DeathMatch/Jungle Ruins.zax']
        self.assertTrue(all(dest is None for _src, dest in jungle))
        # Sources view stays parallel to the routes view.
        from zaxbot.portal_data import resolve_portal_data
        self.assertEqual(
            {n: tuple(s for s, _d in r) for n, r in maps.items()},
            dict(resolve_portal_data()),
        )

    def test_portal_routing_connects_hydro_arenas(self):
        # Offline simulation of the emitted portal BFS relax (bfs_run's
        # bfsr_portals pass + ctf_next_hop's pad scan) on the shipped Hydro
        # Vengence graph. The premise: the two arenas are DISCONNECTED in the
        # plain edge list, so CTF routing could never reach the enemy base.
        # With pads as directed edges (dest node relaxes source node during
        # the goal-outward BFS) each base becomes reachable from the other,
        # and at a departure-arena pad node the pad's destination carries a
        # strictly smaller distance — exactly the condition ctf_next_hop
        # latches the pad final-approach on.
        import struct as _struct
        from pathlib import Path
        from zaxbot.portal_data import resolve_portal_routes
        from zaxbot.flag_data import resolve_flag_data

        name = 'Levels/Multiplayer/CTF/Hydro Vengence.zax'
        d = (Path(__file__).resolve().parents[1] / 'waypoints'
             / (name.replace('/', '_') + '.zwpt')).read_bytes()
        magic, _ver, vc, ec = _struct.unpack('<4sIII', d[:16])
        self.assertEqual(magic, b'ZWPT')
        verts = [_struct.unpack('<ff', d[16 + i*8:24 + i*8]) for i in range(vc)]
        eoff = 16 + vc*8
        edges = []
        for e in range(ec):
            w = _struct.unpack('<I', d[eoff + e*4:eoff + e*4 + 4])[0]
            edges.append((w & 0xFFFF, w >> 16))

        def nearest(x, y):
            return min(range(vc),
                       key=lambda k: (verts[k][0]-x)**2 + (verts[k][1]-y)**2)

        def bfs(goal, portal_edges):
            # Weighted mirror of bfs_run: quantized-length edge costs, pad
            # relaxes at cost 1 (near-free, matching the emitted pass).
            import heapq
            import math
            dist = [-1] * vc
            dist[goal] = 0
            pq = [(0, goal)]
            while pq:
                du, u = heapq.heappop(pq)
                if du > dist[u]:
                    continue
                for (i, j) in edges:
                    v = j if i == u else (i if j == u else None)
                    if v is None:
                        continue
                    w = max(1, round(math.dist(verts[i], verts[j])
                                     / cfg.WP_EDGE_LEN_QUANTUM))
                    if dist[v] == -1 or du + w < dist[v]:
                        dist[v] = du + w
                        heapq.heappush(pq, (du + w, v))
                for (src_n, dest_n) in portal_edges:
                    if dest_n == u and (dist[src_n] == -1 or du + 1 < dist[src_n]):
                        dist[src_n] = du + 1
                        heapq.heappush(pq, (du + 1, src_n))
            return dist

        routes = dict(resolve_portal_routes())[name]
        portal_edges = [(nearest(*src), nearest(*dest)) for src, dest in routes]
        bases = {t: nearest(x, y) for (x, y, t) in dict(resolve_flag_data())[name]}

        for goal_team in (0, 1):
            start = bases[1 - goal_team]
            plain = bfs(bases[goal_team], [])
            self.assertEqual(plain[start], -1,
                             'arenas connected without portals — premise changed '
                             '(re-authored graph?); revisit this test')
            dist = bfs(bases[goal_team], portal_edges)
            self.assertNotEqual(dist[start], -1,
                                'portal edges did not connect the arenas')
            descending = [k for k, (sn, dn) in enumerate(portal_edges)
                          if dist[sn] != -1 and dist[dn] != -1
                          and dist[dn] < dist[sn]]
            self.assertTrue(descending,
                            'no pad offers a strictly-descending hop')

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
            # Weighted mirror of bfs_run (SPFA): edge cost = quantized
            # physical length exactly as build_edge_lens computes it
            # (round-half-even matches the x87 default rounding).
            import heapq
            import math
            dist = [-1] * len(verts)
            dist[start] = 0
            pq = [(0, start)]
            while pq:
                du, u = heapq.heappop(pq)
                if du > dist[u]:
                    continue
                for e, (i, j) in enumerate(edges):
                    if skip and edge_door[e] is not None and blocked[edge_door[e]]:
                        continue
                    v = j if i == u else (i if j == u else None)
                    if v is None:
                        continue
                    w = max(1, round(math.dist(verts[i], verts[j])
                                     / cfg.WP_EDGE_LEN_QUANTUM))
                    if dist[v] == -1 or du + w < dist[v]:
                        dist[v] = du + w
                        heapq.heappush(pq, (du + w, v))
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
                # Activation benefit bar: the switch route must beat the
                # requester's current open route (unless the goal is
                # open-field unreachable, where any viable switch wins).
                if open_[start] != -1 and score >= open_[start]:
                    continue
                if best is None or score < best_score:
                    best, best_score = s, score
            return ('seek', best, best_score)

        # Scores are physical lengths in WP_EDGE_LEN_QUANTUM (16 px) units
        # since the weighted-SPFA change.
        # Torture Chamber: a blue bot sealed in its base by the closed pillar
        # walls picks a REACHABLE pillar toggler — the weighted metric now
        # prefers toggler 3 (physically nearer than toggler 0, which the hop
        # metric used to pick); bumping it unseals the base (open route to
        # the red base drops to 105 units, below the gate), so the choice is
        # a valid escape and the chaining machinery handles any residual.
        self.assertEqual(run('Torture Chamber.zax', 0, 1), ('seek', 3, 98))
        self.assertEqual(run('Torture Chamber.zax', 0, 1, opened=(3,))[0], 'no-gate')
        self.assertEqual(run('Torture Chamber.zax', 0, 1, opened=(0,))[0], 'no-gate')
        self.assertEqual(run('Torture Chamber.zax', 1, 0), ('seek', 1, 92))
        # Battle on the Ice: a red bot at the BLUE base heading home (the
        # live-reported scenario) still picks the blue-door switch INSIDE the
        # blue base — not the red-door switch across the map — because the
        # detour score weighs the walk to the switch.
        self.assertEqual(run('Battle on the Ice.zax', 0, 1), ('seek', 1, 214))
        # A red attacker from its own base picks the near red-door switch
        # (harmless short detour; once bumped its door opens, the candidate
        # filter excludes it, and the next round picks the blue-door switch).
        self.assertEqual(run('Battle on the Ice.zax', 1, 0), ('seek', 0, 242))
        # Hydroplant Bouncefest — the weighted metric's motivating map
        # (live-reported): base-to-base ties at 9 HOPS through the doors and
        # around the top, so the hop gate never fired here; physically the
        # door route is ~680 px shorter. The blue-base bot now seeks the
        # blue-side switch (0, opens door 0) and the red-base bot the
        # red-side switch (3) — score 117 vs the 161-unit around-route. The
        # sim's PHYSICAL door semantics still gate after door 0 opens (door
        # 3 blocks the last leg), but no candidate passes the benefit bar
        # (('seek', None, ...) = pending with no activation, i.e. bots keep
        # the open route); the RUNTIME's directional edge_pass goes further:
        # door 3 is passable from the inside via its arming walk-in trigger,
        # so the real open field equals full and the gate stops entirely.
        self.assertEqual(run('Hydroplant Bouncefest.zax', 0, 1), ('seek', 0, 117))
        self.assertEqual(run('Hydroplant Bouncefest.zax', 1, 0), ('seek', 3, 117))
        self.assertEqual(run('Hydroplant Bouncefest.zax', 0, 1, opened=(0,)),
                         ('seek', None, None))

    def test_switch_seek_join_gate_on_battle_on_the_ice(self):
        # Offline mirror of ctf_next_hop's per-bot ON-THE-WAY join gate for an
        # ACTIVE team seek, with the RUNTIME's directional door semantics
        # (edge_pass: a closed-door edge passes for team T from side S iff a
        # T-usable opener lies on side S; an opener ON the door grants both).
        # Live-diagnosed 2026-07-20: Battle on the Ice's south team door
        # (door 1, team-0 walk-up, self-closing) flips door_blocked every few
        # seconds; each re-close re-activated switch 1 (node 46) for team 0
        # and the OLD unconditional join turned the whole team around
        # (R snapshots: bot_seek=[1,1,1,1,1] with bots at nodes 12/13; slot 1
        # backtracked 14->54). The gate keeps the descent local:
        #   seek_dist[cur] + full(switch -> goal) <= full(cur -> goal) + SLACK
        # so the south-side bots that need the switch still join while bots
        # past the doorway (node 48) or far along the route (13) do not.
        import math as _math
        import struct as _struct
        import heapq as _heapq
        from pathlib import Path
        from zaxbot.door_data import resolve_door_topology
        from zaxbot.flag_data import resolve_flag_data

        topo = {t.map_name.split('/')[-1]: t for t in resolve_door_topology()}
        m = topo['Battle on the Ice.zax']
        p = (Path(__file__).resolve().parents[1] / 'waypoints'
             / 'Levels_Multiplayer_CTF_Battle on the Ice.zax.zwpt')
        d = p.read_bytes()
        _magic, _ver, vc, ec = _struct.unpack('<4sIII', d[:16])
        verts = [_struct.unpack('<ff', d[16 + i*8:24 + i*8]) for i in range(vc)]
        edges = [(w & 0xFFFF, w >> 16)
                 for w in _struct.unpack(f'<{ec}I', d[16 + vc*8:16 + vc*8 + ec*4])]

        def seg_d2(px, py, ax, ay, bx, by):
            vx, vy = bx - ax, by - ay
            wx, wy = px - ax, py - ay
            L2 = vx*vx + vy*vy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, (wx*vx + wy*vy) / L2))
            ddx, ddy = px - (ax + t*vx), py - (ay + t*vy)
            return ddx*ddx + ddy*ddy

        edge_door = []
        for e, (i, j) in enumerate(edges):
            best, bd = None, cfg.DOOR_EDGE_RADIUS_SQ
            for di, (dx, dy) in enumerate(m.doors):
                d2 = seg_d2(dx, dy, *verts[i], *verts[j])
                if d2 < bd:
                    bd, best = d2, di
            edge_door.append(best)

        def pass_bits(e, team):
            di = edge_door[e]
            i, j = edges[e]
            bits = 0
            for (ox, oy, odi, mask) in m.openers:
                if odi != di or not ((mask >> team) & 1):
                    continue
                dx, dy = m.doors[di]
                for bit, n in ((1, i), (2, j)):
                    if (ox-dx)*(verts[n][0]-dx) + (oy-dy)*(verts[n][1]-dy) + 1.0 > 0:
                        bits |= bit
            return bits

        blocked = [True, True]     # authored default: both team doors closed

        def bfs(team, start, gated):
            dist = [-1] * vc
            dist[start] = 0
            pq = [(0, start)]
            while pq:
                du, u = _heapq.heappop(pq)
                if du > dist[u]:
                    continue
                for e, (i, j) in enumerate(edges):
                    v = j if i == u else (i if j == u else None)
                    if v is None:
                        continue
                    if gated and edge_door[e] is not None and blocked[edge_door[e]]:
                        # BFS expands u->v = bot walks v->u: side v's bit.
                        if not (pass_bits(e, team) & (1 if v == i else 2)):
                            continue
                    w = max(1, round(_math.dist(verts[i], verts[j])
                                     / cfg.WP_EDGE_LEN_QUANTUM))
                    if dist[v] == -1 or du + w < dist[v]:
                        dist[v] = du + w
                        _heapq.heappush(pq, (du + w, v))
            return dist

        flags = {n.split('/')[-1]: pts for n, pts in resolve_flag_data()}
        base_node = {t: min(range(vc),
                            key=lambda k: (verts[k][0]-x)**2 + (verts[k][1]-y)**2)
                     for (x, y, t) in flags['Battle on the Ice.zax']}
        sw_nodes = [min(range(vc),
                        key=lambda k: (verts[k][0]-x)**2 + (verts[k][1]-y)**2)
                    for (x, y, _f) in m.switches]
        # Pin the live-observed bindings: switch 1 (opens door 1) binds node
        # 46; door 1 crosses edge (47,48) 30 px from node 47.
        self.assertEqual(sw_nodes[1], 46)
        self.assertEqual(base_node, {0: 44, 1: 111})

        goal = base_node[1]                      # team-0 attackers -> base 1
        full = bfs(0, goal, gated=False)
        seek = bfs(0, sw_nodes[1], gated=True)   # active seek on switch 1
        self.assertEqual(full[sw_nodes[1]], 206)

        def joins(cur):
            s, f = seek[cur], full[cur]
            if s == -1 or full[sw_nodes[1]] == -1:
                return False
            return f == -1 or s + full[sw_nodes[1]] <= f + cfg.SWITCH_SEEK_JOIN_SLACK
        # South-side bots (the switch IS their way through) join ...
        self.assertTrue(joins(44))     # own base node, detour 0
        self.assertTrue(joins(46))     # at the switch, detour 0
        self.assertTrue(joins(47))     # south of the doorway, detour 16
        # ... bots past the doorway or far along the route do NOT.
        self.assertFalse(joins(48))    # NORTH of the doorway, detour 38
        self.assertFalse(joins(51))    # detour 60
        self.assertFalse(joins(14))    # the live slot-1 backtrack, detour 118
        self.assertFalse(joins(13))    # snap6's far recruits, detour 138

        # --- Wedge-cluster hard reset (live 2026-07-20 snaps 1-3): a team-1
        # bot north of the CLOSED south team door stood latched onto in-base
        # nodes (cur flipped 77<->47 with prev=78, marker (78,77)) because
        # every recovery re-picked cross-wall nodes: node 78's arrival ball
        # even pokes through the entrance wall. The hard reset acquires the
        # nearest node EXCLUDING the wedge cluster {failed cur, prev, marker
        # nodes} — from the live position that must pick 48, the entry to the
        # around-route (team-1 open field into the south base is finite via
        # the east side; only node 47 is truly sealed).
        bot = (1557.4, 2770.9)         # snap 2 live position
        def nearest_excluding(p, excl):
            best, bd = -1, None
            for k in range(vc):
                if k in excl:
                    continue
                d2 = (verts[k][0]-p[0])**2 + (verts[k][1]-p[1])**2
                if bd is None or d2 < bd:
                    bd, best = d2, k
            return best
        self.assertEqual(nearest_excluding(bot, set()), 78)          # the trap
        self.assertEqual(nearest_excluding(bot, {47, 77, 78}), 48)   # the escape
        # The escape node genuinely reaches the goal around the wall for
        # team 1 while the wedge-cluster nodes' field values lie (they are
        # near-goal but physically unreachable from the bot's side).
        open1 = bfs(1, base_node[0], gated=True)
        self.assertNotEqual(open1[48], -1)
        self.assertEqual(open1[47], -1)

    def test_door_side_arrival_gate_on_battle_on_the_ice(self):
        # Offline mirror of the follow_arrive door-side ARRIVAL gate (live
        # 2026-07-20, the "blue carrier grinds the wall at the south team
        # gate" report). Node 47 sits 30px behind door 1, so the 64px arrival
        # ball (128px stuck) fires while the bot is still NORTH of the closed
        # door; the next hop then targets an in-base node (snap: prev=47
        # cur=78) whose straight line from the bot's REAL position crosses
        # the wall west of the doorway. The gate refuses an arrival at a node
        # within DOOR_WEDGE_MATCH_RADIUS of a BLOCKED door when
        # dot(bot - door, node - door) < 0 (bot across the door), so the bot
        # keeps pressing INTO the door — which fires its team-0 walk-up
        # opener — instead of latching cross-wall nodes.
        import struct as _struct
        from pathlib import Path
        from zaxbot.door_data import resolve_door_topology

        topo = {t.map_name.split('/')[-1]: t for t in resolve_door_topology()}
        m = topo['Battle on the Ice.zax']
        p = (Path(__file__).resolve().parents[1] / 'waypoints'
             / 'Levels_Multiplayer_CTF_Battle on the Ice.zax.zwpt')
        d = p.read_bytes()
        _magic, _ver, vc, _ec = _struct.unpack('<4sIII', d[:16])
        verts = [_struct.unpack('<ff', d[16 + i*8:24 + i*8]) for i in range(vc)]

        def refuses(node, bot, blocked):
            # Byte-for-byte mirror of the emitted gate predicate.
            nx, ny = verts[node]
            for di, (dx, dy) in enumerate(m.doors):
                if not blocked[di]:
                    continue
                t1, t2 = nx - dx, ny - dy
                if t1*t1 + t2*t2 > cfg.DOOR_WEDGE_MATCH_RADIUS_SQ:
                    continue
                if t1*(bot[0]-dx) + t2*(bot[1]-dy) < 0:
                    return True
            return False

        # Door 1 = the south team door; node 47 is its door-adjacent node
        # (30px inside), node 48 the outside approach node (143px — beyond
        # the wedge radius, never gated).
        d1 = m.doors[1]
        d47 = (verts[47][0]-d1[0])**2 + (verts[47][1]-d1[1])**2
        d48 = (verts[48][0]-d1[0])**2 + (verts[48][1]-d1[1])**2
        self.assertLess(d47, cfg.DOOR_WEDGE_MATCH_RADIUS_SQ)
        self.assertGreater(d48, cfg.DOOR_WEDGE_MATCH_RADIUS_SQ)

        closed = [True, True]
        outside = (1553.7, 2745.1)     # live snap-2 grind position (north)
        at_door_n = (1670.0, 2790.0)   # pressing the door from the north
        inside = (1670.0, 2860.0)      # just south of the door line
        # A bot NORTH of the closed door must not "arrive" at node 47.
        self.assertTrue(refuses(47, outside, closed))
        self.assertTrue(refuses(47, at_door_n, closed))
        # Legit arrivals stay untouched: from inside, at the outside node,
        # and everywhere once the door reads open.
        self.assertFalse(refuses(47, inside, closed))
        self.assertFalse(refuses(48, at_door_n, closed))
        self.assertFalse(refuses(48, outside, closed))
        self.assertFalse(refuses(47, at_door_n, [True, False]))
        # Node 78 (155px from the door) is beyond the gate radius — its
        # cross-wall stuck-arrival is the wedge hard reset's job, which now
        # escalates because stuck-radius arrivals no longer reset
        # bot_wedge_cycles (they enter past the reset at
        # s542360_wp_arrived_gate).
        self.assertFalse(refuses(78, outside, closed))

        # --- door_capture_node_gate (live 2026-07-20 follow-up snaps) -----
        # The routed-timeout press patience used to require a blocked door
        # within the wedge radius of the BOT (door_capture_wedge); the new
        # session caught the carrier timing out while grinding the wall
        # 136px from the door center — no bot-near latch, so the recovery
        # alternated onto cross-wall node 78 and armed the suspension
        # (snap 2: cur=78 wedge=1 susp=233). The node-gate fallback latches
        # by the SAME arrival-gate predicate (target node door-adjacent,
        # bot across), so patience presses instead.
        def node_gate(node, bot, blocked):
            nx, ny = verts[node]
            for di, (dx, dy) in enumerate(m.doors):
                if not blocked[di]:
                    continue
                t1, t2 = nx - dx, ny - dy
                if t1*t1 + t2*t2 > cfg.DOOR_WEDGE_MATCH_RADIUS_SQ:
                    continue
                if t1*(bot[0]-dx) + t2*(bot[1]-dy) < 0:
                    return di
            return -1

        snap1_grind = (1548.8, 2748.5)   # live: cur=47, try=28, 136px from door
        bot_d2 = (snap1_grind[0]-d1[0])**2 + (snap1_grind[1]-d1[1])**2
        # The bot-radius capture genuinely misses this position ...
        self.assertGreater(bot_d2, cfg.DOOR_WEDGE_MATCH_RADIUS_SQ)
        # ... while the node gate latches door 1 via the target node.
        self.assertEqual(node_gate(47, snap1_grind, closed), 1)
        # Door open, or a target on the bot's side -> no latch (normal
        # recovery proceeds).
        self.assertEqual(node_gate(47, snap1_grind, [True, False]), -1)
        self.assertEqual(node_gate(48, snap1_grind, closed), -1)

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


class DroppedFlagTests(unittest.TestCase):
    """Pins the assumptions behind dropped-flag pursuit.

    The runtime identifies a DROPPED flag by exact entity name ("Red Flag" /
    "Blue Flag" — what the canned drop/recreate scripts stamp via New Name)
    gated on ``flag_present[i] == 0``. That gate is only sound if every
    AUTHORED entity carrying one of those names is the at-base flag pickup
    itself (consumed the moment the flag is stolen, so it never coexists with
    ``flag_present == 0``). Census the shipped Data.dat to keep that true."""

    @staticmethod
    def _authored_flag_named_parts():
        from zaxbot.portal_data import _iter_local_files, _find_block

        data_path = Path(zax_patch.__file__).resolve().parent / 'Data.dat'
        if not data_path.exists():
            return None
        named = {}                       # map name -> [part names]
        for name, payload in _iter_local_files(data_path.read_bytes()):
            if not name.lower().endswith('.zax'):
                continue
            if '/multiplayer/' not in name.replace('\\', '/').lower():
                continue
            text = payload.decode('latin1', 'replace').replace('\r\n', '\n')
            lines = text.split('\n')
            idx = 0
            while idx < len(lines):
                if not lines[idx].strip().startswith('Level Part='):
                    idx += 1
                    continue
                start, end = _find_block(lines, idx)
                for raw in lines[start:end]:
                    line = raw.strip()
                    if line.startswith('Name='):
                        part_name = line.split('=', 1)[1]
                        if part_name in ('Red Flag', 'Blue Flag'):
                            named.setdefault(name, []).append(part_name)
                        break
                idx = end
        return named

    def test_dropped_flag_name_census_is_pinned(self):
        from zaxbot.flag_data import resolve_flag_data

        named = self._authored_flag_named_parts()
        if named is None:
            self.skipTest('Data.dat not present')
        # No MP map authors a part named "Red Flag" at all, and exactly the
        # CTF-capable maps (the ones with flag anchors) author ONE part named
        # "Blue Flag" — the at-base blue flag pickup itself. Anything else
        # would be a standing entity that could alias a dropped flag while
        # flag_present == 0, breaking the name-scan's gate.
        self.assertTrue(all(parts == ['Blue Flag'] for parts in named.values()),
                        f'unexpected authored flag-named parts: {named}')
        flag_maps = {name for name, _points in resolve_flag_data()}
        self.assertEqual(set(named), flag_maps)

    def test_hydro_cross_arena_drop_descent_uses_pads(self):
        # Live-reported (dpursuit snapshots): a bot pursuing a cross-arena
        # drop on Hydro Vengence shuttled between two waypoints — the 0<->25
        # orbit with failed-edge marker (0,25). Root cause: the drop_dist
        # descent funnels into the pad-entry node, whose only WALKABLE
        # neighbour ASCENDS (the descent continues only through the pad), so
        # a drop_next_hop without pad-hop emission returned -1 there and the
        # random fallback bounced the bot off the pad node forever. Simulate
        # the emitted semantics (neighbour descent + cnh_pp-style pad hop)
        # on the shipped graph with the snapshot's own drop position and
        # require the walk from the snapshot's bot position to reach the
        # drop node, strictly descending, using at least one pad.
        import struct as _struct
        from zaxbot.portal_data import resolve_portal_routes

        name = 'Levels/Multiplayer/CTF/Hydro Vengence.zax'
        path = (Path(__file__).resolve().parents[1] / 'waypoints'
                / (name.replace('/', '_') + '.zwpt'))
        if not path.exists():
            self.skipTest('shipped Hydro graph not present')
        d = path.read_bytes()
        magic, _ver, vc, ec = _struct.unpack('<4sIII', d[:16])
        self.assertEqual(magic, b'ZWPT')
        verts = [_struct.unpack('<ff', d[16 + i*8:24 + i*8]) for i in range(vc)]
        eoff = 16 + vc*8
        edges = []
        for e in range(ec):
            w = _struct.unpack('<I', d[eoff + e*4:eoff + e*4 + 4])[0]
            edges.append((w & 0xFFFF, w >> 16))
        routes = dict(resolve_portal_routes())[name]

        def nearest(p):
            return min(range(vc),
                       key=lambda i: (verts[i][0]-p[0])**2 + (verts[i][1]-p[1])**2)

        pads = [(nearest(src), nearest(dst)) for src, dst in routes]
        drop_node = nearest((709.6, 1851.8))     # snapshot 6 drop position
        start = nearest((619.0, 330.0))          # snapshot 6 bot position
        # Weighted mirror of bfs_run: quantized-length edge costs, pad cost 1.
        import heapq
        import math
        INF = 0xFFFFFFFF
        dist = [INF]*vc
        dist[drop_node] = 0
        pq = [(0, drop_node)]
        while pq:
            du, u = heapq.heappop(pq)
            if du > dist[u]:
                continue
            for i, j in edges:
                v = j if i == u else (i if j == u else None)
                if v is None:
                    continue
                w = max(1, round(math.dist(verts[i], verts[j])
                                 / cfg.WP_EDGE_LEN_QUANTUM))
                if du + w < dist[v]:
                    dist[v] = du + w
                    heapq.heappush(pq, (du + w, v))
            for sn, dn in pads:                  # bfsr_portals relax
                if dn == u and du + 1 < dist[sn]:
                    dist[sn] = du + 1
                    heapq.heappush(pq, (du + 1, sn))

        self.assertNotEqual(dist[start], INF)
        cur, used_pad = start, False
        for _step in range(vc + 4):
            if cur == drop_node:
                break
            best, best_d = -1, dist[cur]
            for i, j in edges:                   # dnh neighbour scan
                v = j if i == cur else (i if j == cur else None)
                if v is not None and dist[v] < best_d:
                    best_d, best = dist[v], v
            pad_hop = -1
            for p, (sn, dn) in enumerate(pads):  # dnh_pp pad pass
                if sn == cur and dist[dn] < best_d:
                    best_d, pad_hop = dist[dn], p
            if pad_hop >= 0:
                used_pad = True
                cur = pads[pad_hop][1]           # teleport-jump re-acquire
            else:
                self.assertNotEqual(best, -1,
                                    f'drop descent dead-ended at node {cur}')
                cur = best
        self.assertEqual(cur, drop_node)
        self.assertTrue(used_pad, 'cross-arena descent should cross a pad')

    def test_drop_scratch_block_layout_invariants(self):
        # The `dpursuit` snapshot chunk dumps flag_drop_valid ..
        # drop_pursue_enabled as ONE contiguous range, and load_flags clears
        # bot_drop_target..bot_drop_best with ONE rep stosd — both rely on
        # this physical ordering.
        layout = build_scratch_layout(
            zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA + zax_patch.SCRATCH_OFF,
            zax_patch.NEW_SECTION_SIZE - zax_patch.SCRATCH_OFF,
            zax_patch.NUM_BOT_NAMES,
            zax_patch.NAME_SLOT_SIZE,
            zax_patch.NAME_SLOT_ASCII,
            cfg.WEAPON_SPEEDS_MAX,
            overlay_vertex_max=cfg.OVERLAY_VERTEX_MAX,
            overlay_edge_max=cfg.OVERLAY_EDGE_MAX,
            flag_table_max=cfg.FLAG_TABLE_MAX,
            flag_static_map_max=cfg.FLAG_STATIC_MAP_MAX,
            flag_static_point_max=cfg.FLAG_STATIC_POINT_MAX,
            flag_map_name_slot=cfg.FLAG_MAP_NAME_SLOT,
        )
        valid = layout.field('flag_drop_valid')
        pos = layout.field('flag_drop_pos')
        node = layout.field('flag_drop_node')
        target = layout.field('bot_drop_target')
        cd = layout.field('bot_drop_cd')
        try_ = layout.field('bot_drop_try')
        best = layout.field('bot_drop_best')
        roots = layout.field('drop_route_root')
        self.assertEqual(pos.offset, valid.end)
        self.assertEqual(node.offset, pos.end)
        self.assertEqual(target.offset, node.end)
        self.assertEqual(cd.offset, target.end)
        self.assertEqual(try_.offset, cd.end)
        self.assertEqual(best.offset, try_.end)
        self.assertEqual(roots.offset, best.end)
        self.assertEqual(roots.size, 8)
        self.assertEqual(layout.field('drop_pursue_radius_sq').offset, roots.end)
        self.assertEqual(layout.field('drop_reached_radius_sq').offset, roots.end + 4)
        self.assertEqual(layout.field('drop_direct_radius_sq').offset, roots.end + 8)
        self.assertEqual(layout.field('drop_abandon_radius_sq').offset, roots.end + 12)
        self.assertEqual(layout.field('drop_pursue_enabled').offset, roots.end + 16)
        # 16-byte name slots indexed by team (asm does `shl eax, 4`).
        self.assertEqual(layout.field('drop_names').size, 0x20)
        # Two BFS rows (drop_next_hop hard-caps the latch idx at 2).
        self.assertEqual(layout.field('drop_dist').size,
                         2 * cfg.OVERLAY_VERTEX_MAX * 4)


class SwitchWanderTests(unittest.TestCase):
    """Roam switch wander-bump scratch invariants.

    The `swander` snapshot chunk dumps bot_switch_target .. sww_census as ONE
    contiguous range, and load_switches clears bot_switch_target ..
    bot_switch_snap with ONE rep stosd — both rely on this physical ordering.
    """

    def _layout(self):
        return build_scratch_layout(
            zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA + zax_patch.SCRATCH_OFF,
            zax_patch.NEW_SECTION_SIZE - zax_patch.SCRATCH_OFF,
            zax_patch.NUM_BOT_NAMES,
            zax_patch.NAME_SLOT_SIZE,
            zax_patch.NAME_SLOT_ASCII,
            cfg.WEAPON_SPEEDS_MAX,
            overlay_vertex_max=cfg.OVERLAY_VERTEX_MAX,
            overlay_edge_max=cfg.OVERLAY_EDGE_MAX,
            door_table_max=cfg.DOOR_TABLE_MAX,
            door_static_map_max=cfg.DOOR_STATIC_MAP_MAX,
            door_static_point_max=cfg.DOOR_STATIC_POINT_MAX,
            door_map_name_slot=cfg.DOOR_MAP_NAME_SLOT,
            door_opener_table_max=cfg.DOOR_OPENER_TABLE_MAX,
            door_opener_static_max=cfg.DOOR_OPENER_STATIC_MAX,
            switch_table_max=cfg.SWITCH_TABLE_MAX,
            switch_pair_max=cfg.SWITCH_PAIR_MAX,
            switch_static_map_max=cfg.SWITCH_STATIC_MAP_MAX,
            switch_static_point_max=cfg.SWITCH_STATIC_POINT_MAX,
            switch_static_pair_max=cfg.SWITCH_STATIC_PAIR_MAX,
            switch_map_name_slot=cfg.SWITCH_MAP_NAME_SLOT,
        )

    def test_swander_scratch_block_layout_invariants(self):
        layout = self._layout()
        target = layout.field('bot_switch_target')
        cd = layout.field('bot_switch_cd')
        try_ = layout.field('bot_switch_try')
        snap = layout.field('bot_switch_snap')
        chance = layout.field('switch_wander_chance')
        spill = layout.field('sww_spill')
        census = layout.field('sww_census')
        self.assertEqual(cd.offset, target.end)
        self.assertEqual(try_.offset, cd.end)
        self.assertEqual(snap.offset, try_.end)
        self.assertEqual(chance.offset, snap.end)
        self.assertEqual(spill.offset, chance.end)
        self.assertEqual(census.offset, spill.end)
        self.assertEqual(layout.field('tag_swander').offset, census.end)

    def test_all_mp_door_parts_are_centityanimated(self):
        # The door-anchor cache class gate (entity_scan.py) admits ONLY
        # CEntityAnimated entities — the fix for Hydroplant Bouncefest's
        # permanently-"closed" doors, whose anchors host two always-solid
        # CEntityBase scenery models at the exact door position. This pins
        # the premise: every CDoorAI Level Part in every MP map is authored
        # `Level Part=CEntityAnimated`.
        from zaxbot.door_data import _iter_local_files, _find_block
        data_path = os.path.join(os.path.dirname(zax_patch.__file__), 'Data.dat')
        if not os.path.exists(data_path):
            self.skipTest('Data.dat not present')
        with open(data_path, 'rb') as f:
            data = f.read()
        total = 0
        for name, payload in _iter_local_files(data):
            normalized = name.replace('\\', '/').lower()
            if not normalized.endswith('.zax') or '/multiplayer/' not in normalized:
                continue
            lines = payload.decode('latin-1').replace('\r\n', '\n').split('\n')
            idx = 0
            while idx < len(lines):
                if not lines[idx].strip().startswith('Level Part='):
                    idx += 1
                    continue
                start, end = _find_block(lines, idx)
                if any(l.strip() == 'Activity=CDoorAI' for l in lines[start:end]):
                    cls = lines[idx].strip().split('=', 1)[1]
                    self.assertEqual(cls, 'CEntityAnimated',
                                     f'{name}: door part class {cls!r}')
                    total += 1
                idx = end
        self.assertEqual(total, 333)

    def test_swander_block_absent_without_switch_tables(self):
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
        self.assertFalse(layout.has_field('bot_switch_target'))
        self.assertFalse(layout.has_field('switch_wander_chance'))


class SalvageKingTests(unittest.TestCase):
    """Salvage King static census + scratch invariants + routing simulation.

    Census source: zaxbot/sk_data.py parse of the shipped Data.dat
    (2026-07-19). Minerals are Model=Items/Money/* CEntityBase pickups with
    Used In=MultiPlayer/Salvage King; bins are 'Bin NN' CollideTrigger parts
    running the 'Drop Ore in Container' canned, whose authored Team Number ==
    NN-1 == the SK bot team id (botidx). Both are load-bearing for the SK
    behavior layer: the deposit canned gates on the toucher's team matching
    the bin's, so a wrong census means bots press bins that can never score.
    """

    # (ore, crystals, bins) per SK-capable map — 8 Greed maps + Jungle Ruins.
    SK_CENSUS = {
        'Caves of Gold.zax': (258, 115, 12),
        'Cold Crucible.zax': (66, 59, 8),
        'Cold Sweat.zax': (66, 54, 3),
        'Corridor of Suffering.zax': (51, 56, 4),
        'Jungle Madness.zax': (103, 66, 6),
        'Molten Ice.zax': (92, 71, 3),
        'The Foundry.zax': (262, 124, 16),
        'Underground Frenzy.zax': (66, 59, 8),
        'Jungle Ruins.zax': (182, 106, 10),
    }

    def _sk_maps(self):
        from zaxbot.sk_data import resolve_sk_data
        maps = resolve_sk_data()
        if not maps:
            self.skipTest('Data.dat not present')
        return maps

    def test_sk_census_is_pinned(self):
        maps = self._sk_maps()
        seen = {}
        for m in maps:
            base = m.map_name.replace('\\', '/').rsplit('/', 1)[-1]
            ore = sum(1 for p in m.minerals if p[2] == 0)
            cry = sum(1 for p in m.minerals if p[2] == 1)
            seen[base] = (ore, cry, len(m.bins))
            # Bins must be contiguous team ids [0, N) in team order — the
            # runtime table is INDEXED by team id and the SK spawn path
            # assigns botidx < MaxPlayers == bin count.
            self.assertEqual([b[2] for b in m.bins], list(range(len(m.bins))),
                             f'{base}: bins not contiguous by team')
        self.assertEqual(seen, self.SK_CENSUS)
        # Capacity pins: the static pack and the live table must hold the
        # shipped content (The Foundry peaks at 386 minerals / 16 bins).
        total_minerals = sum(len(m.minerals) for m in maps)
        total_bins = sum(len(m.bins) for m in maps)
        self.assertEqual(total_minerals, 1856)
        self.assertEqual(total_bins, 70)
        self.assertLessEqual(total_minerals, cfg.SK_STATIC_MINERAL_MAX)
        self.assertLessEqual(total_bins, cfg.SK_STATIC_BIN_MAX)
        self.assertLessEqual(len(maps), cfg.SK_STATIC_MAP_MAX)
        self.assertLessEqual(max(len(m.minerals) for m in maps),
                             cfg.SK_MINERAL_TABLE_MAX)
        self.assertLessEqual(max(len(m.bins) for m in maps),
                             cfg.SK_BIN_TABLE_MAX)
        # The reported "only ~80-90% of ores marked" overlay gap was the old
        # 96-slot pickup table saturating: SK mode loads every mineral PLUS
        # the weapon/ammo/health mix (The Foundry: 502 total). Keep the live
        # pickup table above the shipped worst case.
        self.assertGreaterEqual(cfg.PICKUP_TABLE_MAX, 512)

    def test_sk_scratch_block_layout_invariants(self):
        layout = build_scratch_layout(
            zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA + zax_patch.SCRATCH_OFF,
            zax_patch.NEW_SECTION_SIZE - zax_patch.SCRATCH_OFF,
            zax_patch.NUM_BOT_NAMES,
            zax_patch.NAME_SLOT_SIZE,
            zax_patch.NAME_SLOT_ASCII,
            cfg.WEAPON_SPEEDS_MAX,
            overlay_vertex_max=cfg.OVERLAY_VERTEX_MAX,
            overlay_edge_max=cfg.OVERLAY_EDGE_MAX,
            sk_mineral_table_max=cfg.SK_MINERAL_TABLE_MAX,
            sk_bin_table_max=cfg.SK_BIN_TABLE_MAX,
            sk_static_map_max=cfg.SK_STATIC_MAP_MAX,
            sk_static_mineral_max=cfg.SK_STATIC_MINERAL_MAX,
            sk_static_bin_max=cfg.SK_STATIC_BIN_MAX,
            sk_map_name_slot=cfg.SK_MAP_NAME_SLOT,
            sk_pile_table_max=cfg.SK_PILE_TABLE_MAX,
        )
        # load_sk clears bot_sk_return..bot_sk_thresh with ONE rep stosd (8
        # contiguous u32[16] arrays) — pin the physical ordering it relies on.
        perbot = ['bot_sk_return', 'bot_sk_carry', 'bot_sk_dep_try',
                  'bot_pile_target', 'bot_pile_cd', 'bot_pile_try',
                  'bot_pile_best', 'bot_sk_thresh']
        for prev, cur in zip(perbot, perbot[1:]):
            self.assertEqual(layout.field(cur).offset, layout.field(prev).end,
                             f'{cur} not contiguous after {prev}')
            self.assertEqual(layout.field(cur).size, 16 * 4)
        # The `skstate` snapshot chunk dumps sk_routing_active..sk_pile_pos as
        # one range ending right before tag_skstate; the pile ring must sit
        # inside it and the cold data (static pack + BFS rows) after the tag.
        start = layout.field('sk_routing_active')
        pile_pos = layout.field('sk_pile_pos')
        tag = layout.field('tag_skstate')
        self.assertEqual(tag.offset, pile_pos.end)
        self.assertLess(start.offset, pile_pos.offset)
        self.assertGreater(layout.field('sk_static_maps').offset, tag.offset)
        self.assertGreater(layout.field('sk_ore_dist').offset, tag.offset)
        # Row strides the emitted descent code assumes.
        self.assertEqual(layout.field('sk_ore_dist').size,
                         cfg.OVERLAY_VERTEX_MAX * 4)
        self.assertEqual(layout.field('sk_bin_dist').size,
                         cfg.SK_BIN_TABLE_MAX * cfg.OVERLAY_VERTEX_MAX * 4)
        # The pile ring mask in the register detour needs a power of two.
        self.assertEqual(cfg.SK_PILE_TABLE_MAX & (cfg.SK_PILE_TABLE_MAX - 1), 0)

    # (health, energy, shield) filler counts per MP map — the goody-pursuit
    # layer's static anchors (item_data.py, model-prefix classified).
    ITEM_CENSUS = {
        'Battle on the Ice.zax': (0, 13, 11),
        'Curse of the Temple.zax': (0, 8, 8),
        'Doom ship.zax': (0, 9, 7),
        'Hydro Vengence.zax': (0, 7, 4),
        'Temple Melee.zax': (2, 8, 4),
        'Torture Chamber.zax': (0, 5, 4),
        'Hydroplant Bouncefest.zax': (0, 4, 4),
        'Jungle Ruins.zax': (15, 8, 7),
        'Temple Deathgrip.zax': (2, 2, 3),
        'Caves of Gold.zax': (14, 17, 11),
        'Cold Crucible.zax': (8, 6, 1),
        'Cold Sweat.zax': (3, 4, 1),
        'Corridor of Suffering.zax': (4, 7, 1),
        'Jungle Madness.zax': (2, 4, 2),
        'Molten Ice.zax': (0, 1, 1),
        'The Foundry.zax': (0, 19, 16),
        'Underground Frenzy.zax': (8, 6, 1),
    }

    def test_item_census_is_pinned(self):
        from zaxbot.item_data import resolve_item_data, ITEM_CATEGORIES
        maps = resolve_item_data()
        if not maps:
            self.skipTest('Data.dat not present')
        seen = {}
        for m in maps:
            base = m.map_name.replace('\\', '/').rsplit('/', 1)[-1]
            counts = [0] * ITEM_CATEGORIES
            for (_x, _y, cat) in m.items:
                self.assertTrue(0 <= cat < ITEM_CATEGORIES)
                counts[cat] += 1
            seen[base] = tuple(counts)
        self.assertEqual(seen, self.ITEM_CENSUS)
        total = sum(len(m.items) for m in maps)
        self.assertEqual(total, 272)
        self.assertLessEqual(total, cfg.ITEM_STATIC_POINT_MAX)
        self.assertLessEqual(len(maps), cfg.ITEM_STATIC_MAP_MAX)
        self.assertLessEqual(max(len(m.items) for m in maps),
                             cfg.ITEM_TABLE_MAX)
        self.assertEqual(cfg.ITEM_CATEGORIES, ITEM_CATEGORIES)

    def test_goody_scratch_block_layout_invariants(self):
        layout = build_scratch_layout(
            zax_patch.IMAGE_BASE + zax_patch.NEW_SECTION_VA + zax_patch.SCRATCH_OFF,
            zax_patch.NEW_SECTION_SIZE - zax_patch.SCRATCH_OFF,
            zax_patch.NUM_BOT_NAMES,
            zax_patch.NAME_SLOT_SIZE,
            zax_patch.NAME_SLOT_ASCII,
            cfg.WEAPON_SPEEDS_MAX,
            overlay_vertex_max=cfg.OVERLAY_VERTEX_MAX,
            overlay_edge_max=cfg.OVERLAY_EDGE_MAX,
            sk_mineral_table_max=cfg.SK_MINERAL_TABLE_MAX,
            sk_bin_table_max=cfg.SK_BIN_TABLE_MAX,
            sk_static_map_max=cfg.SK_STATIC_MAP_MAX,
            sk_static_mineral_max=cfg.SK_STATIC_MINERAL_MAX,
            sk_static_bin_max=cfg.SK_STATIC_BIN_MAX,
            sk_map_name_slot=cfg.SK_MAP_NAME_SLOT,
            sk_pile_table_max=cfg.SK_PILE_TABLE_MAX,
            item_table_max=cfg.ITEM_TABLE_MAX,
            item_static_map_max=cfg.ITEM_STATIC_MAP_MAX,
            item_static_point_max=cfg.ITEM_STATIC_POINT_MAX,
            item_map_name_slot=cfg.ITEM_MAP_NAME_SLOT,
            item_categories=cfg.ITEM_CATEGORIES,
        )
        # The `goody` snapshot chunk dumps item_routing_active..sk_pile_node
        # as one range ending right before tag_goody; the static pack + BFS
        # fields sit after the tag.
        start = layout.field('item_routing_active')
        pile_node = layout.field('sk_pile_node')
        tag = layout.field('tag_goody')
        self.assertEqual(tag.offset, pile_node.offset + pile_node.size)
        self.assertLess(start.offset, pile_node.offset)
        self.assertGreater(layout.field('item_static_maps').offset, tag.offset)
        self.assertGreater(layout.field('item_dist').offset, tag.offset)
        # Row strides the emitted kind row-select assumes.
        self.assertEqual(layout.field('item_dist').size,
                         cfg.ITEM_CATEGORIES * cfg.OVERLAY_VERTEX_MAX * 4)
        self.assertEqual(layout.field('sk_pile_dist').size,
                         cfg.OVERLAY_VERTEX_MAX * 4)
        self.assertEqual(layout.field('sk_pile_node').size,
                         cfg.SK_PILE_TABLE_MAX * 4)

    def test_sk_routing_fields_on_shipped_graphs(self):
        # Offline simulation of the emitted SK fields on every shipped
        # SK-capable graph: the MULTI-SOURCE mineral field (bfs_run_seeded
        # semantics — every bound mineral node at distance 0, weighted SPFA
        # relax) and the per-team bin rows. Asserts the data premises the
        # follower relies on: minerals bind, every graph node reaches a
        # mineral zone (strict descent terminates at dist 0), and every
        # authored bin's node is reachable from the mineral zones (the
        # RETURN descent can always get home).
        import heapq
        import math
        import struct as _struct
        from pathlib import Path

        maps = self._sk_maps()
        for m in maps:
            name = m.map_name.replace('\\', '/')
            path = (Path(__file__).resolve().parents[1] / 'waypoints'
                    / (name.replace('/', '_') + '.zwpt'))
            if not path.exists():
                continue
            d = path.read_bytes()
            magic, _ver, vc, ec = _struct.unpack('<4sIII', d[:16])
            self.assertEqual(magic, b'ZWPT')
            verts = [_struct.unpack('<ff', d[16 + i*8:24 + i*8])
                     for i in range(vc)]
            eoff = 16 + vc*8
            edges = []
            for e in range(ec):
                w = _struct.unpack('<I', d[eoff + e*4:eoff + e*4 + 4])[0]
                edges.append((w & 0xFFFF, w >> 16))
            self.assertGreater(vc, 0, f'{name}: empty graph')

            def nearest(x, y):
                return min(range(vc),
                           key=lambda k: (verts[k][0]-x)**2 + (verts[k][1]-y)**2)

            def elen(i, j):
                # round-half-even matches the emitted x87 fistp default
                return max(1, round(math.dist(verts[i], verts[j])
                                    / cfg.WP_EDGE_LEN_QUANTUM))

            adj = {}
            for (i, j) in edges:
                if i < vc and j < vc:
                    adj.setdefault(i, []).append(j)
                    adj.setdefault(j, []).append(i)

            def field(sources):
                dist = [-1] * vc
                pq = []
                for s in sources:
                    if dist[s] != 0:
                        dist[s] = 0
                        heapq.heappush(pq, (0, s))
                while pq:
                    du, u = heapq.heappop(pq)
                    if du > dist[u]:
                        continue
                    for v in adj.get(u, ()):
                        w = elen(u, v)
                        if dist[v] == -1 or du + w < dist[v]:
                            dist[v] = du + w
                            heapq.heappush(pq, (du + w, v))
                return dist

            mineral_nodes = sorted({nearest(x, y) for (x, y, _k) in m.minerals})
            self.assertTrue(mineral_nodes, f'{name}: no minerals bound')
            ore = field(mineral_nodes)
            unreachable = [k for k in range(vc) if ore[k] == -1]
            self.assertFalse(
                unreachable,
                f'{name}: nodes {unreachable} cannot reach any mineral zone')

            for (bx, by, team) in m.bins:
                bin_node = nearest(bx, by)
                row = field([bin_node])
                # Every mineral zone must reach the bin (RETURN phase) and
                # the strict descent from the bin must find minerals again
                # (COLLECT phase leaves the bin after depositing).
                for mn in mineral_nodes:
                    self.assertNotEqual(
                        row[mn], -1,
                        f'{name}: bin {team} unreachable from mineral node {mn}')


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

    SECTION_SHA256 = '388a719f00d92d92769ec661a91bf0bff07c7969d0fbcd1943205f3b7e9bdd63'
    HOOK_ENTRY_SIZE = 40660

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
