#!/usr/bin/env python3
"""Parser + comparator for the Phase A tagged-chunk dump in zax_dump.bin.

Chunk format (see zax_patch.py DUMP_MAGIC / do_snapshot for the writer):
    +0x00  magic   = 'ZAX1' (bytes 5A 41 58 31)
    +0x04  tag     = char[16], zero-padded ASCII (e.g. 'part[1]', 'char[2]', 'dpmgr')
    +0x14  src_va  = u32  game VA the dump was taken from
    +0x18  len     = u32  payload byte length
    +0x1C  payload = bytes[len]

The hook appends one chunk per dumped region per R-press. The 'snap' chunk
contains a single u32 = `snap_counter` and delimits snapshots: every chunk
between two 'snap' markers belongs to the snapshot identified by the FIRST
marker.

Subcommands (run with no args to print usage):
    list                          list every chunk in the file
    snap [N]                      list chunks for snapshot N (default: latest)
    within N TAG1 TAG2            dword-diff TAG1 vs TAG2 inside snapshot N
                                  (e.g. participant comparison: part[1] vs part[2])
    across N1 N2 TAG              dword-diff TAG across two snapshots
                                  (e.g. char[2] before vs after some event)
    hexdump N TAG [--off O] [--len L]   raw hex view of one chunk

Use --file PATH to read a non-default dump (default: ./zax_dump.bin).
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass

DUMP_MAGIC = b'ZAX1'
HEADER_SIZE = 28  # 4 magic + 16 tag + 4 src_va + 4 len

# Mapped-VA range for the patched Zax.exe (image base 0x400000, last section
# .zaxbot ends ~0x71C000). Use a loose upper bound so heap/stack pointers also
# register as "pointer-shaped" — they live in the userland range under Wine.
PTR_LO = 0x00100000
PTR_HI = 0x80000000


@dataclass
class Chunk:
    snap_idx: int     # which snapshot this belongs to (1-based)
    file_off: int     # offset into zax_dump.bin where this chunk's header starts
    tag: str
    src_va: int
    payload: bytes


def parse(path: str) -> list[Chunk]:
    data = open(path, 'rb').read()
    chunks: list[Chunk] = []
    cur_snap = 0
    i = 0
    while i + HEADER_SIZE <= len(data):
        if data[i:i+4] != DUMP_MAGIC:
            j = data.find(DUMP_MAGIC, i + 1)
            if j < 0:
                if i < len(data):
                    print(f'warn: {len(data)-i} trailing bytes at {i:#x} ignored',
                          file=sys.stderr)
                break
            print(f'warn: bad magic at {i:#x}; resyncing at {j:#x}', file=sys.stderr)
            i = j
            continue
        tag = data[i+4:i+20].rstrip(b'\x00').decode('ascii', errors='replace')
        src_va = struct.unpack_from('<I', data, i+20)[0]
        length = struct.unpack_from('<I', data, i+24)[0]
        if i + HEADER_SIZE + length > len(data):
            print(f'warn: truncated chunk at {i:#x} (tag={tag!r}, len={length})',
                  file=sys.stderr)
            break
        payload = data[i+HEADER_SIZE:i+HEADER_SIZE+length]
        if tag == 'snap' and length >= 4:
            cur_snap = struct.unpack('<I', payload[:4])[0]
        chunks.append(Chunk(cur_snap, i, tag, src_va, payload))
        i += HEADER_SIZE + length
    return chunks


def is_pointer(v: int) -> bool:
    return PTR_LO <= v < PTR_HI


def fmt_word(v: int) -> str:
    tag = ''
    if v == 0:
        tag = '         '
    elif is_pointer(v):
        tag = '   (ptr) '
    elif 0 < v < 0x10000:
        tag = f' (={v:<5})'
    else:
        tag = '         '
    return f'{v:08x} {tag}'


def diff_payloads(a: bytes, b: bytes, label_a: str, label_b: str,
                  src_a: int, src_b: int) -> None:
    pa = a
    pb = b
    max_n = max(len(pa), len(pb))
    if len(pa) < max_n:
        pa = pa + b'\x00' * (max_n - len(pa))
    if len(pb) < max_n:
        pb = pb + b'\x00' * (max_n - len(pb))
    end = (max_n // 4) * 4

    print(f'  a = {label_a}  ({len(a)} bytes @ {src_a:#x})')
    print(f'  b = {label_b}  ({len(b)} bytes @ {src_b:#x})')
    print()
    print(f'  {"off":>5}  {"a (dword)":<19}  {"b (dword)":<19}  note')
    print(f'  {"-"*5}  {"-"*19}  {"-"*19}  {"-"*30}')

    diffs = 0
    same = 0
    for off in range(0, end, 4):
        va = struct.unpack_from('<I', pa, off)[0]
        vb = struct.unpack_from('<I', pb, off)[0]
        if va == vb:
            same += 1
            continue
        diffs += 1
        notes: list[str] = []
        if is_pointer(va) and is_pointer(vb):
            notes.append(f'Δ={vb-va:+d}')
        if is_pointer(va) != is_pointer(vb):
            notes.append('*** ptr/scalar')
        if (va == 0) != (vb == 0):
            notes.append('*** zero/nonzero')
        if 0 < abs(va - vb) < 16:
            notes.append('close')
        # Heuristic: small integers often mean flags/counters/ids.
        if va < 0x100 and vb < 0x100 and (va or vb):
            notes.append('small-int')
        print(f'  +{off:04x}  {fmt_word(va)}  {fmt_word(vb)}  {" ".join(notes)}')

    print()
    print(f'  {diffs} differing dwords / {diffs + same} total')


def main() -> None:
    ap = argparse.ArgumentParser(description='Parser/differ for Phase A zax_dump.bin')
    ap.add_argument('--file', default='zax_dump.bin', help='dump file (default: zax_dump.bin)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('list', help='list every chunk')

    p_snap = sub.add_parser('snap', help='list chunks for one snapshot')
    p_snap.add_argument('n', type=int, nargs='?', default=None,
                        help='snapshot number (default: latest)')

    p_within = sub.add_parser('within', help='diff two tags inside one snapshot')
    p_within.add_argument('n', type=int)
    p_within.add_argument('tag1')
    p_within.add_argument('tag2')

    p_across = sub.add_parser('across', help='diff one tag across two snapshots')
    p_across.add_argument('n1', type=int)
    p_across.add_argument('n2', type=int)
    p_across.add_argument('tag')

    p_hd = sub.add_parser('hexdump', help='raw hex view of one chunk')
    p_hd.add_argument('n', type=int)
    p_hd.add_argument('tag')
    p_hd.add_argument('--off', type=lambda s: int(s, 0), default=0)
    p_hd.add_argument('--len', dest='length', type=lambda s: int(s, 0), default=None)

    p_plasma = sub.add_parser('plasma', help='decode the plasma (lava) detection pin chunk')
    p_plasma.add_argument('n', type=int, nargs='?', default=None,
                          help='snapshot number (default: latest)')

    p_cf = sub.add_parser('cross-file', help='diff a tag in one file vs a tag in another file')
    p_cf.add_argument('file1')
    p_cf.add_argument('snap1', type=int)
    p_cf.add_argument('tag1')
    p_cf.add_argument('file2')
    p_cf.add_argument('snap2', type=int)
    p_cf.add_argument('tag2')

    args = ap.parse_args()
    # cross-file passes its own paths and never reads args.file.
    if args.cmd == 'cross-file':
        chunks = []
    else:
        chunks = parse(args.file)
        if not chunks:
            print('no chunks parsed', file=sys.stderr); sys.exit(1)

    def find(n: int, tag: str) -> Chunk:
        for c in chunks:
            if c.snap_idx == n and c.tag == tag:
                return c
        sys.exit(f'no chunk with snap={n} tag={tag!r}')

    if args.cmd == 'list':
        for c in chunks:
            print(f'snap {c.snap_idx:>3}  off={c.file_off:>8x}  '
                  f'tag={c.tag:<12}  src_va={c.src_va:#010x}  len={len(c.payload):#x}')
        return

    if args.cmd == 'snap':
        n = args.n if args.n is not None else max(c.snap_idx for c in chunks)
        print(f'-- snapshot {n} --')
        for c in chunks:
            if c.snap_idx == n:
                print(f'  tag={c.tag:<12}  src_va={c.src_va:#010x}  len={len(c.payload):#x}')
        return

    if args.cmd == 'within':
        c1 = find(args.n, args.tag1)
        c2 = find(args.n, args.tag2)
        diff_payloads(c1.payload, c2.payload,
                      f'{args.tag1}', f'{args.tag2}',
                      c1.src_va, c2.src_va)
        return

    if args.cmd == 'across':
        c1 = find(args.n1, args.tag)
        c2 = find(args.n2, args.tag)
        diff_payloads(c1.payload, c2.payload,
                      f'{args.tag}#snap{args.n1}', f'{args.tag}#snap{args.n2}',
                      c1.src_va, c2.src_va)
        return

    if args.cmd == 'plasma':
        n = args.n if args.n is not None else max(c.snap_idx for c in chunks)
        c = find(n, 'plasma')
        nslot = len(c.payload) // 4
        w = list(struct.unpack_from(f'<{nslot}I', c.payload)) if nslot >= 12 else []
        if not w:
            sys.exit(f'plasma chunk too short ({len(c.payload)} bytes)')

        def tile(v):
            return 'none' if v == 0xFFFFFFFF else f'({(v >> 16) & 0xFFFF},{v & 0xFFFF})'

        labels = [
            'LAY (active CLayer)', '*(LAY+0x7C) candA', '*(LAY+0x40) candB',
            'chosen plasma_map', 'tilepx', 'tw (tiles x)', 'th (tiles y)',
            'host_x', 'host_y', 'host_tx', 'host_ty', 'is_plasma_at@host',
            'heat@host', 'fp_count (cells)', 'heat_count (cells)',
            'fp_max', 'heat_max', 'fp_first_tile', 'heat_first_tile', 'is_plasma_at heat',
        ]
        print(f'-- plasma pin (snapshot {n}) --')
        for i, v in enumerate(w):
            lab = labels[i] if i < len(labels) else f'slot[{i}]'
            note = ''
            if i in (0, 1, 2, 3) and is_pointer(v):
                note = '(ptr)'
            if i == 3:
                if v == 0:
                    note = 'NONE — no CPlasmaTileMap found (non-plasma map?)'
                elif v == w[1]:
                    note = 'matched candA (+0x7C) <-- LAYER_PLASMA_MAP_OFF_A'
                elif len(w) > 2 and v == w[2]:
                    note = 'matched candB (+0x40) <-- LAYER_PLASMA_MAP_OFF_B'
            if i == 11:
                note = 'ON LAVA (heat>=threshold)' if v else 'safe ground'
            if i in (17, 18):
                note = tile(v)
            print(f'  [{i:>2}] {lab:<20} = {v:#010x} ({v}) {note}')

        if nslot >= 19:
            fp_n, heat_n = w[13], w[14]
            print('\n  verdict:')
            print(f'    footprint grid (plasma+0x08): {fp_n} nonzero cells, max {w[15]}, first {tile(w[17])}')
            print(f'    heat grid    (plasma+0x2C6C): {heat_n} nonzero cells, max {w[16]}, first {tile(w[18])}')
            if heat_n and not fp_n:
                print('    => HEAT grid marks the lava region; footprint is empty/unused.')
                print('       M2 should query plasma+0x2C6C (CPLASMA_HEAT_OFF), not the footprint.')
            elif fp_n and not heat_n:
                print('    => FOOTPRINT grid marks lava; keep is_plasma_at on plasma+0x08.')
            elif fp_n and heat_n:
                print('    => BOTH grids populated; compare host_tile vs cur_damage to pick the predicate.')
            else:
                print('    => NEITHER grid has nonzero cells — capture likely missed lava, or a third')
                print('       source drives damage. Re-press R while burning (cur_damage rising).')
        if w[3] and w[4] != 64:
            print(f'  NOTE: tilepx={w[4]} (not 64) — read at runtime, OK')

        # Render the full per-tile heat map (if the pheat chunk is present).
        ph = next((c for c in chunks if c.snap_idx == n and c.tag == 'pheat'), None)
        if ph is not None and len(w) >= 7:
            tw, th = w[5], w[6]
            hx, hy = w[9], w[10]
            m = ph.payload

            def sym(v):
                return ' .:+*#'[0 if v == 0 else 1 if v < 64 else 2 if v < 128
                                  else 3 if v < 192 else 4 if v < 255 else 5]
            if 0 < tw <= 256 and 0 < th <= 256 and tw * th <= len(m):
                print(f'\n  heat map ({tw}x{th})  legend: " "=0  .=1-63  :=64-127  +=128-191  *=192-254  #=255  H=host')
                hist = {}
                for v in m[:tw * th]:
                    hist[v] = hist.get(v, 0) + 1
                for ty in range(th):
                    row = []
                    for tx in range(tw):
                        v = m[ty * tw + tx]
                        row.append('H' if (tx == hx and ty == hy) else sym(v))
                    print('   ' + ''.join(row))
                # Distribution: zero, low gradient, and the top value.
                z = hist.get(0, 0)
                top = max(hist) if hist else 0
                ntop = hist.get(top, 0)
                print(f'\n  distribution: {z} tiles =0 (safe?), '
                      f'{tw*th - z} nonzero, {ntop} tiles ={top} (max).')
                if 0 <= hx < tw and 0 <= hy < th:
                    print(f'  host tile ({hx},{hy}) heat = {m[hy*tw+hx]} '
                          f'(you were burning here -> that value damages)')
        return

    if args.cmd == 'cross-file':
        chunks1 = parse(args.file1)
        chunks2 = parse(args.file2)
        c1 = next((c for c in chunks1 if c.snap_idx == args.snap1 and c.tag == args.tag1), None)
        c2 = next((c for c in chunks2 if c.snap_idx == args.snap2 and c.tag == args.tag2), None)
        if not c1 or not c2:
            sys.exit(f'chunk not found: c1={c1!r} c2={c2!r}')
        diff_payloads(c1.payload, c2.payload,
                      f'{args.tag1}@{args.file1}#snap{args.snap1}',
                      f'{args.tag2}@{args.file2}#snap{args.snap2}',
                      c1.src_va, c2.src_va)
        return

    if args.cmd == 'hexdump':
        c = find(args.n, args.tag)
        end = args.off + (args.length if args.length is not None else len(c.payload) - args.off)
        end = min(end, len(c.payload))
        print(f'  snap={args.n}  tag={args.tag}  src_va={c.src_va:#x}  '
              f'len={len(c.payload):#x}  showing +{args.off:#x}..+{end:#x}')
        for off in range(args.off, end, 16):
            line = c.payload[off:off+16]
            hexpart = ' '.join(f'{b:02x}' for b in line)
            ascpart = ''.join(chr(b) if 0x20 <= b < 0x7f else '.' for b in line)
            print(f'  +{off:04x}  {hexpart:<48}  {ascpart}')
        return


if __name__ == '__main__':
    main()
