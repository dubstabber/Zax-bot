"""``load_sk`` — per-match Salvage King mineral anchors, team-indexed
bin tables, item-def key resolves and live-state reset."""

from ... import addresses as ax
from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    # =====================================================================
    # load_sk: per-match Salvage King data. Copies the active map's mineral
    # anchors from the build-time static pack into sk_mineral_table, scatters
    # its bins into the TEAM-indexed sk_bin_table/sk_bin_valid (the authored
    # 'Bin NN' Team Number == NN-1 == the SK bot team id botidx), resolves
    # the two mineral item-def keys through the engine's own name resolver
    # (sub_591FC0(dword_6C0C08, name, -1) — the exact calls the SK stats
    # sync sub_5616B0 makes), binds every mineral and bin to its nearest
    # graph node (wp_load ran earlier in detour_df90), and clears all live
    # SK state (routing gate, per-bot phase latches, pile ring). Called once
    # per match from detour_df90 after load_switches. pushad/popad, no args.
    #
    # sk_pile_tick: once-per-frame TTL decrement for the pile ring (called
    # from the page-flip hook). Bounded 8-slot loop.
    # =====================================================================
    sk_on = (
        layout.has_field('sk_routing_active')
        and layout.has_field('sk_static_maps')
        and layout.has_field('sk_mineral_table')
        and layout.has_field('sk_bin_table')
    )
    if not sk_on:
        a.label('load_sk')
        a.raw(b'\xC3')
        a.label('sk_pile_tick')
        a.raw(b'\xC3')
    else:
        sk_map_stride = cfg.SK_MAP_NAME_SLOT + 16

        a.label('load_sk')
        a.raw(b'\x60')                                              # pushad
        a.raw(b'\xC7\x05' + le32(layout.va('sk_routing_active')) + le32(0))
        a.raw(b'\xC7\x05' + le32(layout.va('sk_mineral_count')) + le32(0))
        a.raw(b'\xC7\x05' + le32(layout.va('sk_bin_count')) + le32(0))
        a.raw(b'\xC7\x05' + le32(layout.va('sk_def_ore')) + le32(0))
        a.raw(b'\xC7\x05' + le32(layout.va('sk_def_crystal')) + le32(0))
        a.raw(b'\xC7\x05' + le32(layout.va('sk_pile_next')) + le32(0))
        a.raw(b'\xFC')                                              # cld
        a.raw(b'\xBF' + le32(layout.va('sk_bin_valid')))            # edi = sk_bin_valid
        a.raw(b'\xB9' + le32(cfg.SK_BIN_TABLE_MAX))                 # ecx = 16
        a.raw(b'\x31\xC0')                                          # eax = 0
        a.raw(b'\xF3\xAB')                                          # rep stosd
        # Per-bot SK state block: bot_sk_return..bot_sk_thresh are 8
        # contiguous u32[16] arrays (layout-pinned) — one clear covers all.
        # bot_sk_thresh = 0 is the "unrolled" sentinel, so every bot rolls a
        # fresh RETURN threshold on its first pickup of the new match.
        a.raw(b'\xBF' + le32(layout.va('bot_sk_return')))           # edi = per-bot block
        a.raw(b'\xB9' + le32(8 * cfg.MAX_BOT_SLOTS))                # ecx = 8 arrays
        a.raw(b'\xF3\xAB')                                          # rep stosd (eax = 0)
        a.raw(b'\xBF' + le32(layout.va('sk_pile_valid')))           # edi = pile TTLs
        a.raw(b'\xB9' + le32(cfg.SK_PILE_TABLE_MAX))                # ecx = ring slots
        a.raw(b'\xF3\xAB')                                          # rep stosd
        a.raw(b'\xBF' + le32(layout.va('sk_bin_node')))             # edi = sk_bin_node
        a.raw(b'\xB9' + le32(cfg.SK_BIN_TABLE_MAX))                 # ecx = 16
        a.raw(b'\x83\xC8\xFF')                                      # eax = -1
        a.raw(b'\xF3\xAB')                                          # rep stosd
        a.raw(b'\xBF' + le32(layout.va('sk_mineral_node')))         # edi = sk_mineral_node
        a.raw(b'\xB9' + le32(cfg.SK_MINERAL_TABLE_MAX))             # ecx = table max
        a.raw(b'\xF3\xAB')                                          # rep stosd (eax = -1)
        # Resolve the two mineral item-def keys by name (engine strings).
        # Mirrors sub_5616B0's lazy caches; the registry is populated at
        # startup, long before any match change.
        a.raw(b'\x6A\xFF')                                          # push -1
        a.raw(b'\x68' + le32(ax.ORE_DEPOSITS_STR_VA))               # push "Ore Deposits"
        a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))              # ecx = item-def registry
        a.call_va(ax.SUB_591FC0_VA)                                 # eax = key (ret 8)
        a.raw(b'\xA3' + le32(layout.va('sk_def_ore')))
        a.raw(b'\x6A\xFF')                                          # push -1
        a.raw(b'\x68' + le32(ax.CRYSTALS_STR_VA))                   # push "Crystals"
        a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))              # ecx = item-def registry
        a.call_va(ax.SUB_591FC0_VA)                                 # eax = key
        a.raw(b'\xA3' + le32(layout.va('sk_def_crystal')))
        # Active map name -> ebp (same bounded match as load_switches).
        a.raw(b'\xA1' + le32(ax.MAP_NAME_CSTRING_VA))               # eax = map CString hdr
        a.raw(b'\x85\xC0'); a.jz('lsk_done')
        a.raw(b'\x83\xC0' + bytes([ax.MAP_NAME_ASCII_OFFSET]))      # eax = ASCII
        a.raw(b'\x80\x38\x00'); a.jz('lsk_done')                    # empty name?
        a.raw(b'\x89\xC5')                                          # ebp = active map name

        a.raw(b'\x8B\x0D' + le32(layout.va('sk_static_map_count'))) # ecx = map count
        a.raw(b'\x85\xC9'); a.jz('lsk_done')
        a.raw(b'\x83\xF9' + bytes([cfg.SK_STATIC_MAP_MAX]))         # defensive cap
        a.jbe('lsk_map_count_ok')
        a.raw(b'\xB9' + le32(cfg.SK_STATIC_MAP_MAX))
        a.label('lsk_map_count_ok')
        a.raw(b'\x31\xF6')                                          # esi = map idx

        a.label('lsk_map_loop')
        a.raw(b'\x39\xCE'); a.jae('lsk_done')                       # idx >= map_count?
        a.raw(b'\x69\xC6' + le32(sk_map_stride))                    # eax = idx * stride
        a.raw(b'\x05' + le32(layout.va('sk_static_maps')))          # eax = &record
        a.raw(b'\x89\xC7')                                          # edi = record
        a.raw(b'\x89\xEA')                                          # edx = active name
        a.raw(b'\x89\xFB')                                          # ebx = record name

        a.label('lsk_str_loop')
        a.raw(b'\x8A\x02')                                          # al = [active]
        a.raw(b'\x3A\x03')                                          # cmp al, [record]
        a.jnz('lsk_next_map')
        a.raw(b'\x84\xC0'); a.jz('lsk_match')                       # both NUL -> equal
        a.raw(b'\x42\x43')                                          # inc edx; inc ebx
        a.jmp('lsk_str_loop')

        a.label('lsk_next_map')
        a.raw(b'\x46')                                              # ++idx
        a.jmp('lsk_map_loop')

        a.label('lsk_match')
        a.raw(b'\x89\xFD')                                          # ebp = record (name done)
        # Minerals: count (capped) + block copy.
        a.raw(b'\x8B\x4D' + bytes([cfg.SK_MAP_NAME_SLOT]))          # ecx = mineral count
        a.raw(b'\x81\xF9' + le32(cfg.SK_MINERAL_TABLE_MAX))         # cmp ecx, live cap
        a.jbe('lsk_min_count_ok')
        a.raw(b'\xB9' + le32(cfg.SK_MINERAL_TABLE_MAX))
        a.label('lsk_min_count_ok')
        a.raw(b'\x89\x0D' + le32(layout.va('sk_mineral_count')))    # sk_mineral_count = ecx
        a.raw(b'\x85\xC9'); a.jz('lsk_bins')
        a.raw(b'\x8B\x5D' + bytes([cfg.SK_MAP_NAME_SLOT + 4]))      # ebx = mineral first
        a.raw(b'\x8D\x34\xDD' + le32(layout.va('sk_static_minerals')))  # esi = src
        a.raw(b'\xBF' + le32(layout.va('sk_mineral_table')))        # edi = dst
        a.raw(b'\xD1\xE1')                                          # ecx *= 2 dwords/point
        a.raw(b'\xFC\xF3\xA5')                                      # cld; rep movsd

        a.label('lsk_bins')
        # Bins: scatter (x, y, team) records into the TEAM-indexed tables.
        a.raw(b'\x8B\x4D' + bytes([cfg.SK_MAP_NAME_SLOT + 8]))      # ecx = bin count
        a.raw(b'\x83\xF9' + bytes([cfg.SK_STATIC_BIN_MAX]))         # defensive cap
        a.jbe('lsk_bin_count_ok')
        a.raw(b'\xB9' + le32(cfg.SK_STATIC_BIN_MAX))
        a.label('lsk_bin_count_ok')
        a.raw(b'\x89\x0D' + le32(layout.va('sk_bin_count')))        # sk_bin_count = ecx
        a.raw(b'\x8B\x5D' + bytes([cfg.SK_MAP_NAME_SLOT + 12]))     # ebx = bin first
        a.raw(b'\x31\xF6')                                          # esi = k
        a.label('lsk_bin_loop')
        a.raw(b'\x3B\x35' + le32(layout.va('sk_bin_count')))        # k >= bin count?
        a.jae('lsk_bind')
        a.raw(b'\x8D\x04\x1E')                                      # eax = first + k
        a.raw(b'\x8D\x04\x40')                                      # eax = (first+k)*3
        a.raw(b'\x8D\x3C\x85' + le32(layout.va('sk_static_bins')))  # edi = &rec (idx*12)
        a.raw(b'\x8B\x47\x08')                                      # eax = rec.team
        a.raw(b'\x83\xF8' + bytes([cfg.SK_BIN_TABLE_MAX]))          # team >= 16?
        a.jae('lsk_bin_next')
        a.raw(b'\x8B\x17')                                          # edx = rec.x bits
        a.raw(b'\x89\x14\xC5' + le32(layout.va('sk_bin_table')))    # bin_table[team].x
        a.raw(b'\x8B\x57\x04')                                      # edx = rec.y bits
        a.raw(b'\x89\x14\xC5' + le32(layout.va('sk_bin_table') + 4))  # bin_table[team].y
        a.raw(b'\xC7\x04\x85' + le32(layout.va('sk_bin_valid')) + le32(1))
        a.label('lsk_bin_next')
        a.raw(b'\x46')                                              # ++k
        a.jmp('lsk_bin_loop')

        a.label('lsk_bind')
        # Node bindings (need the graph wp_load loaded earlier).
        a.raw(b'\x83\x3D' + le32(layout.va('overlay_vertex_count')) + b'\x00')
        a.jz('lsk_done')
        # Minerals.
        a.raw(b'\xC7\x05' + le32(layout.va('sk_spill')) + le32(0))
        a.label('lsk_mbind_loop')
        a.raw(b'\xA1' + le32(layout.va('sk_spill')))                # eax = i
        a.raw(b'\x3B\x05' + le32(layout.va('sk_mineral_count')))    # i >= count?
        a.jae('lsk_bbind')
        a.raw(b'\x3D' + le32(cfg.SK_MINERAL_TABLE_MAX))             # i >= cap?
        a.jae('lsk_bbind')
        a.raw(b'\x8B\x0C\xC5' + le32(layout.va('sk_mineral_table')))    # ecx = x bits
        a.raw(b'\x89\x0D' + le32(layout.va('wp_scratch')))
        a.raw(b'\x8B\x0C\xC5' + le32(layout.va('sk_mineral_table') + 4))  # ecx = y bits
        a.raw(b'\x89\x0D' + le32(layout.va('wp_scratch') + 4))
        a.call_lbl('wp_find_nearest')                               # ebx = nearest or -1
        a.raw(b'\xA1' + le32(layout.va('sk_spill')))                # eax = i
        a.raw(b'\x89\x1C\x85' + le32(layout.va('sk_mineral_node'))) # node[i] = ebx
        a.raw(b'\xFF\x05' + le32(layout.va('sk_spill')))            # ++i
        a.jmp('lsk_mbind_loop')
        # Bins (by team slot).
        a.label('lsk_bbind')
        a.raw(b'\xC7\x05' + le32(layout.va('sk_spill')) + le32(0))
        a.label('lsk_bbind_loop')
        a.raw(b'\xA1' + le32(layout.va('sk_spill')))                # eax = t
        a.raw(b'\x83\xF8' + bytes([cfg.SK_BIN_TABLE_MAX]))          # t >= 16?
        a.jae('lsk_done')
        a.raw(b'\x83\x3C\x85' + le32(layout.va('sk_bin_valid')) + b'\x00')
        a.jz('lsk_bbind_next')
        a.raw(b'\x8B\x0C\xC5' + le32(layout.va('sk_bin_table')))    # ecx = x bits
        a.raw(b'\x89\x0D' + le32(layout.va('wp_scratch')))
        a.raw(b'\x8B\x0C\xC5' + le32(layout.va('sk_bin_table') + 4))  # ecx = y bits
        a.raw(b'\x89\x0D' + le32(layout.va('wp_scratch') + 4))
        a.call_lbl('wp_find_nearest')                               # ebx = nearest or -1
        a.raw(b'\xA1' + le32(layout.va('sk_spill')))                # eax = t
        a.raw(b'\x89\x1C\x85' + le32(layout.va('sk_bin_node')))     # bin_node[t] = ebx
        a.label('lsk_bbind_next')
        a.raw(b'\xFF\x05' + le32(layout.va('sk_spill')))            # ++t
        a.jmp('lsk_bbind_loop')

        a.label('lsk_done')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')

        # -----------------------------------------------------------------
        # sk_pile_tick: decrement every nonzero pile-ring TTL once per frame.
        # A TTL that hits 0 EXPIRES the entry — flag sk_pile_dirty so the
        # page flip rebuilds the pile routing field without the dead source.
        # Preserves all registers (runs inside the page-flip pushad frame,
        # but cheap to keep self-contained anyway).
        # -----------------------------------------------------------------
        a.label('sk_pile_tick')
        a.raw(b'\x50\x51')                                          # push eax; push ecx
        a.raw(b'\x31\xC9')                                          # ecx = 0
        a.label('skpt_loop')
        a.raw(b'\x83\xF9' + bytes([cfg.SK_PILE_TABLE_MAX]))         # slot >= ring size?
        a.jae('skpt_done')
        a.raw(b'\x8B\x04\x8D' + le32(layout.va('sk_pile_valid')))   # eax = ttl
        a.raw(b'\x85\xC0'); a.jz('skpt_next')
        a.raw(b'\x48')                                              # --ttl
        a.raw(b'\x89\x04\x8D' + le32(layout.va('sk_pile_valid')))
        if layout.has_field('sk_pile_dirty'):
            a.raw(b'\x85\xC0'); a.jnz('skpt_next')                  # still alive
            a.raw(b'\xC7\x05' + le32(layout.va('sk_pile_dirty'))
                  + le32(1))                                        # expired -> rebuild
        a.label('skpt_next')
        a.raw(b'\x41')                                              # ++slot
        a.jmp('skpt_loop')
        a.label('skpt_done')
        a.raw(b'\x59\x58')                                          # pop ecx; pop eax
        a.raw(b'\xC3')

