"""Lava (plasma tile map) capture and queries: ``scan_plasma``,
``plasma_tile_xy``, ``plasma_get``, ``is_plasma_at``,
``plasma_census`` and ``plasma_dump_heat``."""

from ... import addresses as ax
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    # =====================================================================
    # scan_plasma: capture the active map's CPlasmaTileMap* once per match
    # (called from detour_df90, like scan_hazards). Walk the world manager's
    # layer array (mgr+0x2BC[0] == active CLayer) and read the plasma-map
    # pointer. The live-layer field offset is ambiguous (+0x7C vs +0x40), so
    # we try BOTH and VALIDATE each candidate by its vtable: a real
    # CPlasmaTileMap's first dword == off_5FCD98. The validated pointer goes to
    # the plasma_map global (0 on non-plasma maps => is_plasma_at no-ops).
    # The plasma_diag block records LAY, both raw candidates, the chosen ptr,
    # and tilepx/tw/th so the R-snapshot can confirm the pin. pushad/popad,
    # no args/ret (safe to call unconditionally even with a NULL world mgr).
    # =====================================================================
    plasma_map_va   = layout.va('plasma_map')
    plasma_diag_va  = layout.va('plasma_diag')
    plasma_qx_va    = layout.va('plasma_qx')
    plasma_qy_va    = layout.va('plasma_qy')
    plasma_tx_va    = layout.va('plasma_tx')
    plasma_ty_va    = layout.va('plasma_ty')
    plasma_grid_va     = layout.va('plasma_grid')
    plasma_cn_count_va = layout.va('plasma_cn_count')
    plasma_cn_max_va   = layout.va('plasma_cn_max')
    plasma_cn_first_va = layout.va('plasma_cn_first')
    lava_heat_threshold_va = layout.va('lava_heat_threshold')
    lava_dbg_heat_va   = layout.va('lava_dbg_heat')

    a.label('scan_plasma')
    a.raw(b'\x60')                                              # pushad
    a.raw(b'\xC7\x05' + le32(plasma_map_va) + le32(0))          # plasma_map = 0
    a.raw(b'\xBF' + le32(plasma_diag_va))                       # edi = &plasma_diag
    a.raw(b'\xB9\x14\x00\x00\x00')                              # ecx = 20 dwords (0x50)
    a.raw(b'\x31\xC0')                                          # eax = 0
    a.raw(b'\xFC\xF3\xAB')                                      # cld; rep stosd (zero diag)

    a.raw(b'\xA1' + le32(ax.WORLDMGR_GLOBAL))                   # eax = worldmgr
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('scan_plasma_done')    # cmp eax,0x400000
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('scan_plasma_done')   # cmp eax,0x70000000
    a.raw(b'\x8B\x80' + le32(ax.WORLDMGR_ENT_LIST_OFF))         # eax = [eax+0x2BC] layer_arr
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('scan_plasma_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('scan_plasma_done')
    a.raw(b'\x8B\x00')                                          # eax = [layer_arr] (LAY = layer[0])
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('scan_plasma_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('scan_plasma_done')
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x00))                # diag[0] = LAY
    a.raw(b'\x89\xC6')                                          # esi = LAY

    # Record both raw candidates for the pin, then validate.
    a.raw(b'\x8B\x86' + le32(ax.LAYER_PLASMA_MAP_OFF_A))        # eax = [esi+0x7C]
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x04))                # diag[1] = cand7C
    a.raw(b'\x8B\x86' + le32(ax.LAYER_PLASMA_MAP_OFF_B))        # eax = [esi+0x40]
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x08))                # diag[2] = cand40

    # Candidate A (+0x7C): range-check, then vtable == off_5FCD98.
    a.raw(b'\x8B\x86' + le32(ax.LAYER_PLASMA_MAP_OFF_A))        # eax = [esi+0x7C]
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_try_b')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('scan_plasma_try_b')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('scan_plasma_try_b')
    a.raw(b'\x8B\x10')                                          # edx = [eax] (vtable)
    a.raw(b'\x81\xFA' + le32(ax.CPLASMA_TILEMAP_VTBL_VA))       # cmp edx, off_5FCD98
    a.jz('scan_plasma_store')                                   # match -> eax is the plasma map

    a.label('scan_plasma_try_b')
    a.raw(b'\x8B\x86' + le32(ax.LAYER_PLASMA_MAP_OFF_B))        # eax = [esi+0x40]
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_none')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('scan_plasma_none')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('scan_plasma_none')
    a.raw(b'\x8B\x10')                                          # edx = [eax]
    a.raw(b'\x81\xFA' + le32(ax.CPLASMA_TILEMAP_VTBL_VA))
    a.jz('scan_plasma_store')

    a.label('scan_plasma_none')
    a.raw(b'\x31\xC0')                                          # eax = 0 (no plasma map)

    a.label('scan_plasma_store')
    a.raw(b'\xA3' + le32(plasma_map_va))                        # plasma_map = eax
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x0C))                # diag[3] = chosen
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_done')
    a.raw(b'\x8B\x90' + le32(ax.CPLASMA_TILEPX_W_OFF))          # edx = [eax+0x2D04] tilepx
    a.raw(b'\x89\x15' + le32(plasma_diag_va + 0x10))            # diag[4] = tilepx
    a.raw(b'\x8B\x90' + le32(ax.CPLASMA_TILECNT_W_OFF))         # edx = [eax+0x2D0C] tw
    a.raw(b'\x89\x15' + le32(plasma_diag_va + 0x14))            # diag[5] = tw
    a.raw(b'\x8B\x90' + le32(ax.CPLASMA_TILECNT_H_OFF))         # edx = [eax+0x2D10] th
    a.raw(b'\x89\x15' + le32(plasma_diag_va + 0x18))            # diag[6] = th
    a.label('scan_plasma_done')
    a.raw(b'\x61')                                              # popad
    a.raw(b'\xC3')                                              # ret

    # =====================================================================
    # plasma_tile_xy: convert world (plasma_qx, plasma_qy) -> tile
    # (plasma_tx, plasma_ty) using the active map's tilepx (read at runtime,
    # not hardcoded). Signed idiv matches the engine's sub_540640. NULL/zero
    # tilepx -> tiles 0. Clobbers EAX, ECX, EDX.
    # =====================================================================
    a.label('plasma_tile_xy')
    a.raw(b'\xA1' + le32(plasma_map_va))                        # eax = plasma_map
    a.raw(b'\x85\xC0'); a.jz('ptxy_zero')
    a.raw(b'\x8B\x88' + le32(ax.CPLASMA_TILEPX_W_OFF))          # ecx = [eax+0x2D04] tilepx
    a.raw(b'\x85\xC9'); a.jz('ptxy_zero')                       # div-by-zero guard
    a.raw(b'\xA1' + le32(plasma_qx_va))                         # eax = qx
    a.raw(b'\x99'); a.raw(b'\xF7\xF9')                          # cdq; idiv ecx -> eax = qx/tilepx
    a.raw(b'\xA3' + le32(plasma_tx_va))                         # plasma_tx = tile x
    a.raw(b'\xA1' + le32(plasma_qy_va))                         # eax = qy
    a.raw(b'\x99'); a.raw(b'\xF7\xF9')                          # cdq; idiv ecx -> eax = qy/tilepx
    a.raw(b'\xA3' + le32(plasma_ty_va))                         # plasma_ty = tile y
    a.raw(b'\xC3')
    a.label('ptxy_zero')
    a.raw(b'\x31\xC0')                                          # eax = 0
    a.raw(b'\xA3' + le32(plasma_tx_va))
    a.raw(b'\xA3' + le32(plasma_ty_va))
    a.raw(b'\xC3')

    # =====================================================================
    # plasma_get: read one cell of the grid in [plasma_grid] at tile
    # (plasma_tx, plasma_ty). The grid element getter is __thiscall(grid, x, y)
    # at vtable+0xD8, callee-clean (ret 8), bounds-checked (out-of-range -> 0).
    # Works for either embedded grid (footprint @plasma+0x08 or heat
    # @plasma+0x2C6C). Returns EAX = cell value (0 if NULL grid / out of range).
    # Clobbers EAX, ECX; the engine getter preserves EBX/ESI/EDI/EBP.
    # =====================================================================
    a.label('plasma_get')
    a.raw(b'\x8B\x0D' + le32(plasma_grid_va))                   # ecx = plasma_grid
    a.raw(b'\x85\xC9'); a.jz('pget_zero')
    a.raw(b'\xFF\x35' + le32(plasma_ty_va))                     # push ty (arg2 = y)
    a.raw(b'\xFF\x35' + le32(plasma_tx_va))                     # push tx (arg1 = x)
    a.raw(b'\x8B\x01')                                          # eax = [ecx] (grid vtable)
    a.raw(b'\xFF\x90' + le32(ax.CPLASMA_GRID_GETTER_VOFF))      # call [eax+0xD8] (ret 8)
    a.raw(b'\xC3')
    a.label('pget_zero')
    a.raw(b'\x31\xC0')                                          # eax = 0
    a.raw(b'\xC3')

    # =====================================================================
    # is_plasma_at: "is world point (plasma_qx, plasma_qy) damaging lava?"
    # Queries the HEAT/elevation grid (plasma+0x2C6C) and returns EAX = 1 when
    # heat >= lava_heat_threshold, else 0. R-snapshot census on Molten Ice
    # established heat >= 128 = molten pool (host burned at 221) vs <=127 ambient
    # walkable floor; the footprint grid (plasma+0x08) is NOT the damage layer
    # (only 10 sparse source cells). Clobbers EAX, ECX, EDX.
    # =====================================================================
    a.label('is_plasma_at')
    a.raw(b'\xA1' + le32(plasma_map_va))                        # eax = plasma_map
    a.raw(b'\x85\xC0'); a.jz('ipa_no')
    a.call_lbl('plasma_tile_xy')                                # plasma_tx/ty = tile(qx,qy)
    a.raw(b'\xA1' + le32(plasma_map_va))                        # eax = plasma_map
    a.raw(b'\x05' + le32(ax.CPLASMA_HEAT_OFF))                  # add eax, 0x2C6C (heat grid)
    a.raw(b'\xA3' + le32(plasma_grid_va))                       # plasma_grid = heat grid
    # WARM-THEN-READ. The engine's storage grid loads a tile's ROW lazily on
    # first access, so the FIRST plasma_get returns a stale value and the SECOND
    # returns the live heat (proven by R-snapshot: 1st heat read < threshold,
    # 2nd read of the SAME tile = 255). Call twice, use the second; the extra
    # read is cheap and makes the query reliable per-frame.
    a.call_lbl('plasma_get')                                    # WARM read (row load) — discard
    a.call_lbl('plasma_get')                                    # REAL read -> eax = live heat (0..255)
    a.raw(b'\xA3' + le32(lava_dbg_heat_va))                     # debug: record the heat seen
    a.raw(b'\x3B\x05' + le32(lava_heat_threshold_va))           # cmp eax, threshold
    a.jb('ipa_no')                                              # heat < threshold -> safe
    a.raw(b'\xB8\x01\x00\x00\x00')                              # eax = 1 (lava)
    a.raw(b'\xC3')
    a.label('ipa_no')
    a.raw(b'\x31\xC0')                                          # eax = 0 (safe)
    a.raw(b'\xC3')

    # =====================================================================
    # plasma_census: scan EVERY tile of the grid in [plasma_grid] and count
    # nonzero cells, tracking the max value and the first nonzero tile. Outputs
    # plasma_cn_count / plasma_cn_max / plasma_cn_first (tx<<16 | ty, or
    # 0xFFFFFFFF if none). Whole-grid coverage is robust to the fire animation
    # and to the host's exact tile, so it definitively shows which embedded grid
    # marks the lava region. pushad/popad; ESI/EDI/EBX survive plasma_get
    # (the engine getter is callee-clean for them).
    # =====================================================================
    a.label('plasma_census')
    a.raw(b'\x60')                                              # pushad
    a.raw(b'\xC7\x05' + le32(plasma_cn_count_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(plasma_cn_max_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(plasma_cn_first_va) + le32(0xFFFFFFFF))
    a.raw(b'\x8B\x35' + le32(plasma_map_va))                    # esi = plasma_map
    a.raw(b'\x85\xF6'); a.jz('census_done')
    a.raw(b'\x31\xDB')                                          # ebx = ty = 0
    a.label('census_ty')
    a.raw(b'\x8B\x86' + le32(ax.CPLASMA_TILECNT_H_OFF))         # eax = th
    a.raw(b'\x39\xC3'); a.jae('census_done')                   # ty >= th -> done (unsigned)
    a.raw(b'\x31\xFF')                                          # edi = tx = 0
    a.label('census_tx')
    a.raw(b'\x8B\x86' + le32(ax.CPLASMA_TILECNT_W_OFF))         # eax = tw
    a.raw(b'\x39\xC7'); a.jae('census_tx_done')                # tx >= tw -> row done
    a.raw(b'\x89\x3D' + le32(plasma_tx_va))                     # plasma_tx = tx
    a.raw(b'\x89\x1D' + le32(plasma_ty_va))                     # plasma_ty = ty
    a.call_lbl('plasma_get')                                    # eax = cell value
    a.raw(b'\x85\xC0'); a.jz('census_next')
    a.raw(b'\xFF\x05' + le32(plasma_cn_count_va))               # ++count
    a.raw(b'\x3B\x05' + le32(plasma_cn_max_va))                 # cmp eax, max
    a.jbe('census_skipmax')
    a.raw(b'\xA3' + le32(plasma_cn_max_va))                     # max = eax
    a.label('census_skipmax')
    a.raw(b'\x81\x3D' + le32(plasma_cn_first_va) + b'\xFF\xFF\xFF\xFF')  # cmp first, -1
    a.jnz('census_next')
    a.raw(b'\x89\xF8')                                          # eax = tx
    a.raw(b'\xC1\xE0\x10')                                      # shl eax, 16
    a.raw(b'\x09\xD8')                                          # or  eax, ebx (ty)
    a.raw(b'\xA3' + le32(plasma_cn_first_va))                   # first = tx<<16 | ty
    a.label('census_next')
    a.raw(b'\x47')                                              # inc edi (tx)
    a.jmp('census_tx')
    a.label('census_tx_done')
    a.raw(b'\x43')                                              # inc ebx (ty)
    a.jmp('census_ty')
    a.label('census_done')
    a.raw(b'\x61')                                              # popad
    a.raw(b'\xC3')

    # =====================================================================
    # plasma_dump_heat: write the HEAT grid (plasma+0x2C6C) for every tile into
    # plasma_heatmap as row-major bytes (tw wide, th tall), bounded to the 0x800
    # field. Lets the R-snapshot render the whole lava layout + value
    # distribution so the damage threshold can be read off directly. ESI=plasma,
    # EDI=output cursor, EBX=ty, EBP=tx all survive plasma_get (callee-clean).
    # pushad/popad.
    # =====================================================================
    plasma_heatmap_va = layout.va('plasma_heatmap')
    a.label('plasma_dump_heat')
    a.raw(b'\x60')                                              # pushad
    a.raw(b'\xBF' + le32(plasma_heatmap_va))                    # edi = &heatmap
    a.raw(b'\xB9\x00\x08\x00\x00')                              # ecx = 0x800
    a.raw(b'\x31\xC0')                                          # eax = 0
    a.raw(b'\xFC\xF3\xAA')                                      # cld; rep stosb (zero map)
    a.raw(b'\x8B\x35' + le32(plasma_map_va))                    # esi = plasma_map
    a.raw(b'\x85\xF6'); a.jz('pdh_done')
    a.raw(b'\x89\xF0')                                          # eax = esi
    a.raw(b'\x05' + le32(ax.CPLASMA_HEAT_OFF))                  # add eax, 0x2C6C (heat grid)
    a.raw(b'\xA3' + le32(plasma_grid_va))
    a.raw(b'\xBF' + le32(plasma_heatmap_va))                    # edi = output cursor
    a.raw(b'\x31\xDB')                                          # ebx = ty = 0
    a.label('pdh_ty')
    a.raw(b'\x8B\x86' + le32(ax.CPLASMA_TILECNT_H_OFF))         # eax = th
    a.raw(b'\x39\xC3'); a.jae('pdh_done')                       # ty >= th
    a.raw(b'\x31\xED')                                          # ebp = tx = 0
    a.label('pdh_tx')
    a.raw(b'\x81\xFF' + le32(plasma_heatmap_va + 0x800))        # cmp edi, heatmap_end
    a.jae('pdh_done')                                           # bound: don't overflow the field
    a.raw(b'\x8B\x86' + le32(ax.CPLASMA_TILECNT_W_OFF))         # eax = tw
    a.raw(b'\x39\xC5'); a.jae('pdh_tx_done')                    # tx >= tw -> row done
    a.raw(b'\x89\x2D' + le32(plasma_tx_va))                     # plasma_tx = tx (ebp)
    a.raw(b'\x89\x1D' + le32(plasma_ty_va))                     # plasma_ty = ty (ebx)
    a.call_lbl('plasma_get')                                    # eax = heat byte
    a.raw(b'\x88\x07')                                          # [edi] = al
    a.raw(b'\x47')                                              # inc edi
    a.raw(b'\x45')                                              # inc ebp (tx)
    a.jmp('pdh_tx')
    a.label('pdh_tx_done')
    a.raw(b'\x43')                                              # inc ebx (ty)
    a.jmp('pdh_ty')
    a.label('pdh_done')
    a.raw(b'\x61')                                              # popad
    a.raw(b'\xC3')                                              # ret
