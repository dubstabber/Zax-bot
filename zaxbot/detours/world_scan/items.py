"""``load_items`` — per-match filler-item anchors (health/energy/shield)
for the goody-pursuit layer; mode-independent."""

from ... import addresses as ax
from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout


def fields_present(layout: ScratchLayout) -> bool:
    """True when the filler-item layout fields exist (ITEM_PURSUIT_ENABLED).
    Shared gate — ``goody.emit`` keys its scans on the same fields."""
    return (
        layout.has_field('item_static_maps')
        and layout.has_field('item_table')
        and layout.has_field('item_cat')
        and layout.has_field('item_node')
        and layout.has_field('sk_spill')
    )


def emit(a: Asm, layout: ScratchLayout) -> None:
    # =====================================================================
    # load_items: per-match filler-item data for the goody-pursuit layer
    # (mode-independent — fillers exist in DM/CTF/SK alike). Copies the
    # active map's (x, y, category) anchors from the static pack, binds each
    # to its nearest graph node, and resets the live gate/count.
    # build_item_routes (detour_df90, right after this) fills the
    # per-category fields and arms item_routing_active. Same bounded
    # map-name match as every other load_*. pushad/popad, no args.
    # =====================================================================
    items_on = fields_present(layout)
    if not items_on:
        a.label('load_items')
        a.raw(b'\xC3')
    else:
        item_map_stride = cfg.ITEM_MAP_NAME_SLOT + 8

        a.label('load_items')
        a.raw(b'\x60')                                              # pushad
        a.raw(b'\xC7\x05' + le32(layout.va('item_routing_active')) + le32(0))
        a.raw(b'\xC7\x05' + le32(layout.va('item_count')) + le32(0))
        a.raw(b'\xFC')                                              # cld
        a.raw(b'\xBF' + le32(layout.va('item_node')))               # edi = item_node
        a.raw(b'\xB9' + le32(cfg.ITEM_TABLE_MAX))                   # ecx = table max
        a.raw(b'\x83\xC8\xFF')                                      # eax = -1
        a.raw(b'\xF3\xAB')                                          # rep stosd
        # Active map name -> ebp.
        a.raw(b'\xA1' + le32(ax.MAP_NAME_CSTRING_VA))               # eax = map CString hdr
        a.raw(b'\x85\xC0'); a.jz('lit_done')
        a.raw(b'\x83\xC0' + bytes([ax.MAP_NAME_ASCII_OFFSET]))      # eax = ASCII
        a.raw(b'\x80\x38\x00'); a.jz('lit_done')                    # empty name?
        a.raw(b'\x89\xC5')                                          # ebp = active map name

        a.raw(b'\x8B\x0D' + le32(layout.va('item_static_map_count')))  # ecx = map count
        a.raw(b'\x85\xC9'); a.jz('lit_done')
        a.raw(b'\x83\xF9' + bytes([cfg.ITEM_STATIC_MAP_MAX]))       # defensive cap
        a.jbe('lit_map_count_ok')
        a.raw(b'\xB9' + le32(cfg.ITEM_STATIC_MAP_MAX))
        a.label('lit_map_count_ok')
        a.raw(b'\x31\xF6')                                          # esi = map idx

        a.label('lit_map_loop')
        a.raw(b'\x39\xCE'); a.jae('lit_done')                       # idx >= map_count?
        a.raw(b'\x69\xC6' + le32(item_map_stride))                  # eax = idx * stride
        a.raw(b'\x05' + le32(layout.va('item_static_maps')))        # eax = &record
        a.raw(b'\x89\xC7')                                          # edi = record
        a.raw(b'\x89\xEA')                                          # edx = active name
        a.raw(b'\x89\xFB')                                          # ebx = record name

        a.label('lit_str_loop')
        a.raw(b'\x8A\x02')                                          # al = [active]
        a.raw(b'\x3A\x03')                                          # cmp al, [record]
        a.jnz('lit_next_map')
        a.raw(b'\x84\xC0'); a.jz('lit_match')                       # both NUL -> equal
        a.raw(b'\x42\x43')                                          # inc edx; inc ebx
        a.jmp('lit_str_loop')

        a.label('lit_next_map')
        a.raw(b'\x46')                                              # ++idx
        a.jmp('lit_map_loop')

        a.label('lit_match')
        a.raw(b'\x89\xFD')                                          # ebp = record (name done)
        a.raw(b'\x8B\x4D' + bytes([cfg.ITEM_MAP_NAME_SLOT]))        # ecx = item count
        a.raw(b'\x83\xF9' + bytes([cfg.ITEM_TABLE_MAX]))            # cmp ecx, live cap
        a.jbe('lit_count_ok')
        a.raw(b'\xB9' + le32(cfg.ITEM_TABLE_MAX))
        a.label('lit_count_ok')
        a.raw(b'\x89\x0D' + le32(layout.va('item_count')))          # item_count = ecx
        a.raw(b'\x85\xC9'); a.jz('lit_done')
        a.raw(b'\x8B\x5D' + bytes([cfg.ITEM_MAP_NAME_SLOT + 4]))    # ebx = item first
        # Unpack loop: static (x f32, y f32, cat u32) records at 12 bytes ->
        # live item_table (8B) + item_cat (4B), then bind nodes.
        a.raw(b'\x31\xF6')                                          # esi = k
        a.label('lit_copy_loop')
        a.raw(b'\x3B\x35' + le32(layout.va('item_count')))          # k >= count?
        a.jae('lit_bind')
        a.raw(b'\x8D\x04\x1E')                                      # eax = first + k
        a.raw(b'\x8D\x04\x40')                                      # eax = (first+k)*3
        a.raw(b'\x8D\x3C\x85' + le32(layout.va('item_static_points')))  # edi = &rec (idx*12)
        a.raw(b'\x8B\x17')                                          # edx = rec.x bits
        a.raw(b'\x89\x14\xF5' + le32(layout.va('item_table')))      # item_table[k].x
        a.raw(b'\x8B\x57\x04')                                      # edx = rec.y bits
        a.raw(b'\x89\x14\xF5' + le32(layout.va('item_table') + 4))  # item_table[k].y
        a.raw(b'\x8B\x57\x08')                                      # edx = rec.cat
        a.raw(b'\x89\x14\xB5' + le32(layout.va('item_cat')))        # item_cat[k]
        a.raw(b'\x46')                                              # ++k
        a.jmp('lit_copy_loop')

        a.label('lit_bind')
        a.raw(b'\x83\x3D' + le32(layout.va('overlay_vertex_count')) + b'\x00')
        a.jz('lit_done')                                            # no graph -> unbound
        a.raw(b'\xC7\x05' + le32(layout.va('sk_spill')) + le32(0))
        a.label('lit_bind_loop')
        a.raw(b'\xA1' + le32(layout.va('sk_spill')))                # eax = i
        a.raw(b'\x3B\x05' + le32(layout.va('item_count')))          # i >= count?
        a.jae('lit_done')
        a.raw(b'\x3D' + le32(cfg.ITEM_TABLE_MAX))                   # i >= cap?
        a.jae('lit_done')
        a.raw(b'\x8B\x0C\xC5' + le32(layout.va('item_table')))      # ecx = x bits
        a.raw(b'\x89\x0D' + le32(layout.va('wp_scratch')))
        a.raw(b'\x8B\x0C\xC5' + le32(layout.va('item_table') + 4))  # ecx = y bits
        a.raw(b'\x89\x0D' + le32(layout.va('wp_scratch') + 4))
        a.call_lbl('wp_find_nearest')                               # ebx = nearest or -1
        a.raw(b'\xA1' + le32(layout.va('sk_spill')))                # eax = i
        a.raw(b'\x89\x1C\x85' + le32(layout.va('item_node')))       # node[i] = ebx
        a.raw(b'\xFF\x05' + le32(layout.va('sk_spill')))            # ++i
        a.jmp('lit_bind_loop')

        a.label('lit_done')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')

