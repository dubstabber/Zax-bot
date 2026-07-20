"""``goody_scan_piles`` / ``goody_scan_items`` — nearest-goody scans
called by the follower (EBX = index or -1; fills goody_tx/ty/node/idx)."""

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
