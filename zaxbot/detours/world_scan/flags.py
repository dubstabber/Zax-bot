"""``load_flags`` — per-match CTF flag-base anchors (static Data.dat
pipeline; seeds ``flag_present[] = 1``)."""

from ... import addresses as ax
from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    # =====================================================================
    # load_flags: copy the build-time CTF flag-base points for the active map
    # into the live flag_table. Called once per match from detour_df90, exactly
    # like load_portals — a bounded active-map-name string match against
    # MAP_NAME_CSTRING_VA, then a rep movsd of the matching map's float[2]
    # points. Inert stub when the flag layout fields are absent.
    # =====================================================================
    if not (
        layout.has_field('flag_table')
        and layout.has_field('flag_static_maps')
        and layout.has_field('flag_static_points')
        and layout.has_field('flag_team')
        and layout.has_field('flag_static_team')
    ):
        a.label('load_flags')
        a.raw(b'\xC3')
    else:
        flag_count_va             = layout.va('flag_count')
        flag_table_va             = layout.va('flag_table')
        flag_team_va              = layout.va('flag_team')
        flag_entity_va            = layout.va('flag_entity') if layout.has_field('flag_entity') else 0
        flag_present_va           = layout.va('flag_present') if layout.has_field('flag_present') else 0
        flag_static_map_count_va  = layout.va('flag_static_map_count')
        flag_static_maps_va       = layout.va('flag_static_maps')
        flag_static_points_va     = layout.va('flag_static_points')
        flag_static_team_va       = layout.va('flag_static_team')
        flag_map_stride           = cfg.FLAG_MAP_NAME_SLOT + 8

        a.label('load_flags')
        a.raw(b'\x60')                                              # pushad
        a.raw(b'\xC7\x05' + le32(flag_count_va) + le32(0))          # flag_count = 0
        if flag_entity_va:
            a.raw(b'\xBF' + le32(flag_entity_va))                   # edi = flag_entity
            a.raw(b'\xB9' + le32(cfg.FLAG_TABLE_MAX * cfg.FLAG_ENTITY_SLOTS_PER_FLAG))
            a.raw(b'\x31\xC0')                                      # eax = 0
            a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
        if flag_present_va:
            a.raw(b'\xBF' + le32(flag_present_va))                  # edi = flag_present
            a.raw(b'\xB9' + le32(cfg.FLAG_TABLE_MAX))               # ecx = flag slots
            a.raw(b'\x31\xC0')                                      # eax = 0
            a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
        if layout.has_field('flag_drop_valid') and layout.has_field('bot_drop_target'):
            # Fresh dropped-flag state per match: no known drops, no per-bot
            # pursuit latch/cooldown/patience/best (bot_drop_target through
            # bot_drop_best are contiguous per-bot arrays — one clear covers
            # all four), node binds and route roots back to -1 so a stale
            # drop_dist row can never be consumed on the new map.
            a.raw(b'\xBF' + le32(layout.va('flag_drop_valid')))     # edi = flag_drop_valid
            a.raw(b'\xB9' + le32(cfg.FLAG_TABLE_MAX))               # ecx = flag slots
            a.raw(b'\x31\xC0')                                      # eax = 0
            a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
            a.raw(b'\xBF' + le32(layout.va('bot_drop_target')))     # edi = bot_drop_target
            a.raw(b'\xB9' + le32(4 * cfg.MAX_BOT_SLOTS))            # ecx = target+cd+try+best
            a.raw(b'\xF3\xAB')                                      # rep stosd (eax still 0)
            a.raw(b'\xBF' + le32(layout.va('flag_drop_node')))      # edi = flag_drop_node
            a.raw(b'\xB9' + le32(cfg.FLAG_TABLE_MAX))               # ecx = flag slots
            a.raw(b'\x83\xC8\xFF')                                  # eax = -1
            a.raw(b'\xF3\xAB')                                      # rep stosd
            a.raw(b'\xBF' + le32(layout.va('drop_route_root')))     # edi = drop_route_root
            a.raw(b'\xB9\x02\x00\x00\x00')                          # ecx = 2 rows
            a.raw(b'\xF3\xAB')                                      # rep stosd (eax still -1)
        a.raw(b'\xA1' + le32(ax.MAP_NAME_CSTRING_VA))               # eax = [map CString header]
        a.raw(b'\x85\xC0'); a.jz('lf_done')
        a.raw(b'\x83\xC0' + bytes([ax.MAP_NAME_ASCII_OFFSET]))      # eax = map name ASCII
        a.raw(b'\x80\x38\x00'); a.jz('lf_done')                     # empty name?
        a.raw(b'\x89\xC5')                                          # ebp = active map name

        a.raw(b'\x8B\x0D' + le32(flag_static_map_count_va))         # ecx = map_count
        a.raw(b'\x85\xC9'); a.jz('lf_done')
        a.raw(b'\x83\xF9' + bytes([cfg.FLAG_STATIC_MAP_MAX]))       # cmp ecx, static max
        a.jbe('lf_map_count_ok')
        a.raw(b'\xB9' + le32(cfg.FLAG_STATIC_MAP_MAX))              # cap corrupt count defensively
        a.label('lf_map_count_ok')
        a.raw(b'\x31\xF6')                                          # esi = map idx

        a.label('lf_map_loop')
        a.raw(b'\x39\xCE'); a.jae('lf_done')                        # if idx >= map_count
        a.raw(b'\x69\xC6' + le32(flag_map_stride))                  # eax = idx * map_stride
        a.raw(b'\x05' + le32(flag_static_maps_va))                  # eax = &map_entry[idx]
        a.raw(b'\x89\xC7')                                          # edi = entry
        a.raw(b'\x89\xEA')                                          # edx = active map name
        a.raw(b'\x89\xFB')                                          # ebx = entry name

        a.label('lf_str_loop')
        a.raw(b'\x8A\x02')                                          # al = [active]
        a.raw(b'\x3A\x03')                                          # cmp al, [entry]
        a.jnz('lf_next_map')
        a.raw(b'\x84\xC0'); a.jz('lf_match')                        # both NUL -> equal
        a.raw(b'\x42\x43')                                          # inc edx; inc ebx
        a.jmp('lf_str_loop')

        a.label('lf_next_map')
        a.raw(b'\x46')                                              # ++idx
        a.jmp('lf_map_loop')

        a.label('lf_match')
        # edi still = &map_entry[idx]. Read capped point count and the first
        # point index; EBX holds first across both rep-movsd (survives ESI/EDI/
        # ECX clobber).
        a.raw(b'\x8B\x4F' + bytes([cfg.FLAG_MAP_NAME_SLOT]))        # ecx = point count
        a.raw(b'\x83\xF9' + bytes([cfg.FLAG_TABLE_MAX]))            # cmp ecx, live cap
        a.jbe('lf_count_ok')
        a.raw(b'\xB9' + le32(cfg.FLAG_TABLE_MAX))                   # cap live count
        a.label('lf_count_ok')
        a.raw(b'\x89\x0D' + le32(flag_count_va))                    # flag_count = ecx (capped)
        a.raw(b'\x85\xC9'); a.jz('lf_done')
        a.raw(b'\x8B\x5F' + bytes([cfg.FLAG_MAP_NAME_SLOT + 4]))    # ebx = first point idx
        # Copy points: src = &flag_static_points[first*8], dst = flag_table, n = count*2 dwords.
        a.raw(b'\x8D\x34\xDD' + le32(flag_static_points_va))        # lea esi, [ebx*8 + static_points]
        a.raw(b'\xBF' + le32(flag_table_va))                       # edi = live flag_table
        a.raw(b'\xD1\xE1')                                          # ecx *= 2 (dwords per point)
        a.raw(b'\xFC\xF3\xA5')                                      # cld; rep movsd
        # Copy team tags: src = &flag_static_team[first*4], dst = flag_team, n = count dwords.
        a.raw(b'\x8D\x34\x9D' + le32(flag_static_team_va))         # lea esi, [ebx*4 + static_team]
        a.raw(b'\xBF' + le32(flag_team_va))                        # edi = live flag_team
        a.raw(b'\x8B\x0D' + le32(flag_count_va))                   # ecx = flag_count (count)
        a.raw(b'\xF3\xA5')                                         # rep movsd
        if flag_present_va:
            # Flags always start at their bases. From here on flag_present[]
            # is EVENT-owned: the checker activate/deactivate apply detours
            # (detours/flag_events.py) flip it in lockstep with the map
            # script's steal/return/capture transitions.
            a.raw(b'\xBF' + le32(flag_present_va))                  # edi = flag_present
            a.raw(b'\x8B\x0D' + le32(flag_count_va))                # ecx = flag_count
            a.raw(b'\xB8\x01\x00\x00\x00')                          # eax = 1
            a.raw(b'\xF3\xAB')                                      # rep stosd

        a.label('lf_done')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')

