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
    equip_on = (items_on and cfg.WEAPON_EQUIP_ENABLED
                and layout.has_field('welder_def_key')
                and layout.has_field('bot_equip_cd')
                and layout.has_field('primary_hash'))
    if not items_on:
        a.label('load_items')
        a.raw(b'\xC3')
        a.label('weapon_equip_tick')
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
        if equip_on:
            # Weapon auto-equip per-match state: clear the per-bot check
            # cooldowns and resolve the SPAWN weapon's def key ("Modified
            # Laser Welder" — the weak default loadout every bot starts
            # with; the equip tick upgrades bots off it).
            a.raw(b'\xBF' + le32(layout.va('bot_equip_cd')))        # edi = cds
            a.raw(b'\xB9' + le32(cfg.MAX_BOT_SLOTS))                # ecx = 16
            a.raw(b'\x31\xC0')                                      # eax = 0
            a.raw(b'\xF3\xAB')                                      # rep stosd
            a.raw(b'\x6A\xFF')                                      # push -1
            a.raw(b'\x68' + le32(ax.MODIFIED_LASER_WELDER_STR_VA))  # push name
            a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))          # ecx = registry
            a.call_va(ax.SUB_523DF0_VA)                             # eax = key
            a.raw(b'\xA3' + le32(layout.va('welder_def_key')))
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

        # =================================================================
        # weapon_equip_tick (page flip): upgrade each live bot off the
        # SPAWN weapon. The engine never re-selects a Primary weapon on
        # pickup, so a bot that grabbed a real gun kept firing the starter
        # Modified Laser Welder forever (user-reported: "most of the fight
        # happens with this weapon"). Every WEAPON_EQUIP_CHECK_FRAMES per
        # bot: if the SELECTED Primary item is the welder (or nothing is
        # selected), pick the first carried non-welder Primary item whose
        # can-fire virtual passes (item vtbl+0x98 — pure checks: rounds +
        # reuse delay) and select it via the spawn.py force-switch
        # sequence. A bot already on a working real gun is left untouched
        # (no churn); if every carried gun is empty the welder stays (the
        # engine's own fire path auto-cycles on empty). pushad/popad.
        # =================================================================
        a.label('weapon_equip_tick')
        if not equip_on:
            a.raw(b'\xC3')
            return
        a.raw(b'\x60')                                              # pushad
        a.raw(b'\x83\x3D' + le32(layout.va('welder_def_key')) + b'\x00')
        a.jz('wq_out')                                              # unresolved
        a.raw(b'\x31\xF6')                                          # esi = bot slot
        a.label('wq_loop')
        a.raw(b'\x89\x35' + le32(layout.va('weq_tmp_slot')))        # spill slot
        a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))                 # eax = [mgr]
        a.raw(b'\x85\xC0'); a.jz('wq_out')
        a.raw(b'\x8B\x98\x94\x02\x00\x00')                          # ebx = char count
        a.raw(b'\x8B\x80\x90\x02\x00\x00')                          # eax = char array
        a.raw(b'\x85\xC0'); a.jz('wq_out')
        a.raw(b'\x8B\x14\xB5' + le32(layout.va('bot_indices')))     # edx = bot idx
        a.raw(b'\x85\xD2'); a.jz('wq_next')                         # host/unused
        a.raw(b'\x39\xDA'); a.jae('wq_next')                        # idx >= count
        a.raw(b'\x8B\x3C\x90')                                      # edi = char ptr
        a.raw(b'\x85\xFF'); a.jz('wq_next')
        # Check countdown.
        a.raw(b'\x8B\x04\xB5' + le32(layout.va('bot_equip_cd')))    # eax = cd
        a.raw(b'\x85\xC0'); a.jz('wq_try')
        a.raw(b'\x48')                                              # --cd
        a.raw(b'\x89\x04\xB5' + le32(layout.va('bot_equip_cd')))
        a.jmp('wq_next')

        a.label('wq_try')
        a.raw(b'\xC7\x04\xB5' + le32(layout.va('bot_equip_cd'))
              + le32(max(1, cfg.WEAPON_EQUIP_CHECK_FRAMES)))
        # Lazy "Primary" group-key resolve (shared primary_hash slot —
        # exactly spawn.py's force-weapon pattern).
        a.raw(b'\x83\x3D' + le32(layout.va('primary_hash')) + b'\x00')
        a.jnz('wq_ph_ok')
        a.raw(b'\x6A\xFF')                                          # push -1
        a.raw(b'\x68' + le32(ax.PRIMARY_STR_VA))                    # push "Primary"
        a.raw(b'\xB9' + le32(ax.SLOT_NAME_REGISTRY_VA))             # ecx = registry
        a.call_va(ax.SUB_523DF0_VA)                                 # eax = key
        a.raw(b'\xA3' + le32(layout.va('primary_hash')))
        a.label('wq_ph_ok')
        a.raw(b'\x8B\xCF')                                          # ecx = char
        a.call_va(ax.SUB_4267E0_VA)                                 # eax = inventory
        a.raw(b'\x85\xC0'); a.jz('wq_next')
        a.raw(b'\x8B\xE8')                                          # ebp = inv
        # Is the SELECTED Primary item the welder (or nothing selected)?
        a.raw(b'\xFF\x35' + le32(layout.va('primary_hash')))        # push group key
        a.raw(b'\x8B\xCD')                                          # ecx = inv
        a.call_va(ax.SUB_425290_VA)                                 # eax = sel id / -1
        a.raw(b'\x83\xF8\xFF'); a.jz('wq_scan')                     # none -> find a gun
        a.raw(b'\x50')                                              # push sel id
        a.raw(b'\x8B\xCD')                                          # ecx = inv
        a.raw(b'\x8B\x11')                                          # edx = [inv] vtbl
        a.raw(b'\xFF\x52' + bytes([ax.INVENTORY_GET_WEAPON_OFF]))   # call [edx+0x68]
        a.raw(b'\x85\xC0'); a.jz('wq_scan')
        a.raw(b'\x8B\x50' + bytes([ax.ITEM_DEF_KEY_OFF]))           # edx = def key
        a.raw(b'\x3B\x15' + le32(layout.va('welder_def_key')))      # the welder?
        a.jnz('wq_next')                                            # real gun -> leave it

        # Scan the Primary group for a firable non-welder weapon.
        a.label('wq_scan')
        a.raw(b'\x83\xC8\xFF')                                      # eax = -1 (prev)
        a.label('wq_scan_loop')
        a.raw(b'\xFF\x35' + le32(layout.va('primary_hash')))        # push group key
        a.raw(b'\x50')                                              # push prev id
        a.raw(b'\x8B\xCD')                                          # ecx = inv
        a.call_va(ax.SUB_425350_VA)                                 # eax = next id / -1
        a.raw(b'\x83\xF8\xFF'); a.jz('wq_next')                     # none usable
        a.raw(b'\xA3' + le32(layout.va('weq_tmp_id')))              # candidate id
        a.raw(b'\x50')                                              # push id
        a.raw(b'\x8B\xCD')                                          # ecx = inv
        a.call_va(ax.SUB_424F60_VA)                                 # eax = item
        a.raw(b'\x85\xC0'); a.jz('wq_next')
        a.raw(b'\x8B\x50' + bytes([ax.ITEM_DEF_KEY_OFF]))           # edx = def key
        a.raw(b'\x3B\x15' + le32(layout.va('welder_def_key')))      # the welder itself?
        a.jz('wq_scan_cont')
        a.raw(b'\x57')                                              # push char
        a.raw(b'\x8B\xC8')                                          # ecx = item
        a.raw(b'\x8B\x11')                                          # edx = [item] vtbl
        a.raw(b'\xFF\x92' + le32(ax.ITEM_TRY_FIRE_OFF))             # call [edx+0x98]
        a.raw(b'\x84\xC0'); a.jnz('wq_select')                      # firable -> equip
        a.label('wq_scan_cont')
        a.raw(b'\xA1' + le32(layout.va('weq_tmp_id')))              # eax = prev = id
        a.jmp('wq_scan_loop')

        # Equip: clear pending, engine select, force the switch NOW (the
        # exact spawn.py sequence on the Primary slot — stride 24: timer
        # +0xC, current +0x10, pending +0x14).
        a.label('wq_select')
        a.raw(b'\x8B\x55\x10')                                      # edx = [inv+0x10]
        a.raw(b'\xA1' + le32(layout.va('primary_hash')))            # eax = group key
        a.raw(b'\x8D\x04\x40')                                      # eax *= 3
        a.raw(b'\xC1\xE0\x03')                                      # eax *= 8
        a.raw(b'\xC7\x44\x02\x14\xFF\xFF\xFF\xFF')                  # pending = -1
        a.raw(b'\x6A\x01')                                          # push 1 (auto-equip)
        a.raw(b'\x57')                                              # push char
        a.raw(b'\xFF\x35' + le32(layout.va('primary_hash')))        # push group key
        a.raw(b'\xFF\x35' + le32(layout.va('weq_tmp_id')))          # push item id
        a.raw(b'\x8B\xCD')                                          # ecx = inv
        a.call_va(ax.SUB_425590_VA)
        a.raw(b'\x8B\x55\x10')                                      # edx = [inv+0x10]
        a.raw(b'\xA1' + le32(layout.va('primary_hash')))            # eax = group key
        a.raw(b'\x8D\x04\x40')                                      # eax *= 3
        a.raw(b'\xC1\xE0\x03')                                      # eax *= 8
        a.raw(b'\x8B\x0D' + le32(layout.va('weq_tmp_id')))          # ecx = item id
        a.raw(b'\x89\x4C\x02\x10')                                  # current = id
        a.raw(b'\xC7\x44\x02\x14\xFF\xFF\xFF\xFF')                  # pending = -1
        a.raw(b'\xC7\x44\x02\x0C\x00\x00\x00\x00')                  # switch timer = 0

        a.label('wq_next')
        a.raw(b'\x8B\x35' + le32(layout.va('weq_tmp_slot')))        # esi = slot
        a.raw(b'\x46')                                              # ++slot
        a.raw(b'\x83\xFE' + bytes([cfg.MAX_BOT_SLOTS]))
        a.jb('wq_loop')
        a.label('wq_out')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')

