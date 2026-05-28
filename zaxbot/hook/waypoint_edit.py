"""``wp_drop`` / ``wp_select`` / ``wp_delete`` — waypoint-editor bodies.

Three no-arg, no-return subroutines invoked from the WM_KEYDOWN dispatcher:

  - ``wp_drop``   (N key): read host char position, snap to existing node
                  within ``wp_snap_radius_sq``, otherwise append a new vertex.
                  Auto-edge from ``wp_selected_idx`` to new/snapped index, set
                  ``wp_selected_idx`` = new/snapped index.
  - ``wp_select`` (J key): set ``wp_selected_idx`` = index of the vertex
                  nearest to the host's world position (any distance).
  - ``wp_delete`` (X key): find nearest vertex, remove it (swap-with-last in
                  ``overlay_vertices``), compact ``overlay_edges`` (drop edges
                  touching the deleted index, remap edges that pointed at the
                  swap-source), and patch ``wp_selected_idx`` accordingly.

All three are gated implicitly by the dispatcher's ``mp_gate`` (no point
editing waypoints outside a match — the host char isn't a valid entity).
A shared helper ``wp_read_host_pos`` writes the host's world position into
``wp_scratch`` and returns EAX = 1 on success / 0 on failure (NULL chain).

Capacity bounds are baked at emit time from ``cfg.OVERLAY_VERTEX_MAX`` and
``cfg.OVERLAY_EDGE_MAX``; the layout table sizes are sized off the same
constants.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


# Worldmgr char-array offset (also used in snapshot.py).
WORLDMGR_CHAR_ARR_OFF = 0x290


def emit(a: Asm, layout: ScratchLayout) -> None:
    overlay_vertices_va     = layout.va('overlay_vertices')
    overlay_edges_va        = layout.va('overlay_edges')
    overlay_vertex_count_va = layout.va('overlay_vertex_count')
    overlay_edge_count_va   = layout.va('overlay_edge_count')
    wp_selected_idx_va      = layout.va('wp_selected_idx')
    wp_scratch_va           = layout.va('wp_scratch')
    wp_snap_radius_sq_va    = layout.va('wp_snap_radius_sq')

    vertex_max = cfg.OVERLAY_VERTEX_MAX
    edge_max   = cfg.OVERLAY_EDGE_MAX

    # =========================================================================
    # wp_read_host_pos: shared helper. Writes host world pos into wp_scratch.
    # Returns EAX = 1 on success, 0 on failure. Clobbers EAX/ECX/EDX (and
    # whatever sub_4FB0A0 internally clobbers — callers wrap in pushad).
    # =========================================================================
    a.label('wp_read_host_pos')
    a.raw(b'\x8B\x0D' + le32(ax.WORLDMGR_GLOBAL))               # mov ecx, [worldmgr]
    a.raw(b'\x85\xC9'); a.jz('wp_rhp_fail')
    a.raw(b'\x8B\x89' + le32(WORLDMGR_CHAR_ARR_OFF))            # mov ecx, [ecx+0x290]
    a.raw(b'\x85\xC9'); a.jz('wp_rhp_fail')
    a.raw(b'\x8B\x09')                                           # mov ecx, [ecx]  (charArray[0])
    a.raw(b'\x85\xC9'); a.jz('wp_rhp_fail')
    a.raw(b'\x68' + le32(wp_scratch_va))                         # push &wp_scratch
    a.call_va(ax.SUB_4FB0A0_VA)                                  # __thiscall, ret 4
    a.raw(b'\xB8\x01\x00\x00\x00')                               # mov eax, 1
    a.raw(b'\xC3')                                               # ret
    a.label('wp_rhp_fail')
    a.raw(b'\x31\xC0\xC3')                                       # xor eax,eax; ret

    # =========================================================================
    # wp_find_nearest: scans overlay_vertices and returns EBX = idx of vertex
    # nearest to wp_scratch (any distance). Pre: wp_scratch valid. Post:
    # EBX = best_idx, or 0xFFFFFFFF if vertex_count == 0. Clobbers EAX/EBX/
    # ECX/ESI/EDI and FPU stack (leaves it balanced).
    # =========================================================================
    a.label('wp_find_nearest')
    a.raw(b'\xBB\xFF\xFF\xFF\xFF')                               # mov ebx, 0xFFFFFFFF
    a.raw(b'\x8B\x3D' + le32(overlay_vertex_count_va))           # mov edi, [vertex_count]
    a.raw(b'\x85\xFF'); a.jz('wp_fn_done')

    # Seed best = distance²(vertices[0]). ESI = 1 thereafter.
    a.raw(b'\xD9\x05' + le32(overlay_vertices_va + 0))           # fld [v0.x]
    a.raw(b'\xD8\x25' + le32(wp_scratch_va + 0))                 # fsub [scratch.x]
    a.raw(b'\xD8\xC8')                                           # fmul st(0), st(0)
    a.raw(b'\xD9\x05' + le32(overlay_vertices_va + 4))           # fld [v0.y]
    a.raw(b'\xD8\x25' + le32(wp_scratch_va + 4))                 # fsub [scratch.y]
    a.raw(b'\xD8\xC8')                                           # fmul st(0), st(0)
    a.raw(b'\xDE\xC1')                                           # faddp st(1), st(0)
    a.raw(b'\x31\xDB')                                           # xor ebx, ebx
    a.raw(b'\xBE\x01\x00\x00\x00')                               # mov esi, 1

    a.label('wp_fn_loop')
    a.raw(b'\x39\xFE'); a.jae('wp_fn_pop_done')                  # if esi >= count, done
    a.raw(b'\xD9\x04\xF5' + le32(overlay_vertices_va + 0))       # fld [v.x] (esi*8)
    a.raw(b'\xD8\x25' + le32(wp_scratch_va + 0))                 # fsub [scratch.x]
    a.raw(b'\xD8\xC8')                                           # fmul st(0), st(0)
    a.raw(b'\xD9\x04\xF5' + le32(overlay_vertices_va + 4))       # fld [v.y]
    a.raw(b'\xD8\x25' + le32(wp_scratch_va + 4))                 # fsub [scratch.y]
    a.raw(b'\xD8\xC8')                                           # fmul st(0), st(0)
    a.raw(b'\xDE\xC1')                                           # faddp st(1), st(0) -> dsq
    # Now ST0 = dsq, ST1 = best_dsq.
    a.raw(b'\xD8\xD1')                                           # fcom st(1)
    a.raw(b'\xDF\xE0')                                           # fnstsw ax
    a.raw(b'\x9E')                                               # sahf
    a.jae('wp_fn_skip')                                          # if dsq >= best, skip
    # New best: pop old best, keep dsq as the new ST0; record idx.
    a.raw(b'\xD9\xC9')                                           # fxch st(1)
    a.raw(b'\xDD\xD8')                                           # fstp st(0)  (pop old best)
    a.raw(b'\x89\xF3')                                           # mov ebx, esi
    a.jmp('wp_fn_next')
    a.label('wp_fn_skip')
    a.raw(b'\xDD\xD8')                                           # fstp st(0)  (pop dsq, keep best)
    a.label('wp_fn_next')
    a.raw(b'\x46')                                               # inc esi
    a.jmp('wp_fn_loop')

    a.label('wp_fn_pop_done')
    a.raw(b'\xDD\xD8')                                           # fstp st(0)  (pop best)
    a.label('wp_fn_done')
    a.raw(b'\xC3')                                               # ret

    # =========================================================================
    # wp_drop (N key)
    # =========================================================================
    a.label('wp_drop')
    a.raw(b'\x60')                                               # pushad
    a.call_lbl('wp_read_host_pos')
    a.raw(b'\x85\xC0'); a.jz('wp_drop_done')

    # SNAP PASS: find any vertex within wp_snap_radius_sq. Reuses wp_find_nearest
    # then compares the *best* squared distance against snap_radius_sq.
    # Easier inline: track snap-candidate index in EBX, seed best with snap_radius_sq.
    a.raw(b'\xBB\xFF\xFF\xFF\xFF')                               # mov ebx, 0xFFFFFFFF
    a.raw(b'\x8B\x3D' + le32(overlay_vertex_count_va))           # mov edi, [vertex_count]
    a.raw(b'\x85\xFF'); a.jz('wp_drop_snap_done')
    a.raw(b'\xD9\x05' + le32(wp_snap_radius_sq_va))              # fld [snap_radius_sq]
    a.raw(b'\x31\xF6')                                           # xor esi, esi

    a.label('wp_drop_snap_loop')
    a.raw(b'\x39\xFE'); a.jae('wp_drop_snap_pop_done')
    a.raw(b'\xD9\x04\xF5' + le32(overlay_vertices_va + 0))       # fld [v.x]
    a.raw(b'\xD8\x25' + le32(wp_scratch_va + 0))                 # fsub [scratch.x]
    a.raw(b'\xD8\xC8')                                           # fmul st,st
    a.raw(b'\xD9\x04\xF5' + le32(overlay_vertices_va + 4))       # fld [v.y]
    a.raw(b'\xD8\x25' + le32(wp_scratch_va + 4))                 # fsub [scratch.y]
    a.raw(b'\xD8\xC8')                                           # fmul st,st
    a.raw(b'\xDE\xC1')                                           # faddp -> dsq
    a.raw(b'\xD8\xD1')                                           # fcom st(1)
    a.raw(b'\xDF\xE0')                                           # fnstsw ax
    a.raw(b'\x9E')                                               # sahf
    a.jae('wp_drop_snap_skip')                                   # dsq >= best -> skip
    a.raw(b'\xD9\xC9')                                           # fxch
    a.raw(b'\xDD\xD8')                                           # fstp st(0)
    a.raw(b'\x89\xF3')                                           # mov ebx, esi
    a.jmp('wp_drop_snap_next')
    a.label('wp_drop_snap_skip')
    a.raw(b'\xDD\xD8')                                           # fstp st(0)
    a.label('wp_drop_snap_next')
    a.raw(b'\x46')                                               # inc esi
    a.jmp('wp_drop_snap_loop')

    a.label('wp_drop_snap_pop_done')
    a.raw(b'\xDD\xD8')                                           # fstp st(0)

    a.label('wp_drop_snap_done')
    # EBX = snapped idx or 0xFFFFFFFF
    a.raw(b'\x83\xFB\xFF'); a.jnz('wp_drop_have_idx')            # cmp ebx, -1

    # CREATE NEW VERTEX: bail if at cap.
    a.raw(b'\xA1' + le32(overlay_vertex_count_va))               # eax = count
    a.raw(b'\x3D' + le32(vertex_max))                            # cmp eax, vertex_max
    a.jae('wp_drop_done')
    # Append vertex at [overlay_vertices + eax*8].
    a.raw(b'\x8D\x0C\xC5' + le32(overlay_vertices_va))           # lea ecx, [eax*8 + verts]
    a.raw(b'\x8B\x15' + le32(wp_scratch_va + 0))                 # mov edx, [scratch.x]
    a.raw(b'\x89\x11')                                           # mov [ecx], edx
    a.raw(b'\x8B\x15' + le32(wp_scratch_va + 4))                 # mov edx, [scratch.y]
    a.raw(b'\x89\x51\x04')                                       # mov [ecx+4], edx
    a.raw(b'\xFF\x05' + le32(overlay_vertex_count_va))           # inc [vertex_count]
    a.raw(b'\x89\xC3')                                           # mov ebx, eax  (new_idx)

    a.label('wp_drop_have_idx')
    # EBX = new/snapped idx. If wp_selected_idx valid and != ebx, append edge.
    a.raw(b'\x8B\x15' + le32(wp_selected_idx_va))                # mov edx, [selected]
    a.raw(b'\x83\xFA\xFF'); a.jz('wp_drop_skip_edge')            # if -1, skip
    # Compare edx == 0xFFFFFFFF — `cmp r32, imm8` sign-extends -1; the jz
    # above caught that path. Now also bail if selected == new_idx.
    a.raw(b'\x39\xDA'); a.jz('wp_drop_skip_edge')                # cmp edx, ebx
    a.raw(b'\xA1' + le32(overlay_edge_count_va))                 # eax = edge_count
    a.raw(b'\x3D' + le32(edge_max))                              # cmp eax, edge_max
    a.jae('wp_drop_skip_edge')
    # Pack (i=selected (low16), j=new (low16)) into edx, write dword.
    # edges[edge_count] = (selected & 0xFFFF) | ((new & 0xFFFF) << 16)
    a.raw(b'\x81\xE2\xFF\xFF\x00\x00')                           # and edx, 0xFFFF
    a.raw(b'\x89\xD9')                                           # mov ecx, ebx
    a.raw(b'\x81\xE1\xFF\xFF\x00\x00')                           # and ecx, 0xFFFF
    a.raw(b'\xC1\xE1\x10')                                       # shl ecx, 16
    a.raw(b'\x09\xCA')                                           # or edx, ecx
    a.raw(b'\x89\x14\x85' + le32(overlay_edges_va))              # mov [eax*4 + edges], edx
    a.raw(b'\xFF\x05' + le32(overlay_edge_count_va))             # inc [edge_count]
    a.label('wp_drop_skip_edge')
    # selected = new_idx
    a.raw(b'\x89\x1D' + le32(wp_selected_idx_va))                # mov [selected], ebx

    a.label('wp_drop_done')
    a.raw(b'\x61')                                               # popad
    a.raw(b'\xC3')                                               # ret

    # =========================================================================
    # wp_select (J key)
    # =========================================================================
    a.label('wp_select')
    a.raw(b'\x60')                                               # pushad
    a.call_lbl('wp_read_host_pos')
    a.raw(b'\x85\xC0'); a.jz('wp_select_done')
    a.call_lbl('wp_find_nearest')                                # ebx = nearest idx or -1
    a.raw(b'\x89\x1D' + le32(wp_selected_idx_va))                # mov [selected], ebx
    a.label('wp_select_done')
    a.raw(b'\x61')                                               # popad
    a.raw(b'\xC3')                                               # ret

    # =========================================================================
    # wp_delete (X key)
    # =========================================================================
    a.label('wp_delete')
    a.raw(b'\x60')                                               # pushad
    a.call_lbl('wp_read_host_pos')
    a.raw(b'\x85\xC0'); a.jz('wp_delete_done')
    a.call_lbl('wp_find_nearest')                                # ebx = nearest idx
    a.raw(b'\x83\xFB\xFF'); a.jz('wp_delete_done')               # if -1, nothing to delete

    # Save deleted_idx (ebx), compute last_idx = vertex_count - 1 in EBP.
    # We use EBP as a stable holder across the edge-loop (callee-clobbered
    # by sub_* would matter if we made calls here — we don't).
    a.raw(b'\xA1' + le32(overlay_vertex_count_va))               # eax = count
    a.raw(b'\x48')                                               # dec eax (last_idx)
    a.raw(b'\x89\xC5')                                           # mov ebp, eax

    # If ebx == ebp, no swap. Else copy vertices[ebp] -> vertices[ebx].
    a.raw(b'\x39\xEB'); a.jz('wp_del_after_swap')                # cmp ebx, ebp
    a.raw(b'\x8D\x0C\xDD' + le32(overlay_vertices_va))           # lea ecx, [ebx*8+verts]
    a.raw(b'\x8D\x34\xED' + le32(overlay_vertices_va))           # lea esi, [ebp*8+verts]
    a.raw(b'\x8B\x06')                                           # mov eax, [esi]
    a.raw(b'\x89\x01')                                           # mov [ecx], eax
    a.raw(b'\x8B\x46\x04')                                       # mov eax, [esi+4]
    a.raw(b'\x89\x41\x04')                                       # mov [ecx+4], eax
    a.label('wp_del_after_swap')
    a.raw(b'\xFF\x0D' + le32(overlay_vertex_count_va))           # dec [vertex_count]

    # COMPACT EDGES: r = ESI, w = EDI, count = ECX.
    # for r in 0..count:
    #   i = edges[r] & 0xFFFF
    #   j = (edges[r] >> 16) & 0xFFFF
    #   if i == ebx or j == ebx: skip
    #   if i == ebp: i = ebx
    #   if j == ebp: j = ebx
    #   edges[w] = (j<<16) | i; w++
    a.raw(b'\x31\xF6')                                           # xor esi, esi
    a.raw(b'\x31\xFF')                                           # xor edi, edi
    a.raw(b'\x8B\x0D' + le32(overlay_edge_count_va))             # mov ecx, [edge_count]
    a.raw(b'\x85\xC9'); a.jz('wp_del_edges_done')

    a.label('wp_del_edge_loop')
    a.raw(b'\x39\xCE'); a.jae('wp_del_edges_finish')             # if esi >= ecx, done
    a.raw(b'\x8B\x04\xB5' + le32(overlay_edges_va))              # mov eax, [edges + esi*4]
    a.raw(b'\x0F\xB7\xD0')                                       # movzx edx, ax     (i)
    a.raw(b'\xC1\xE8\x10')                                       # shr eax, 16       (j)
    a.raw(b'\x0F\xB7\xC0')                                       # movzx eax, ax     (j zero-ext)
    # Drop if i==ebx or j==ebx
    a.raw(b'\x39\xDA'); a.jz('wp_del_edge_skip')                 # cmp edx, ebx
    a.raw(b'\x39\xD8'); a.jz('wp_del_edge_skip')                 # cmp eax, ebx
    # Remap last (ebp) -> deleted slot (ebx)
    a.raw(b'\x39\xEA'); a.jnz('wp_del_no_remap_i')               # cmp edx, ebp
    a.raw(b'\x89\xDA')                                           # mov edx, ebx
    a.label('wp_del_no_remap_i')
    a.raw(b'\x39\xE8'); a.jnz('wp_del_no_remap_j')               # cmp eax, ebp
    a.raw(b'\x89\xD8')                                           # mov eax, ebx
    a.label('wp_del_no_remap_j')
    # Pack and write to edges[w]
    a.raw(b'\xC1\xE0\x10')                                       # shl eax, 16
    a.raw(b'\x09\xC2')                                           # or edx, eax
    a.raw(b'\x89\x14\xBD' + le32(overlay_edges_va))              # mov [edges + edi*4], edx
    a.raw(b'\x47')                                               # inc edi
    a.label('wp_del_edge_skip')
    a.raw(b'\x46')                                               # inc esi
    a.jmp('wp_del_edge_loop')

    a.label('wp_del_edges_finish')
    a.raw(b'\x89\x3D' + le32(overlay_edge_count_va))             # mov [edge_count], edi

    a.label('wp_del_edges_done')
    # Patch wp_selected_idx: if it was the deleted node, clear; if it was
    # the swap-source (last), it now lives at ebx.
    a.raw(b'\xA1' + le32(wp_selected_idx_va))                    # eax = selected
    a.raw(b'\x39\xD8'); a.jnz('wp_del_check_swap')               # cmp eax, ebx
    a.raw(b'\xC7\x05' + le32(wp_selected_idx_va) + b'\xFF\xFF\xFF\xFF')
    a.jmp('wp_delete_done')
    a.label('wp_del_check_swap')
    a.raw(b'\x39\xE8'); a.jnz('wp_delete_done')                  # cmp eax, ebp
    a.raw(b'\x89\x1D' + le32(wp_selected_idx_va))                # mov [selected], ebx

    a.label('wp_delete_done')
    a.raw(b'\x61')                                               # popad
    a.raw(b'\xC3')                                               # ret
