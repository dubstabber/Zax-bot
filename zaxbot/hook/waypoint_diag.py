"""``wp_compute`` — waypoint-graph probe body for the R-key snapshot.

Populates the ``wp_diag_data`` scratch buffer (8 contiguous u32 fields):

  [+0x00] MGR ptr        (dword_713F14)
  [+0x04] WM ptr         (dword_6C2080; aliased with MGR at runtime)
  [+0x08] LV ptr         (mgr.vtbl[0x184]() result — NOT a CLayer)
  [+0x0C] WPM ptr        ([LV + 0x134]; read into a CString on this type,
                          confirmed bogus in the May 27 R dump)
  [+0x10] char count     ([WM + 0x294])
  [+0x14] layer_arr ptr  ([WM + 0x2BC]; sub_4F1050 confirms this is an
                          array indexed in [0, [WM+0x2C0]) — observed
                          count=1 in MP, so element 0 is the active layer)
  [+0x18] LAY ptr        ([layer_arr + 0]; the active CLayer that
                          sub_4ECA80 stores the CWayPointMap on)
  [+0x1C] WPM_REAL ptr   ([LAY + 0x134]; the actual CWayPointMap if the
                          hypothesis is right)

Called from ``do_snapshot`` via ``call_lbl('wp_compute')`` inside the R-key
pushad bracket. No args, no return; clobbers all GP regs (caller saved).
All reads pass null checks; missing data shows as zeros.
"""

from .. import addresses as ax
from ..asm import Asm, le32
from ..layout import ScratchLayout


# Worldmgr char-array count offset. Documented in world-entity-array memory:
# chars at +0x290, count at +0x294.
WORLDMGR_CHAR_COUNT_OFF = 0x294


def emit(a: Asm, layout: ScratchLayout) -> None:
    diag_va = layout.va('wp_diag_data')

    a.label('wp_compute')

    # Zero the whole 32-byte block so any "skip" path leaves clean zeros.
    a.raw(b'\xC7\x05' + le32(diag_va + 0x00) + le32(0))
    a.raw(b'\xC7\x05' + le32(diag_va + 0x04) + le32(0))
    a.raw(b'\xC7\x05' + le32(diag_va + 0x08) + le32(0))
    a.raw(b'\xC7\x05' + le32(diag_va + 0x0C) + le32(0))
    a.raw(b'\xC7\x05' + le32(diag_va + 0x10) + le32(0))
    a.raw(b'\xC7\x05' + le32(diag_va + 0x14) + le32(0))
    a.raw(b'\xC7\x05' + le32(diag_va + 0x18) + le32(0))
    a.raw(b'\xC7\x05' + le32(diag_va + 0x1C) + le32(0))

    # --- Slot 0: MGR raw value (dword_713F14) ---
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))             # eax = mgr
    a.raw(b'\xA3' + le32(diag_va + 0x00))
    a.raw(b'\x85\xC0'); a.jz('wp_after_mgr')

    # --- Slot 2/3: LV via mgr.vtbl[0x184]; WPM = [LV + 0x134] ---
    a.raw(b'\x89\xC1')                                      # mov ecx, eax (this)
    a.raw(b'\x8B\x10')                                      # mov edx, [eax] (vtable)
    a.raw(b'\xFF\x92' + le32(ax.VT_OFFSET_TO_LVL))          # call [edx + 0x184]
    a.raw(b'\xA3' + le32(diag_va + 0x08))                   # diag[2] = lv
    a.raw(b'\x85\xC0'); a.jz('wp_after_mgr')
    a.raw(b'\x8B\x88' + le32(ax.LEVEL_WAYPOINT_MAP_OFF))    # mov ecx, [eax + 0x134]
    a.raw(b'\x89\x0D' + le32(diag_va + 0x0C))               # diag[3] = wpm

    a.label('wp_after_mgr')

    # --- Slot 1: WM raw value (dword_6C2080). ESI = wm for re-use.
    a.raw(b'\x8B\x35' + le32(ax.WORLDMGR_GLOBAL))           # mov esi, [worldmgr_global]
    a.raw(b'\x89\x35' + le32(diag_va + 0x04))
    a.raw(b'\x85\xF6'); a.jz('wp_done')

    # --- Slot 4: char count ([WM + 0x294]) ---
    a.raw(b'\x8B\x86' + le32(WORLDMGR_CHAR_COUNT_OFF))      # mov eax, [esi + 0x294]
    a.raw(b'\xA3' + le32(diag_va + 0x10))

    # --- Slot 5: layer_arr = [WM + 0x2BC] ---
    a.raw(b'\x8B\x86' + le32(ax.WORLDMGR_ENT_LIST_OFF))     # mov eax, [esi + 0x2BC]
    a.raw(b'\xA3' + le32(diag_va + 0x14))
    a.raw(b'\x85\xC0'); a.jz('wp_done')
    # Heap range check before deref.
    a.raw(b'\x3D\x00\x00\x40\x00')                          # cmp eax, 0x00400000
    a.jb('wp_done')
    a.raw(b'\x3D\x00\x00\x00\x70')                          # cmp eax, 0x70000000
    a.jae('wp_done')

    # --- Slot 6: LAY = [layer_arr + 0] (first/active layer) ---
    a.raw(b'\x8B\x00')                                      # mov eax, [eax]
    a.raw(b'\xA3' + le32(diag_va + 0x18))
    a.raw(b'\x85\xC0'); a.jz('wp_done')
    a.raw(b'\x3D\x00\x00\x40\x00')                          # cmp eax, 0x00400000
    a.jb('wp_done')
    a.raw(b'\x3D\x00\x00\x00\x70')                          # cmp eax, 0x70000000
    a.jae('wp_done')

    # --- Slot 7: WPM_REAL = [LAY + 0x134] ---
    a.raw(b'\x8B\x80' + le32(ax.LEVEL_WAYPOINT_MAP_OFF))    # mov eax, [eax + 0x134]
    a.raw(b'\xA3' + le32(diag_va + 0x1C))

    a.label('wp_done')
    a.raw(b'\xC3')                                          # ret
