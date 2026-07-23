"""``goody_scan_piles`` / ``goody_scan_items`` — nearest-goody scans
called by the follower (EBX = index or -1; fills goody_tx/ty/node/idx) —
plus ``goody_update_need``, the per-think pickup-need bitmask refresh
the item scan filters on (see cfg.ITEM_NEED_GATE_ENABLED)."""

from ... import addresses as ax
from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout
from . import items


def emit(a: Asm, layout: ScratchLayout) -> None:
    # =====================================================================
    # Goody-pursuit nearest-target scans (called from the follower inside
    # its pushad frame; clobber eax/esi/FPU, return EBX = index or -1 and,
    # on a hit, fill goody_tx/goody_ty/goody_node/goody_idx). Input:
    # goody_scan_rad = radius^2 float bits (FLT_MAX bits = unlimited);
    # goody_scan_cat (items only) = category filter or -1 for any.
    #   goody_scan_piles: over the live pile ring (TTL > 0 slots).
    #   goody_scan_items: over the live filler table, category-filtered.
    # =====================================================================
    goody_on = (
        items.fields_present(layout)
        and layout.has_field('goody_tx')
        and layout.has_field('goody_scan_rad')
        and layout.has_field('sk_pile_valid')
        and layout.has_field('sk_pile_node')
    )
    need_gate = (goody_on
                 and cfg.ITEM_NEED_GATE_ENABLED
                 and layout.has_field('goody_need_mask')
                 and layout.has_field('bot_char_tmp'))

    if not goody_on:
        a.label('goody_scan_piles')
        a.raw(b'\xBB\xFF\xFF\xFF\xFF\xC3')                          # ebx = -1; ret
        a.label('goody_scan_items')
        a.raw(b'\xBB\xFF\xFF\xFF\xFF\xC3')                          # ebx = -1; ret
    else:
        def _emit_goody_scan(name, table_va, node_va, count_imm, count_va,
                             valid_va, cat_va):
            a.label(name)
            a.raw(b'\xBB\xFF\xFF\xFF\xFF')                          # ebx = -1
            a.raw(b'\xD9\x05' + le32(layout.va('goody_scan_rad')))  # fld best = radius
            a.raw(b'\x31\xF6')                                      # esi = 0
            a.label(f'{name}_loop')
            if count_va:
                a.raw(b'\x3B\x35' + le32(count_va))                 # i >= live count?
                a.jae(f'{name}_pop')
            a.raw(b'\x83\xFE' + bytes([count_imm]))                 # i >= cap?
            a.jae(f'{name}_pop')
            if valid_va:
                a.raw(b'\x83\x3C\xB5' + le32(valid_va) + b'\x00')   # slot live?
                a.jz(f'{name}_next')
            if cat_va:
                a.raw(b'\x83\x3D' + le32(layout.va('goody_scan_cat')) + b'\xFF')
                a.jz(f'{name}_cat_ok')                              # -1 = any
                a.raw(b'\x8B\x04\xB5' + le32(cat_va))               # eax = item_cat[i]
                a.raw(b'\x3B\x05' + le32(layout.va('goody_scan_cat')))
                a.jnz(f'{name}_next')
                a.label(f'{name}_cat_ok')
            if cat_va and need_gate:
                # NEED filter: skip categories whose need bit is clear
                # (goody_update_need refreshed the mask this think). A
                # latched category whose need vanished mid-route resolves
                # to -1 here and the follower unlatches cleanly. item_cat
                # values are 0..2 from our own static pack, so the bt
                # index is always in the dword.
                a.raw(b'\x8B\x04\xB5' + le32(cat_va))               # eax = item_cat[i]
                a.raw(b'\x0F\xA3\x05' + le32(layout.va('goody_need_mask')))
                a.jae(f'{name}_next')                               # CF=0 -> not needed
            a.raw(b'\xD9\x04\xF5' + le32(table_va))                 # fld t.x
            a.raw(b'\xD8\x25' + le32(layout.va('bot_pos')))         # fsub bot.x
            a.raw(b'\xD8\xC8')                                      # fmul st,st
            a.raw(b'\xD9\x04\xF5' + le32(table_va + 4))             # fld t.y
            a.raw(b'\xD8\x25' + le32(layout.va('bot_pos') + 4))     # fsub bot.y
            a.raw(b'\xD8\xC8')                                      # fmul st,st
            a.raw(b'\xDE\xC1')                                      # faddp -> dsq, best
            a.raw(b'\xD8\xD1')                                      # fcom st(1) (dsq:best)
            a.raw(b'\xDF\xE0'); a.raw(b'\x9E')                      # fnstsw ax; sahf
            a.jae(f'{name}_skip')                                   # dsq >= best
            a.raw(b'\xD9\xC9')                                      # fxch
            a.raw(b'\xDD\xD8')                                      # fstp st0 (pop old best)
            a.raw(b'\x89\xF3')                                      # ebx = i
            a.jmp(f'{name}_next')
            a.label(f'{name}_skip')
            a.raw(b'\xDD\xD8')                                      # fstp st0 (pop dsq)
            a.label(f'{name}_next')
            a.raw(b'\x46')                                          # ++i
            a.jmp(f'{name}_loop')
            a.label(f'{name}_pop')
            a.raw(b'\xDD\xD8')                                      # fstp st0 (FPU empty)
            a.raw(b'\x83\xFB\xFF')                                  # found?
            a.jz(f'{name}_ret')
            a.raw(b'\x8B\x04\xDD' + le32(table_va))                 # eax = t.x bits
            a.raw(b'\xA3' + le32(layout.va('goody_tx')))
            a.raw(b'\x8B\x04\xDD' + le32(table_va + 4))             # eax = t.y bits
            a.raw(b'\xA3' + le32(layout.va('goody_ty')))
            a.raw(b'\x8B\x04\x9D' + le32(node_va))                  # eax = node[i]
            a.raw(b'\xA3' + le32(layout.va('goody_node')))
            a.raw(b'\x89\x1D' + le32(layout.va('goody_idx')))       # goody_idx = i
            a.label(f'{name}_ret')
            a.raw(b'\xC3')

        _emit_goody_scan('goody_scan_piles',
                         layout.va('sk_pile_pos'), layout.va('sk_pile_node'),
                         cfg.SK_PILE_TABLE_MAX, 0,
                         layout.va('sk_pile_valid'), 0)
        _emit_goody_scan('goody_scan_items',
                         layout.va('item_table'), layout.va('item_node'),
                         cfg.ITEM_TABLE_MAX, layout.va('item_count'),
                         0, layout.va('item_cat'))

    if need_gate:
        # =================================================================
        # goody_update_need: refresh goody_need_mask from the bot's LIVE
        # state (reads bot_char_tmp; called by the follower once per goody
        # think, inside its pushad frame). Bits: 0 = health (cur_damage at
        # char+0x7C != 0 — the float is never negative, so bits==0 iff
        # full), 1 = energy, 2 = shield — the latter two via the ENGINE'S
        # OWN pickup-useful predicates (vtable slot 32 of the charge-pickup
        # classes, __stdcall(char) ret 4, AL=1 iff the pickup would help):
        # no carried battery/shield -> no need (a blob would do nothing —
        # the user-requested "no shield -> ignore shield blobs"), full
        # charge -> no need. Clobbers EAX/ECX/EDX; the engine predicates
        # preserve callee-saved regs (verified in disasm).
        # =================================================================
        goody_need_mask_va = layout.va('goody_need_mask')
        bot_char_tmp_va    = layout.va('bot_char_tmp')
        a.label('goody_update_need')
        a.raw(b'\x31\xD2')                                          # edx = mask = 0
        a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))                  # ecx = bot char
        a.raw(b'\x85\xC9'); a.jz('gun_store')                       # NULL char -> 0
        a.raw(b'\x8B\x41' + bytes([ax.CHAR_CUR_DAMAGE_OFF]))        # eax = cur_damage bits
        a.raw(b'\x85\xC0'); a.jz('gun_no_health')                   # 0.0 -> full health
        a.raw(b'\x83\xCA\x01')                                      # mask |= 1 (health)
        a.label('gun_no_health')
        a.raw(b'\x52')                                              # push mask
        a.raw(b'\xFF\x35' + le32(bot_char_tmp_va))                  # push char
        a.call_va(ax.SUB_BATTERY_NEED_VA)                           # al = energy useful?
        a.raw(b'\x5A')                                              # pop edx (mask)
        a.raw(b'\x84\xC0'); a.jz('gun_no_energy')
        a.raw(b'\x83\xCA\x02')                                      # mask |= 2 (energy)
        a.label('gun_no_energy')
        a.raw(b'\x52')                                              # push mask
        a.raw(b'\xFF\x35' + le32(bot_char_tmp_va))                  # push char
        a.call_va(ax.SUB_SHIELD_NEED_VA)                            # al = shield useful?
        a.raw(b'\x5A')                                              # pop edx (mask)
        a.raw(b'\x84\xC0'); a.jz('gun_weapon')
        a.raw(b'\x83\xCA\x04')                                      # mask |= 4 (shield)
        a.label('gun_weapon')
        # Bit 3 = "needs a weapon": the bot carries fewer than
        # cfg.WEAPON_NEED_MIN_OWNED Primary-group items (spawn loadout is 1
        # starter pistol, so fresh bots hunt guns; an armed bot's bit goes
        # clear and the weapon category stops latching). No engine
        # pickup-useful predicate exists for whole weapons, so the count IS
        # the need test. Iterates the engine's own group list
        # (sub_425350); the Primary group key is lazily resolved into the
        # shared primary_hash slot exactly like spawn.py's force-weapon
        # path. ebx/esi are callee-saved for our caller — preserved.
        weapon_need = (cfg.ITEM_CATEGORIES >= 4
                       and layout.has_field('primary_hash'))
        if weapon_need:
            primary_hash_va = layout.va('primary_hash')
            a.raw(b'\x52')                                          # push mask
            a.raw(b'\x53\x56')                                      # push ebx; push esi
            a.raw(b'\x83\x3D' + le32(primary_hash_va) + b'\x00')    # hash resolved?
            a.jnz('gun_w_hash_ok')
            a.raw(b'\x6A\xFF')                                      # push -1
            a.raw(b'\x68' + le32(ax.PRIMARY_STR_VA))                # push "Primary"
            a.raw(b'\xB9' + le32(ax.SLOT_NAME_REGISTRY_VA))         # ecx = registry
            a.call_va(ax.SUB_523DF0_VA)                             # eax = group key
            a.raw(b'\xA3' + le32(primary_hash_va))
            a.label('gun_w_hash_ok')
            a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))              # ecx = char
            a.call_va(ax.SUB_4267E0_VA)                             # eax = inventory
            a.raw(b'\x8B\xD8')                                      # ebx = inv
            a.raw(b'\x85\xDB'); a.jz('gun_w_no')                    # no inv -> no bit
            a.raw(b'\x31\xF6')                                      # esi = count
            a.raw(b'\x83\xC8\xFF')                                  # eax = -1 (prev id)
            a.label('gun_w_loop')
            a.raw(b'\xFF\x35' + le32(primary_hash_va))              # push group key
            a.raw(b'\x50')                                          # push prev id
            a.raw(b'\x8B\xCB')                                      # ecx = inv
            a.call_va(ax.SUB_425350_VA)                             # eax = next id / -1
            a.raw(b'\x83\xF8\xFF'); a.jz('gun_w_yes')               # exhausted below MIN
            a.raw(b'\x46')                                          # ++count
            a.raw(b'\x83\xFE' + bytes([max(1, cfg.WEAPON_NEED_MIN_OWNED)]))
            a.jb('gun_w_loop')                                      # still hungry
            a.label('gun_w_no')                                     # satisfied
            a.raw(b'\x5E\x5B\x5A')                                  # pop esi/ebx/edx
            a.jmp('gun_store')
            a.label('gun_w_yes')
            a.raw(b'\x5E\x5B\x5A')                                  # pop esi/ebx/edx
            a.raw(b'\x83\xCA\x08')                                  # mask |= 8 (weapon)
        a.label('gun_store')
        a.raw(b'\x89\x15' + le32(goody_need_mask_va))               # store mask
        a.raw(b'\xC3')
