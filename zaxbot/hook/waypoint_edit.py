"""``wp_*`` waypoint-editor + persistence bodies.

Subroutines invoked from the WM_KEYDOWN dispatcher and the bot-movement /
match-change detours:

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
  - ``wp_save``   (',' key): persist the graph to ``waypoints/<map>.zwpt``.
  - ``wp_load``   (match change): reload the saved graph for the current map.

Shared helpers used by both the editor and the bot-movement follower:

  - ``wp_read_host_pos``: writes the host's world position into ``wp_scratch``
    and returns EAX = 1 on success / 0 on failure (NULL chain).
  - ``wp_find_nearest``: EBX = index of the vertex nearest ``wp_scratch``.
  - ``wp_advance``: pick the next vertex to walk toward along a real edge.

The editor entry points (drop/select/delete/save) are gated implicitly by the
dispatcher's ``mp_gate`` (no point editing waypoints outside a match — the host
char isn't a valid entity).

Capacity bounds are baked at emit time from ``cfg.OVERLAY_VERTEX_MAX`` and
``cfg.OVERLAY_EDGE_MAX``; the layout table sizes are sized off the same
constants.

## Module structure

``emit`` assembles the subroutines back-to-back into one region. The source is
split into one ``_emit_*`` function per subroutine; each appends to the same
``Asm`` cursor in section order and the cross-subroutine calls resolve through
the two-pass linker regardless of which function emitted a given label.
Splitting keeps the emitted bytes identical to the old flat function (pinned by
the golden-section test) while letting each subroutine be read in isolation.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


# Worldmgr char-array offset (also used in snapshot.py).
WORLDMGR_CHAR_ARR_OFF = 0x290

# File magic 'ZWPT' as a u32 LE: bytes 'Z'(0x5A), 'W'(0x57), 'P'(0x50),
# 'T'(0x54) at offsets 0..3 → u32 = 0x5450575A.
ZWPT_MAGIC = 0x5450575A
ZWPT_VERSION = 1


def emit(a: Asm, layout: ScratchLayout) -> None:
    """Assemble the waypoint subroutines in section order. Order is load-bearing
    (it fixes label positions); do not reorder without re-establishing the
    byte-identity baseline."""
    _emit_read_host_pos(a, layout)
    _emit_find_nearest(a, layout)
    _emit_find_nearest_ex(a, layout)
    _emit_advance(a, layout)
    _emit_drop(a, layout)
    _emit_select(a, layout)
    _emit_delete(a, layout)
    _emit_build_filename(a, layout)
    _emit_save(a, layout)
    _emit_load(a, layout)


def _emit_read_host_pos(a: Asm, layout: ScratchLayout) -> None:
    """wp_read_host_pos: shared helper. Writes host world pos into wp_scratch.
    Returns EAX = 1 on success, 0 on failure. Clobbers EAX/ECX/EDX (and
    whatever sub_4FB0A0 internally clobbers — callers wrap in pushad)."""
    wp_scratch_va = layout.va('wp_scratch')

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


def _emit_find_nearest(a: Asm, layout: ScratchLayout) -> None:
    """wp_find_nearest: scans overlay_vertices and returns EBX = idx of vertex
    nearest to wp_scratch (any distance). Pre: wp_scratch valid. Post:
    EBX = best_idx, or 0xFFFFFFFF if vertex_count == 0. Clobbers EAX/EBX/
    ECX/ESI/EDI and FPU stack (leaves it balanced)."""
    overlay_vertices_va     = layout.va('overlay_vertices')
    overlay_vertex_count_va = layout.va('overlay_vertex_count')
    wp_scratch_va           = layout.va('wp_scratch')

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


def _emit_find_nearest_ex(a: Asm, layout: ScratchLayout) -> None:
    """wp_find_nearest_ex: like wp_find_nearest, but skips up to four vertex
    indices listed in wpfn_excl[0..3] (-1 = unused slot). Used by the wedge
    HARD RESET in bot_movement to acquire the nearest node OUTSIDE the wedge
    cluster (a bot on the wrong side of a wall keeps re-picking the same
    cross-wall nodes otherwise — live 2026-07-20). Pre: wp_scratch = query
    pos, wpfn_excl fully written by the caller (stale contents from an
    earlier reset are harmless only because every caller writes all four).
    Post: EBX = best non-excluded idx, or -1 if none. Clobbers EAX/EBX/ECX/
    ESI/EDI and FPU stack (leaves it balanced)."""
    if not layout.has_field('wpfn_excl'):
        a.label('wp_find_nearest_ex')
        a.raw(b'\xBB\xFF\xFF\xFF\xFF')                           # ebx = -1
        a.raw(b'\xC3')
        return
    overlay_vertices_va     = layout.va('overlay_vertices')
    overlay_vertex_count_va = layout.va('overlay_vertex_count')
    wp_scratch_va           = layout.va('wp_scratch')
    wpfn_excl_va            = layout.va('wpfn_excl')
    wpfn_tmp_va             = layout.va('wpfn_tmp')

    a.label('wp_find_nearest_ex')
    a.raw(b'\xBB\xFF\xFF\xFF\xFF')                               # mov ebx, -1
    a.raw(b'\x8B\x3D' + le32(overlay_vertex_count_va))           # mov edi, [vertex_count]
    a.raw(b'\x85\xFF'); a.jz('wp_fnx_done')
    # Seed best = FLT_MAX (unlike wp_find_nearest, vertex 0 may be excluded,
    # so the seed cannot come from a vertex).
    a.raw(b'\xC7\x05' + le32(wpfn_tmp_va) + le32(0x7F7FFFFF))
    a.raw(b'\xD9\x05' + le32(wpfn_tmp_va))                       # fld FLT_MAX (best)
    a.raw(b'\x31\xF6')                                           # esi = 0

    a.label('wp_fnx_loop')
    a.raw(b'\x39\xFE'); a.jae('wp_fnx_pop_done')                 # if esi >= count, done
    a.raw(b'\x3B\x35' + le32(wpfn_excl_va + 0)); a.jz('wp_fnx_next')
    a.raw(b'\x3B\x35' + le32(wpfn_excl_va + 4)); a.jz('wp_fnx_next')
    a.raw(b'\x3B\x35' + le32(wpfn_excl_va + 8)); a.jz('wp_fnx_next')
    a.raw(b'\x3B\x35' + le32(wpfn_excl_va + 12)); a.jz('wp_fnx_next')
    a.raw(b'\xD9\x04\xF5' + le32(overlay_vertices_va + 0))       # fld [v.x] (esi*8)
    a.raw(b'\xD8\x25' + le32(wp_scratch_va + 0))                 # fsub [scratch.x]
    a.raw(b'\xD8\xC8')                                           # fmul st(0), st(0)
    a.raw(b'\xD9\x04\xF5' + le32(overlay_vertices_va + 4))       # fld [v.y]
    a.raw(b'\xD8\x25' + le32(wp_scratch_va + 4))                 # fsub [scratch.y]
    a.raw(b'\xD8\xC8')                                           # fmul st(0), st(0)
    a.raw(b'\xDE\xC1')                                           # faddp -> dsq; ST1 = best
    a.raw(b'\xD8\xD1')                                           # fcom st(1)
    a.raw(b'\xDF\xE0')                                           # fnstsw ax (eax is scratch)
    a.raw(b'\x9E')                                               # sahf
    a.jae('wp_fnx_skip')                                         # dsq >= best -> skip
    a.raw(b'\xD9\xC9')                                           # fxch st(1)
    a.raw(b'\xDD\xD8')                                           # fstp st(0)  (pop old best)
    a.raw(b'\x89\xF3')                                           # mov ebx, esi
    a.jmp('wp_fnx_next')
    a.label('wp_fnx_skip')
    a.raw(b'\xDD\xD8')                                           # fstp st(0)  (pop dsq)
    a.label('wp_fnx_next')
    a.raw(b'\x46')                                               # inc esi
    a.jmp('wp_fnx_loop')

    a.label('wp_fnx_pop_done')
    a.raw(b'\xDD\xD8')                                           # fstp st(0)  (pop best)
    a.label('wp_fnx_done')
    a.raw(b'\xC3')                                               # ret


def _emit_advance(a: Asm, layout: ScratchLayout) -> None:
    """wp_advance: pick the next vertex to walk toward when following the graph.
    ABI (called by the bot-movement detour inside its pushad frame, so it may
    freely clobber any GPR — popad restores them):
      in : ECX = current vertex idx, EDX = prev vertex idx (-1 if not latched)
      out: EAX = next neighbor idx, or 0xFFFFFFFF if `current` has no edges
    Scans overlay_edges for edges touching `current`. With cfg.WP_RANDOM_
    NEIGHBOR (default) it picks a RANDOM neighbor that is NOT `prev` so bots
    roam the whole graph; otherwise the first non-prev neighbor (deterministic
    for reproducible R-dumps). If the only neighbor is `prev` (dead-end /
    degree-1 node), returns `prev` so the bot walks back. No FPU."""
    overlay_edges_va      = layout.va('overlay_edges')
    overlay_edge_count_va = layout.va('overlay_edge_count')
    wp_scratch_va         = layout.va('wp_scratch')

    a.label('wp_advance')
    if cfg.WP_RANDOM_NEIGHBOR:
        # PASS 1: count forward neighbors (nb != prev) into EDI; remember a
        # prev-neighbor as EBP fallback. ECX=cur, EDX=prev stay live (no calls).
        a.raw(b'\x31\xF6')                                      # xor esi, esi  (r = 0)
        a.raw(b'\x31\xFF')                                      # xor edi, edi  (n_fwd = 0)
        a.raw(b'\xBD\xFF\xFF\xFF\xFF')                          # mov ebp, -1   (fallback)
        a.label('wp_adv_p1_loop')
        a.raw(b'\x3B\x35' + le32(overlay_edge_count_va))        # cmp esi, [edge_count]
        a.jae('wp_adv_p1_done')
        a.raw(b'\x8B\x04\xB5' + le32(overlay_edges_va))         # mov eax, [edges + esi*4]
        a.raw(b'\x0F\xB7\xD8')                                  # movzx ebx, ax  (i = low16)
        a.raw(b'\xC1\xE8\x10')                                  # shr eax, 16    (j = high16)
        a.raw(b'\x39\xCB'); a.jz('wp_adv_p1_nb_j')              # i == cur -> nb = j (eax)
        a.raw(b'\x39\xC8'); a.jz('wp_adv_p1_nb_i')              # j == cur -> nb = i (ebx)
        a.jmp('wp_adv_p1_next')                                 # edge doesn't touch cur
        a.label('wp_adv_p1_nb_i')
        a.raw(b'\x89\xD8')                                      # mov eax, ebx  (nb = i)
        a.label('wp_adv_p1_nb_j')                               # nb in eax
        a.raw(b'\x39\xD0'); a.jz('wp_adv_p1_fallback')          # nb == prev -> fallback
        a.raw(b'\x47')                                          # inc edi  (n_fwd++)
        a.jmp('wp_adv_p1_next')
        a.label('wp_adv_p1_fallback')
        a.raw(b'\x89\xC5')                                      # mov ebp, eax
        a.label('wp_adv_p1_next')
        a.raw(b'\x46')                                          # inc esi
        a.jmp('wp_adv_p1_loop')
        a.label('wp_adv_p1_done')
        a.raw(b'\x85\xFF'); a.jz('wp_adv_fallback')             # no forward neighbor -> fallback
        # k = RNG(0, n_fwd-1). Spill cur/prev across the call (RNG clobbers).
        a.raw(b'\x89\x0D' + le32(wp_scratch_va))                # mov [wp_scratch], ecx (cur)
        a.raw(b'\x89\x15' + le32(wp_scratch_va + 4))            # mov [wp_scratch+4], edx (prev)
        a.raw(b'\x8D\x47\xFF')                                  # lea eax, [edi-1]  (high = n_fwd-1)
        a.raw(b'\x50')                                          # push eax (high)
        a.raw(b'\x6A\x00')                                      # push 0   (low)
        a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                    # mov ecx, RNG instance
        a.call_va(ax.RNG_SUB)                                   # eax = k in [0, n_fwd-1], callee pops 8
        a.raw(b'\x8B\x0D' + le32(wp_scratch_va))                # reload cur -> ecx
        a.raw(b'\x8B\x15' + le32(wp_scratch_va + 4))            # reload prev -> edx
        # PASS 2: return the k-th forward neighbor (EAX counts down; no calls).
        a.raw(b'\x31\xF6')                                      # xor esi, esi
        a.label('wp_adv_p2_loop')
        a.raw(b'\x3B\x35' + le32(overlay_edge_count_va))        # cmp esi, [edge_count]
        a.jae('wp_adv_fallback')                                # safety (unreached)
        a.raw(b'\x8B\x1C\xB5' + le32(overlay_edges_va))         # mov ebx, [edges + esi*4]
        a.raw(b'\x0F\xB7\xFB')                                  # movzx edi, bx  (i = low16)
        a.raw(b'\xC1\xEB\x10')                                  # shr ebx, 16    (j = high16)
        a.raw(b'\x39\xCF'); a.jz('wp_adv_p2_nb_j')              # i == cur -> nb = j (ebx)
        a.raw(b'\x39\xCB'); a.jz('wp_adv_p2_nb_i')              # j == cur -> nb = i (edi)
        a.jmp('wp_adv_p2_next')                                 # edge doesn't touch cur
        a.label('wp_adv_p2_nb_i')
        a.raw(b'\x89\xFB')                                      # mov ebx, edi  (nb = i)
        a.label('wp_adv_p2_nb_j')                               # nb in ebx
        a.raw(b'\x39\xD3'); a.jz('wp_adv_p2_next')              # nb == prev -> skip
        a.raw(b'\x85\xC0'); a.jz('wp_adv_p2_take')              # k == 0 -> take this one
        a.raw(b'\x48')                                          # dec eax  (k--)
        a.jmp('wp_adv_p2_next')
        a.label('wp_adv_p2_take')
        a.raw(b'\x89\xD8')                                      # mov eax, ebx  (return nb)
        a.raw(b'\xC3')                                          # ret
        a.label('wp_adv_p2_next')
        a.raw(b'\x46')                                          # inc esi
        a.jmp('wp_adv_p2_loop')
        a.label('wp_adv_fallback')
        a.raw(b'\x89\xE8')                                      # mov eax, ebp  (fallback / -1)
        a.raw(b'\xC3')                                          # ret
    else:
        a.raw(b'\xBF\xFF\xFF\xFF\xFF')                          # mov edi, -1  (chosen)
        a.raw(b'\xBD\xFF\xFF\xFF\xFF')                          # mov ebp, -1  (fallback)
        a.raw(b'\x31\xF6')                                      # xor esi, esi (r = 0)
        a.label('wp_adv_loop')
        a.raw(b'\x3B\x35' + le32(overlay_edge_count_va))        # cmp esi, [edge_count]
        a.jae('wp_adv_done')
        a.raw(b'\x8B\x04\xB5' + le32(overlay_edges_va))         # mov eax, [edges + esi*4]
        a.raw(b'\x0F\xB7\xD8')                                  # movzx ebx, ax     (i = low16)
        a.raw(b'\xC1\xE8\x10')                                  # shr eax, 16       (j = high16)
        a.raw(b'\x39\xCB'); a.jz('wp_adv_nb_j')                 # if i == current -> nb = j (eax)
        a.raw(b'\x39\xC8'); a.jz('wp_adv_nb_i')                 # if j == current -> nb = i (ebx)
        a.jmp('wp_adv_next')                                    # edge doesn't touch current
        a.label('wp_adv_nb_i')
        a.raw(b'\x89\xD8')                                      # mov eax, ebx      (nb = i)
        a.label('wp_adv_nb_j')                                  # nb now in eax
        a.raw(b'\x39\xD0'); a.jnz('wp_adv_take')                # if nb != prev -> take it
        a.raw(b'\x89\xC5')                                      # mov ebp, eax      (fallback = prev neighbor)
        a.jmp('wp_adv_next')
        a.label('wp_adv_take')
        a.raw(b'\x89\xC7')                                      # mov edi, eax      (chosen = nb)
        a.jmp('wp_adv_done')                                    # first non-prev neighbor wins
        a.label('wp_adv_next')
        a.raw(b'\x46')                                          # inc esi
        a.jmp('wp_adv_loop')
        a.label('wp_adv_done')
        a.raw(b'\x83\xFF\xFF'); a.jnz('wp_adv_ret')             # chosen != -1 ? return it
        a.raw(b'\x89\xEF')                                      # mov edi, ebp      (else use fallback)
        a.label('wp_adv_ret')
        a.raw(b'\x89\xF8')                                      # mov eax, edi
        a.raw(b'\xC3')                                          # ret


def _emit_drop(a: Asm, layout: ScratchLayout) -> None:
    """wp_drop (N key): snap-or-append a vertex at the host position, auto-edge
    from wp_selected_idx, and select the new/snapped node."""
    overlay_vertices_va     = layout.va('overlay_vertices')
    overlay_edges_va        = layout.va('overlay_edges')
    overlay_vertex_count_va = layout.va('overlay_vertex_count')
    overlay_edge_count_va   = layout.va('overlay_edge_count')
    wp_selected_idx_va      = layout.va('wp_selected_idx')
    wp_scratch_va           = layout.va('wp_scratch')
    wp_snap_radius_sq_va    = layout.va('wp_snap_radius_sq')

    vertex_max = cfg.OVERLAY_VERTEX_MAX
    edge_max   = cfg.OVERLAY_EDGE_MAX

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


def _emit_select(a: Asm, layout: ScratchLayout) -> None:
    """wp_select (J key): set wp_selected_idx to the vertex nearest the host."""
    wp_selected_idx_va = layout.va('wp_selected_idx')

    a.label('wp_select')
    a.raw(b'\x60')                                               # pushad
    a.call_lbl('wp_read_host_pos')
    a.raw(b'\x85\xC0'); a.jz('wp_select_done')
    a.call_lbl('wp_find_nearest')                                # ebx = nearest idx or -1
    a.raw(b'\x89\x1D' + le32(wp_selected_idx_va))                # mov [selected], ebx
    a.label('wp_select_done')
    a.raw(b'\x61')                                               # popad
    a.raw(b'\xC3')                                               # ret


def _emit_delete(a: Asm, layout: ScratchLayout) -> None:
    """wp_delete (X key): remove the nearest vertex (swap-with-last), compact
    overlay_edges, and patch wp_selected_idx."""
    overlay_vertices_va     = layout.va('overlay_vertices')
    overlay_edges_va        = layout.va('overlay_edges')
    overlay_vertex_count_va = layout.va('overlay_vertex_count')
    overlay_edge_count_va   = layout.va('overlay_edge_count')
    wp_selected_idx_va      = layout.va('wp_selected_idx')

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


def _emit_build_filename(a: Asm, layout: ScratchLayout) -> None:
    """wp_build_filename: writes "<prefix><sanitized_map_name><suffix>\\0" into
    wp_filename_buf. Returns EAX = 1 on success, 0 if the map name is null
    or empty. Sanitization: '/' (0x2F) and '\\\\' (0x5C) → '_' (0x5F) so we
    never accidentally cross directory boundaries. Clobbers all GP regs.
    Hard-caps the filename at 240 chars (safe under the 256-byte buffer)."""
    wp_filename_buf_va  = layout.va('wp_filename_buf')
    wp_prefix_static_va = layout.va('wp_prefix_static')
    wp_suffix_static_va = layout.va('wp_suffix_static')

    a.label('wp_build_filename')
    # Resolve map-name ASCII ptr.
    a.raw(b'\xA1' + le32(ax.MAP_NAME_CSTRING_VA))                # eax = [csheader]
    a.raw(b'\x85\xC0'); a.jz('wp_bf_fail')
    a.raw(b'\x83\xC0' + bytes([ax.MAP_NAME_ASCII_OFFSET]))        # add eax, 8 (skip refcount+len)
    a.raw(b'\x80\x38\x00'); a.jz('wp_bf_fail')                    # cmp byte [eax], 0 -- empty name?
    a.raw(b'\x89\xC6')                                           # mov esi, eax (src = name)

    a.raw(b'\xBF' + le32(wp_filename_buf_va))                    # edi = dest = filename_buf
    a.raw(b'\xBA\xF0\x00\x00\x00')                               # edx = 240 (remaining cap)

    # --- Copy prefix --------------------------------------------------------
    a.raw(b'\xB9' + le32(wp_prefix_static_va))                   # ecx = src = prefix
    a.label('wp_bf_pfx_loop')
    a.raw(b'\x85\xD2'); a.jz('wp_bf_fail')                       # cap exhausted?
    a.raw(b'\x8A\x01')                                           # mov al, [ecx]
    a.raw(b'\x84\xC0'); a.jz('wp_bf_after_pfx')                  # NUL? done with prefix
    a.raw(b'\x88\x07')                                           # mov [edi], al
    a.raw(b'\x41\x47\x4A')                                       # inc ecx, inc edi, dec edx
    a.jmp('wp_bf_pfx_loop')

    # --- Copy sanitized map name --------------------------------------------
    a.label('wp_bf_after_pfx')
    a.label('wp_bf_name_loop')
    a.raw(b'\x85\xD2'); a.jz('wp_bf_fail')                       # cap exhausted?
    a.raw(b'\x8A\x06')                                           # mov al, [esi]
    a.raw(b'\x84\xC0'); a.jz('wp_bf_after_name')                 # NUL? done with name
    # Sanitize: '/' or '\\' -> '_'
    a.raw(b'\x3C\x2F'); a.jnz('wp_bf_chk_bslash')                # cmp al, '/'
    a.raw(b'\xB0\x5F'); a.jmp('wp_bf_write_name')                # mov al, '_'
    a.label('wp_bf_chk_bslash')
    a.raw(b'\x3C\x5C'); a.jnz('wp_bf_write_name')                # cmp al, '\\'
    a.raw(b'\xB0\x5F')                                           # mov al, '_'
    a.label('wp_bf_write_name')
    a.raw(b'\x88\x07')                                           # mov [edi], al
    a.raw(b'\x46\x47\x4A')                                       # inc esi, inc edi, dec edx
    a.jmp('wp_bf_name_loop')

    # --- Copy suffix --------------------------------------------------------
    a.label('wp_bf_after_name')
    a.raw(b'\xB9' + le32(wp_suffix_static_va))                   # ecx = src = suffix
    a.label('wp_bf_sfx_loop')
    a.raw(b'\x85\xD2'); a.jz('wp_bf_fail')
    a.raw(b'\x8A\x01')                                           # mov al, [ecx]
    a.raw(b'\x84\xC0'); a.jz('wp_bf_done')                       # NUL? done
    a.raw(b'\x88\x07')                                           # mov [edi], al
    a.raw(b'\x41\x47\x4A')                                       # inc ecx, inc edi, dec edx
    a.jmp('wp_bf_sfx_loop')

    a.label('wp_bf_done')
    a.raw(b'\xC6\x07\x00')                                       # mov byte [edi], 0 (NUL term)
    a.raw(b'\xB8\x01\x00\x00\x00')                               # mov eax, 1
    a.raw(b'\xC3')
    a.label('wp_bf_fail')
    a.raw(b'\x31\xC0\xC3')                                       # xor eax,eax; ret


def _emit_save(a: Asm, layout: ScratchLayout) -> None:
    """wp_save (',' key): persist current overlay_vertices/edges to
    waypoints/<sanitized_map_name>.zwpt. File format:
      +0   u32  magic 'ZWPT' (0x5450575A)
      +4   u32  version (1)
      +8   u32  vertex_count
      +12  u32  edge_count
      +16  ..   vertices (float[2] × vertex_count)
      +..  ..   edges    (u16[2]   × edge_count)"""
    overlay_vertices_va     = layout.va('overlay_vertices')
    overlay_edges_va        = layout.va('overlay_edges')
    overlay_vertex_count_va = layout.va('overlay_vertex_count')
    overlay_edge_count_va   = layout.va('overlay_edge_count')
    wp_filename_buf_va      = layout.va('wp_filename_buf')
    wp_file_header_va       = layout.va('wp_file_header')
    wp_io_count_va          = layout.va('wp_io_count')
    wp_dir_static_va        = layout.va('wp_dir_static')
    wp_msg_saved_va         = layout.va('wp_msg_saved')
    wp_msg_nomap_va         = layout.va('wp_msg_nomap')
    wp_msg_failed_va        = layout.va('wp_msg_failed')

    a.label('wp_save')
    a.raw(b'\x60')                                               # pushad
    a.call_lbl('wp_build_filename')
    a.raw(b'\x85\xC0'); a.jz('wp_save_nomap')

    # CreateDirectoryA(wp_dir_static, NULL) — best effort; ignore result.
    a.raw(b'\x6A\x00')
    a.raw(b'\x68' + le32(wp_dir_static_va))
    a.raw(b'\xFF\x15' + le32(ax.IMP_CREATEDIRECTORYA))

    # CreateFileA(filename, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL)
    a.raw(b'\x6A\x00')                                           # hTemplateFile
    a.raw(b'\x68\x80\x00\x00\x00')                               # FILE_ATTRIBUTE_NORMAL
    a.raw(b'\x6A\x02')                                           # CREATE_ALWAYS
    a.raw(b'\x6A\x00')                                           # lpSecurityAttributes
    a.raw(b'\x6A\x00')                                           # dwShareMode = 0 (exclusive)
    a.raw(b'\x68\x00\x00\x00\x40')                               # GENERIC_WRITE
    a.raw(b'\x68' + le32(wp_filename_buf_va))                    # lpFileName
    a.raw(b'\xFF\x15' + le32(ax.IMP_CREATEFILEA))
    a.raw(b'\x83\xF8\xFF'); a.jz('wp_save_fail')                 # cmp eax, -1
    a.raw(b'\x85\xC0'); a.jz('wp_save_fail')
    a.raw(b'\x89\xC3')                                           # mov ebx, eax (hFile)

    # Build header in wp_file_header.
    a.raw(b'\xC7\x05' + le32(wp_file_header_va + 0)  + le32(ZWPT_MAGIC))
    a.raw(b'\xC7\x05' + le32(wp_file_header_va + 4)  + le32(ZWPT_VERSION))
    a.raw(b'\xA1' + le32(overlay_vertex_count_va))               # eax = vert_count
    a.raw(b'\xA3' + le32(wp_file_header_va + 8))
    a.raw(b'\xA1' + le32(overlay_edge_count_va))                 # eax = edge_count
    a.raw(b'\xA3' + le32(wp_file_header_va + 12))

    # WriteFile(hFile, &header, 16, &wp_io_count, NULL)
    a.raw(b'\x6A\x00')                                           # lpOverlapped
    a.raw(b'\x68' + le32(wp_io_count_va))                        # lpNumberOfBytesWritten
    a.raw(b'\x6A\x10')                                           # nNumberOfBytesToWrite = 16
    a.raw(b'\x68' + le32(wp_file_header_va))                     # lpBuffer
    a.raw(b'\x53')                                               # hFile
    a.raw(b'\xFF\x15' + le32(ax.IMP_WRITEFILE))

    # WriteFile vertices if any.
    a.raw(b'\xA1' + le32(overlay_vertex_count_va))               # eax = vert count
    a.raw(b'\xC1\xE0\x03')                                       # shl eax, 3 (* 8 bytes per vert)
    a.raw(b'\x85\xC0'); a.jz('wp_save_skip_verts')
    a.raw(b'\x6A\x00')
    a.raw(b'\x68' + le32(wp_io_count_va))
    a.raw(b'\x50')                                               # push eax (count)
    a.raw(b'\x68' + le32(overlay_vertices_va))
    a.raw(b'\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_WRITEFILE))
    a.label('wp_save_skip_verts')

    # WriteFile edges if any.
    a.raw(b'\xA1' + le32(overlay_edge_count_va))                 # eax = edge count
    a.raw(b'\xC1\xE0\x02')                                       # shl eax, 2 (* 4 bytes per edge)
    a.raw(b'\x85\xC0'); a.jz('wp_save_skip_edges')
    a.raw(b'\x6A\x00')
    a.raw(b'\x68' + le32(wp_io_count_va))
    a.raw(b'\x50')                                               # push eax (count)
    a.raw(b'\x68' + le32(overlay_edges_va))
    a.raw(b'\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_WRITEFILE))
    a.label('wp_save_skip_edges')

    # CloseHandle(hFile)
    a.raw(b'\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_CLOSEHANDLE))

    # On-screen confirmation.
    a.raw(b'\x6A\xFF')
    a.raw(b'\x68' + le32(wp_msg_saved_va))
    a.call_va(ax.SHOWMSG_VA)

    a.raw(b'\x61\xC3')                                           # popad; ret

    a.label('wp_save_nomap')
    a.raw(b'\x6A\xFF')
    a.raw(b'\x68' + le32(wp_msg_nomap_va))
    a.call_va(ax.SHOWMSG_VA)
    a.raw(b'\x61\xC3')

    a.label('wp_save_fail')
    a.raw(b'\x6A\xFF')
    a.raw(b'\x68' + le32(wp_msg_failed_va))
    a.call_va(ax.SHOWMSG_VA)
    a.raw(b'\x61\xC3')


def _emit_load(a: Asm, layout: ScratchLayout) -> None:
    """wp_load: read waypoints/<map>.zwpt and populate overlay_vertices/edges
    in-place. Missing file is NOT an error — counts are zeroed (fresh map =
    empty graph). Called from detour_df90 on match change. Resets
    wp_selected_idx."""
    overlay_vertices_va     = layout.va('overlay_vertices')
    overlay_edges_va        = layout.va('overlay_edges')
    overlay_vertex_count_va = layout.va('overlay_vertex_count')
    overlay_edge_count_va   = layout.va('overlay_edge_count')
    wp_selected_idx_va      = layout.va('wp_selected_idx')
    wp_filename_buf_va      = layout.va('wp_filename_buf')
    wp_file_header_va       = layout.va('wp_file_header')
    wp_io_count_va          = layout.va('wp_io_count')
    wp_msg_loaded_va        = layout.va('wp_msg_loaded')

    vertex_max = cfg.OVERLAY_VERTEX_MAX
    edge_max   = cfg.OVERLAY_EDGE_MAX

    a.label('wp_load')
    a.raw(b'\x60')                                               # pushad
    # Zero counts up-front: if anything below fails, we leave a clean slate
    # rather than mixed stale state.
    a.raw(b'\xC7\x05' + le32(overlay_vertex_count_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(overlay_edge_count_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(wp_selected_idx_va) + b'\xFF\xFF\xFF\xFF')

    a.call_lbl('wp_build_filename')
    a.raw(b'\x85\xC0'); a.jz('wp_load_done')                     # no map name -> nothing to load

    # CreateFileA(filename, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL)
    a.raw(b'\x6A\x00')                                           # hTemplateFile
    a.raw(b'\x68\x80\x00\x00\x00')                               # FILE_ATTRIBUTE_NORMAL
    a.raw(b'\x6A\x03')                                           # OPEN_EXISTING
    a.raw(b'\x6A\x00')                                           # lpSecurityAttributes
    a.raw(b'\x6A\x01')                                           # FILE_SHARE_READ
    a.raw(b'\x68\x00\x00\x00\x80')                               # GENERIC_READ
    a.raw(b'\x68' + le32(wp_filename_buf_va))                    # lpFileName
    a.raw(b'\xFF\x15' + le32(ax.IMP_CREATEFILEA))
    a.raw(b'\x83\xF8\xFF'); a.jz('wp_load_done')                 # INVALID_HANDLE_VALUE → silent miss
    a.raw(b'\x85\xC0'); a.jz('wp_load_done')
    a.raw(b'\x89\xC3')                                           # mov ebx, eax (hFile)

    # ReadFile(hFile, &header, 16, &wp_io_count, NULL)
    a.raw(b'\x6A\x00')
    a.raw(b'\x68' + le32(wp_io_count_va))
    a.raw(b'\x6A\x10')                                           # 16 bytes
    a.raw(b'\x68' + le32(wp_file_header_va))
    a.raw(b'\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_READFILE))
    a.raw(b'\x85\xC0'); a.jz('wp_load_close_fail')
    a.raw(b'\x83\x3D' + le32(wp_io_count_va) + b'\x10')          # cmp [io_count], 16
    a.jnz('wp_load_close_fail')

    # Validate magic + version.
    a.raw(b'\x81\x3D' + le32(wp_file_header_va + 0) + le32(ZWPT_MAGIC))
    a.jnz('wp_load_close_fail')
    a.raw(b'\x83\x3D' + le32(wp_file_header_va + 4) + b'\x01')
    a.jnz('wp_load_close_fail')

    # Bounds-check vertex_count <= vertex_max
    a.raw(b'\xA1' + le32(wp_file_header_va + 8))                 # eax = vert count
    a.raw(b'\x3D' + le32(vertex_max))                            # cmp eax, vertex_max
    a.ja('wp_load_close_fail')
    # Bounds-check edge_count <= edge_max
    a.raw(b'\x8B\x0D' + le32(wp_file_header_va + 12))            # ecx = edge count
    a.raw(b'\x81\xF9' + le32(edge_max))                          # cmp ecx, edge_max
    a.ja('wp_load_close_fail')

    # Read vertex array if non-empty.
    a.raw(b'\x85\xC0'); a.jz('wp_load_no_verts')                 # zero verts -> skip
    a.raw(b'\xC1\xE0\x03')                                       # shl eax, 3 (bytes)
    a.raw(b'\x6A\x00')
    a.raw(b'\x68' + le32(wp_io_count_va))
    a.raw(b'\x50')                                               # push eax (count bytes)
    a.raw(b'\x68' + le32(overlay_vertices_va))
    a.raw(b'\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_READFILE))
    a.raw(b'\x85\xC0'); a.jz('wp_load_close_fail')
    a.label('wp_load_no_verts')

    # Read edge array if non-empty.
    a.raw(b'\x8B\x0D' + le32(wp_file_header_va + 12))            # ecx = edge count (reload)
    a.raw(b'\x85\xC9'); a.jz('wp_load_commit')
    a.raw(b'\xC1\xE1\x02')                                       # shl ecx, 2 (bytes)
    a.raw(b'\x6A\x00')
    a.raw(b'\x68' + le32(wp_io_count_va))
    a.raw(b'\x51')                                               # push ecx
    a.raw(b'\x68' + le32(overlay_edges_va))
    a.raw(b'\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_READFILE))
    a.raw(b'\x85\xC0'); a.jz('wp_load_close_fail')

    a.label('wp_load_commit')
    # Commit counts.
    a.raw(b'\xA1' + le32(wp_file_header_va + 8))                 # eax = vert_count
    a.raw(b'\xA3' + le32(overlay_vertex_count_va))
    a.raw(b'\xA1' + le32(wp_file_header_va + 12))                # eax = edge_count
    a.raw(b'\xA3' + le32(overlay_edge_count_va))

    # Close + notify.
    a.raw(b'\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_CLOSEHANDLE))
    a.raw(b'\x6A\xFF')
    a.raw(b'\x68' + le32(wp_msg_loaded_va))
    a.call_va(ax.SHOWMSG_VA)
    a.raw(b'\x61\xC3')

    a.label('wp_load_close_fail')
    # Reset counts (already 0 from entry; redundant but explicit).
    a.raw(b'\xC7\x05' + le32(overlay_vertex_count_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(overlay_edge_count_va) + le32(0))
    a.raw(b'\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_CLOSEHANDLE))
    a.label('wp_load_done')
    a.raw(b'\x61\xC3')
