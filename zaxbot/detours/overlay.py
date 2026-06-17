"""Waypoint overlay — draws authored vertices and edges on the back buffer
immediately before the per-frame page flip.

Hooks ``sub_5693A0`` (CGraphics page-flip / windowed Blt entry). At call
time ECX is the active renderer (``*(0x6C02CC + 4)``), the back-buffer
``dword_713338`` holds the rendered frame, and the surface is unlocked.

**Camera handling.** The engine's normal world-render path keeps the
renderer's stored cam fields (`renderer+8/+10`, int16) at zero and has
each entity pre-compute screen coords from its own world position
before calling the line drawer. Inside ``sub_42B160`` the clip rect
comes out as ``(0, 0, screen_w, screen_h)`` in 16.16 fixed point, and
the endpoints come out as screen-relative 16.16 — matching coord
frames so the Cohen-Sutherland clipper culls/clips correctly.

If we wrote a non-zero cam into the renderer, ``sub_42B160`` would
shift the clip rect into world space (``cam*65536``) while the endpoints
stayed in screen space — totally different frames — and lines would
get culled as "outside the clip rect" even when partially visible
on screen. That's the "partially rendered overlay" symptom: as soon as
one endpoint left a small inner region, the whole line got rejected.

So instead: read the engine's screen-edge cam from the host's tracker
layer (`layer+0xC0/0xC4` floats — set by ``sub_4F5DD0`` after smoothing,
dead-zone, and map-bound clamp), stash as ``overlay_cam_x/y``, and have
each draw loop subtract those from world coords to produce screen coords
before passing the pointers to ``sub_4FCCC0`` / ``sub_4B3CB0``. The
renderer's cam stays at 0, so the engine's clip frame matches.

**Color quirk (8-bit palette).** ``sub_53F010`` stamps each CColor's palette
index via ``sub_433A10(blue)`` — from the BLUE byte alone — and in the game's
8-bit palettized display mode the line drawer uses that index, not the RGB. So
the rendered color is driven only by blue: ``blue=0`` renders BLACK (vertices,
edges), ``blue=255`` renders a visible color. Keep visible graph elements on a
non-zero blue channel. See ``cfg.OVERLAY_*_COLOR`` and ``ax.SUB_53F010_VA``.

For each enabled overlay, the detour:

1. Skips fast if ``overlay_enabled`` is 0.
2. Walks ``mgr -> level -> mpd`` and ``sub_59FF90(mgr)`` to suppress
   drawing outside an active match (main-menu safety).
3. Reloads the renderer from ``*(RENDERER_OWNER_VA + 4)``.
4. Walks ``worldmgr.vtbl[+0xB0] -> container.vtbl[+0x5C](0)`` to the
   host's camera-tracker layer. Reads ``layer+0xC0/0xC4`` floats into
   ``overlay_cam_x/y`` and sets ``overlay_cam_ok = 1``. Failure on any
   step leaves ``cam_ok = 0`` and the loops bail.
5. Calls ``sub_53F010`` once per color.
6. Loops vertices: subtract cam to get screen p1, cheap-cull off-screen
   points, then call ``sub_4FCCC0(renderer, &p1, radius, aspect, &color)``.
7. Loops detected pickups from ``pickup_table`` using the same oval path.
8. Loops edges: subtract cam from both endpoints, cheap-cull segments whose
   endpoints are both outside the same side of the screen, then call
   ``sub_4B3CB0(renderer, &p1, &p2, &color)``.
9. ``popad``, re-execute displaced ``mov al, byte_6210C0``, jump back.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    overlay_enabled_va        = layout.va('overlay_enabled')
    overlay_vertex_color_va   = layout.va('overlay_vertex_color')
    overlay_edge_color_va     = layout.va('overlay_edge_color')
    overlay_selected_color_va = layout.va('overlay_selected_color')
    overlay_vertex_radius_va  = layout.va('overlay_vertex_radius')
    overlay_vertex_aspect_va  = layout.va('overlay_vertex_aspect')
    overlay_vertex_count_va   = layout.va('overlay_vertex_count')
    overlay_edge_count_va     = layout.va('overlay_edge_count')
    overlay_renderer_tmp_va   = layout.va('overlay_renderer_tmp')
    overlay_cam_x_va          = layout.va('overlay_cam_x')
    overlay_cam_y_va          = layout.va('overlay_cam_y')
    overlay_cam_ok_va         = layout.va('overlay_cam_ok')
    overlay_tmp_p1_va         = layout.va('overlay_tmp_p1')
    overlay_tmp_p2_va         = layout.va('overlay_tmp_p2')
    overlay_cull_min_x_va     = layout.va('overlay_cull_min_x')
    overlay_cull_max_x_va     = layout.va('overlay_cull_max_x')
    overlay_cull_min_y_va     = layout.va('overlay_cull_min_y')
    overlay_cull_max_y_va     = layout.va('overlay_cull_max_y')
    overlay_vertices_va       = layout.va('overlay_vertices') if layout.has_field('overlay_vertices') else 0
    overlay_edges_va          = layout.va('overlay_edges')    if layout.has_field('overlay_edges')    else 0
    wp_selected_idx_va        = layout.va('wp_selected_idx')
    overlay_pickup_color_va   = layout.va('overlay_pickup_color')
    overlay_portal_color_va   = layout.va('overlay_portal_color') if layout.has_field('overlay_portal_color') else 0
    world_frame_va            = layout.va('world_frame')
    pickup_count_va           = layout.va('pickup_count')
    pickup_table_va           = layout.va('pickup_table') if layout.has_field('pickup_table') else 0
    portal_count_va           = layout.va('portal_count') if layout.has_field('portal_count') else 0
    portal_table_va           = layout.va('portal_table') if layout.has_field('portal_table') else 0
    portal_scan_count_va      = layout.va('portal_scan_count') if layout.has_field('portal_scan_count') else 0

    vr, vg, vb, va_ = _split_rgba_static('vertex')
    er, eg, eb, ea  = _split_rgba_static('edge')
    sr, sg, sb, sa  = _split_rgba_static('selected')
    pr, pg, pb, pa  = _split_rgba_static('pickup')
    tr, tg, tb, ta  = _split_rgba_static('portal')

    a.label('detour_5693A0')
    # Per-frame tick — bumped here (the one reliable once-per-frame site, the
    # page flip) BEFORE the overlay_enabled gate so the pickup table's lazy
    # reset keeps working even when the overlay itself is disabled.
    a.raw(b'\xFF\x05' + le32(world_frame_va))                 # ++world_frame
    # Per-portal active-state periodic re-scan (immune to the overlay gate, so it
    # tracks dynamic activation even with the overlay hidden). Countdown frames;
    # when it hits 0, reset to the interval and run scan_portal_active (self-
    # contained pushad/popad; flags are re-set by the cmp below). The countdown
    # is seeded to 1 on match change by detour_df90, so it fires shortly after a
    # match starts and every PORTAL_ACTIVE_SCAN_INTERVAL frames after.
    if cfg.PORTAL_ACTIVE_ENABLED and portal_scan_count_va:
        # Reset value MUST be >= 1: the dec/jnz fires the scan when the counter
        # hits exactly 0, so a 0 reset would underflow to a ~4-billion-frame
        # never-fire state. Clamp defensively (matches the df90 seed of 1).
        pa_interval = max(1, cfg.PORTAL_ACTIVE_SCAN_INTERVAL)
        a.raw(b'\xFF\x0D' + le32(portal_scan_count_va))       # dec [portal_scan_count]
        a.jnz('ov_pa_skip')
        a.raw(b'\xC7\x05' + le32(portal_scan_count_va)
              + le32(pa_interval))                            # reset countdown
        a.call_lbl('scan_portal_active')
        a.label('ov_pa_skip')
    a.raw(b'\x83\x3D' + le32(overlay_enabled_va) + b'\x00')   # cmp [overlay_enabled], 0
    a.jz('ov_resume')

    a.raw(b'\x60')                                            # pushad
    # Default: assume cam lookup will fail. Set to 1 only after we
    # actually read layer+0xC0/0xC4 successfully.
    a.raw(b'\xC7\x05' + le32(overlay_cam_ok_va) + le32(0))    # mov [cam_ok], 0

    # mp_gate + active-gametype check.
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))               # mov eax, [mgr]
    a.raw(b'\x85\xC0'); a.jz('ov_popad_only')
    a.raw(b'\x89\xC1\x8B\x10')                                # mov ecx, eax; mov edx, [eax]
    a.raw(b'\xFF\x92' + le32(ax.VT_OFFSET_TO_LVL))            # call [edx + vt_to_lvl]
    a.raw(b'\x85\xC0'); a.jz('ov_popad_only')
    a.raw(b'\x8B\x40' + bytes([ax.MP_DATA_FIELD]))            # mov eax, [eax + 0x30]
    a.raw(b'\x85\xC0'); a.jz('ov_popad_only')
    a.raw(b'\x8B\x0D' + le32(ax.MANAGER_GLOBAL_VA))           # mov ecx, [mgr]
    a.call_va(ax.SUB_59FF90_VA)                               # eax = active gametype
    a.raw(b'\x85\xC0'); a.jz('ov_popad_only')

    # Renderer.
    a.raw(b'\xA1' + le32(ax.RENDERER_OWNER_VA))               # mov eax, [CGame*]
    a.raw(b'\x85\xC0'); a.jz('ov_popad_only')
    a.raw(b'\x8B\x40' + bytes([ax.RENDERER_OFF_IN_OWNER]))    # mov eax, [eax + 4]
    a.raw(b'\x85\xC0'); a.jz('ov_popad_only')
    a.raw(b'\xA3' + le32(overlay_renderer_tmp_va))            # mov [renderer_tmp], eax
    a.raw(b'\x89\xC7')                                        # mov edi, eax

    # --- Camera lookup ----------------------------------------------------
    a.raw(b'\x8B\x0D' + le32(ax.WORLDMGR_GLOBAL))             # mov ecx, [worldmgr]
    a.raw(b'\x85\xC9'); a.jz('ov_after_cam')
    a.raw(b'\x8B\x01')                                        # mov eax, [ecx]
    a.raw(b'\xFF\x90\xB0\x00\x00\x00')                        # call [eax + 0xB0] -> container
    a.raw(b'\x85\xC0'); a.jz('ov_after_cam')
    a.raw(b'\x6A\x00')                                        # push 0 (host idx)
    a.raw(b'\x89\xC1')                                        # mov ecx, eax (container)
    a.raw(b'\x8B\x10')                                        # mov edx, [eax]
    a.raw(b'\xFF\x52\x5C')                                    # call [edx + 0x5C] -> layer (ret 4)
    a.raw(b'\x85\xC0'); a.jz('ov_after_cam')
    # eax = layer; copy +0xC0 / +0xC4 floats verbatim.
    a.raw(b'\x8B\x88\xC0\x00\x00\x00')                        # mov ecx, [eax + 0xC0]
    a.raw(b'\x89\x0D' + le32(overlay_cam_x_va))               # mov [cam_x], ecx
    a.raw(b'\x8B\x88\xC4\x00\x00\x00')                        # mov ecx, [eax + 0xC4]
    a.raw(b'\x89\x0D' + le32(overlay_cam_y_va))               # mov [cam_y], ecx
    a.raw(b'\xC7\x05' + le32(overlay_cam_ok_va) + le32(1))    # mov [cam_ok], 1

    a.label('ov_after_cam')

    # If cam lookup failed, skip drawing entirely (drawing with world
    # coords would put the overlay at fixed screen pixels = view-glued).
    a.raw(b'\x83\x3D' + le32(overlay_cam_ok_va) + b'\x00')    # cmp [cam_ok], 0
    a.jz('ov_popad_only')

    # --- Build vertex color ----------------------------------------------
    a.raw(b'\x6A' + bytes([va_]))
    a.raw(b'\x6A' + bytes([vb]))
    a.raw(b'\x6A' + bytes([vg]))
    a.raw(b'\x6A' + bytes([vr]))
    a.raw(b'\xB9' + le32(overlay_vertex_color_va))
    a.call_va(ax.SUB_53F010_VA)

    # --- Build edge color -------------------------------------------------
    a.raw(b'\x6A' + bytes([ea]))
    a.raw(b'\x6A' + bytes([eb]))
    a.raw(b'\x6A' + bytes([eg]))
    a.raw(b'\x6A' + bytes([er]))
    a.raw(b'\xB9' + le32(overlay_edge_color_va))
    a.call_va(ax.SUB_53F010_VA)

    # --- Build selected-vertex color (consumed by the highlight pass) ----
    a.raw(b'\x6A' + bytes([sa]))
    a.raw(b'\x6A' + bytes([sb]))
    a.raw(b'\x6A' + bytes([sg]))
    a.raw(b'\x6A' + bytes([sr]))
    a.raw(b'\xB9' + le32(overlay_selected_color_va))
    a.call_va(ax.SUB_53F010_VA)

    # --- Build pickup-marker color (consumed by the pickup pass) ----------
    a.raw(b'\x6A' + bytes([pa]))
    a.raw(b'\x6A' + bytes([pb]))
    a.raw(b'\x6A' + bytes([pg]))
    a.raw(b'\x6A' + bytes([pr]))
    a.raw(b'\xB9' + le32(overlay_pickup_color_va))
    a.call_va(ax.SUB_53F010_VA)

    # --- Build portal-marker color (consumed by the portal pass) ----------
    if overlay_portal_color_va:
        a.raw(b'\x6A' + bytes([ta]))
        a.raw(b'\x6A' + bytes([tb]))
        a.raw(b'\x6A' + bytes([tg]))
        a.raw(b'\x6A' + bytes([tr]))
        a.raw(b'\xB9' + le32(overlay_portal_color_va))
        a.call_va(ax.SUB_53F010_VA)

    # --- Draw vertices ----------------------------------------------------
    if overlay_vertices_va:
        a.raw(b'\x31\xF6')                                    # xor esi, esi
        a.raw(b'\x8B\x1D' + le32(overlay_vertex_count_va))    # mov ebx, [vertex_count]
        a.raw(b'\x85\xDB'); a.jz('ov_after_vertices')

        a.label('ov_vertex_loop')
        a.raw(b'\x39\xDE'); a.jae('ov_after_vertices')

        # screen_x = world_x - cam_x; screen_y = world_y - cam_y.
        # FPU subtract; result into overlay_tmp_p1. ModR/M = 0x04 (MOD=00,
        # SIB follows) and SIB = 0xF5 (SCALE=8, INDEX=ESI, BASE=disp32)
        # — MOD=10 form would incorrectly add EBP as base.
        a.raw(b'\xD9\x04\xF5' + le32(overlay_vertices_va))    # fld dword [vertices + esi*8]
        a.raw(b'\xD8\x25' + le32(overlay_cam_x_va))           # fsub dword [cam_x]
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va))          # fstp dword [tmp_p1]
        a.raw(b'\xD9\x04\xF5' + le32(overlay_vertices_va + 4))  # fld dword [vertices + esi*8 + 4]
        a.raw(b'\xD8\x25' + le32(overlay_cam_y_va))           # fsub dword [cam_y]
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va + 4))      # fstp dword [tmp_p1 + 4]

        _emit_point_cull(
            a, overlay_tmp_p1_va,
            overlay_cull_min_x_va, overlay_cull_max_x_va,
            overlay_cull_min_y_va, overlay_cull_max_y_va,
            'ov_vertex_next',
        )
        a.raw(b'\xBA' + le32(overlay_tmp_p1_va))              # mov edx, &tmp_p1 (oval center)
        a.raw(b'\x68' + le32(overlay_vertex_color_va))        # push &color
        a.raw(b'\xFF\x35' + le32(overlay_vertex_aspect_va))   # push aspect
        a.raw(b'\xFF\x35' + le32(overlay_vertex_radius_va))   # push radius
        a.raw(b'\x89\xF9')                                    # mov ecx, edi (renderer)
        a.call_va(ax.SUB_4FCCC0_VA)
        a.label('ov_vertex_next')
        a.raw(b'\x46')                                        # inc esi
        a.jmp('ov_vertex_loop')

        a.label('ov_after_vertices')

        # --- Draw selected-vertex highlight ------------------------------
        # If wp_selected_idx is in-range, redraw that single vertex in the
        # selected color so the editing cursor is visible. The standard
        # vertex pass already drew it in the base color; the extra oval
        # overpaints with the selected color.
        a.raw(b'\xA1' + le32(wp_selected_idx_va))             # eax = selected
        a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1 (sign-ext)
        a.jz('ov_after_selected')
        a.raw(b'\x3B\x05' + le32(overlay_vertex_count_va))    # cmp eax, [count]
        a.jae('ov_after_selected')
        # Load vertices[eax] - cam into tmp_p1.
        a.raw(b'\xD9\x04\xC5' + le32(overlay_vertices_va))    # fld dword [eax*8 + verts]
        a.raw(b'\xD8\x25' + le32(overlay_cam_x_va))
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va))
        a.raw(b'\xD9\x04\xC5' + le32(overlay_vertices_va + 4))  # fld [eax*8 + verts + 4]
        a.raw(b'\xD8\x25' + le32(overlay_cam_y_va))
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va + 4))
        _emit_point_cull(
            a, overlay_tmp_p1_va,
            overlay_cull_min_x_va, overlay_cull_max_x_va,
            overlay_cull_min_y_va, overlay_cull_max_y_va,
            'ov_after_selected',
        )
        a.raw(b'\xBA' + le32(overlay_tmp_p1_va))              # mov edx, &tmp_p1
        a.raw(b'\x68' + le32(overlay_selected_color_va))      # push &selected_color
        a.raw(b'\xFF\x35' + le32(overlay_vertex_aspect_va))   # push aspect
        a.raw(b'\xFF\x35' + le32(overlay_vertex_radius_va))   # push radius
        a.raw(b'\x89\xF9')                                    # mov ecx, edi (renderer)
        a.call_va(ax.SUB_4FCCC0_VA)
        a.label('ov_after_selected')

    # --- Draw detected pickups (item-grab feature, stage-1 verification) --
    # Same world->screen (subtract cam) + oval-draw path as vertices, over the
    # live pickup_table populated by detour_53DA40. Ungated by overlay_vertices
    # so pickups render on maps without an authored waypoint graph too.
    if pickup_table_va:
        a.raw(b'\xBD' + le32(pickup_table_va))               # ebp = table base
        a.raw(b'\x8B\x1D' + le32(pickup_count_va))           # ebx = count
        a.raw(b'\xB9' + le32(overlay_pickup_color_va))       # ecx = color
        a.call_lbl('ov_draw_point_table')

    # --- Draw detected portals ------------------------------------------
    # Populated once per match by load_portals from static Data.dat-derived
    # map data. Same world->screen + oval path as waypoint vertices/pickups,
    # but with a distinct color so teleport support can be verified in-game.
    if portal_table_va and overlay_portal_color_va:
        a.raw(b'\xBD' + le32(portal_table_va))               # ebp = table base
        a.raw(b'\x8B\x1D' + le32(portal_count_va))           # ebx = count
        a.raw(b'\xB9' + le32(overlay_portal_color_va))       # ecx = color
        a.call_lbl('ov_draw_point_table')

    # --- Draw edges -------------------------------------------------------
    if overlay_edges_va and overlay_vertices_va:
        a.raw(b'\x31\xF6')                                    # xor esi, esi
        a.raw(b'\x8B\x1D' + le32(overlay_edge_count_va))      # mov ebx, [edge_count]
        a.raw(b'\x85\xDB'); a.jz('ov_popad_only')

        a.label('ov_edge_loop')
        a.raw(b'\x39\xDE'); a.jae('ov_popad_only')
        a.raw(b'\x0F\xB7\x04\xB5' + le32(overlay_edges_va))   # movzx eax, word [edges + esi*4]
        a.raw(b'\x0F\xB7\x14\xB5' + le32(overlay_edges_va + 2))  # movzx edx, word [edges + esi*4 + 2]
        a.raw(b'\x3B\x05' + le32(overlay_vertex_count_va))
        a.jae('ov_skip_edge')
        a.raw(b'\x3B\x15' + le32(overlay_vertex_count_va))
        a.jae('ov_skip_edge')
        # Scale indices to byte offsets (× 8). Both fit in eax/edx.
        a.raw(b'\xC1\xE0\x03')                                # shl eax, 3
        a.raw(b'\xC1\xE2\x03')                                # shl edx, 3

        # tmp_p1 = vertices[i] - cam (i = eax-scaled offset)
        # Use D9 04 05 disp32 form: fld dword [eax + disp32]
        a.raw(b'\xD9\x80' + le32(overlay_vertices_va))        # fld dword [eax + vertices]
        a.raw(b'\xD8\x25' + le32(overlay_cam_x_va))           # fsub dword [cam_x]
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va))          # fstp dword [tmp_p1]
        a.raw(b'\xD9\x80' + le32(overlay_vertices_va + 4))    # fld dword [eax + vertices+4]
        a.raw(b'\xD8\x25' + le32(overlay_cam_y_va))           # fsub dword [cam_y]
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va + 4))      # fstp dword [tmp_p1+4]

        # tmp_p2 = vertices[j] - cam
        a.raw(b'\xD9\x82' + le32(overlay_vertices_va))        # fld dword [edx + vertices]
        a.raw(b'\xD8\x25' + le32(overlay_cam_x_va))
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p2_va))
        a.raw(b'\xD9\x82' + le32(overlay_vertices_va + 4))    # fld dword [edx + vertices+4]
        a.raw(b'\xD8\x25' + le32(overlay_cam_y_va))
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p2_va + 4))

        _emit_segment_cull(
            a, overlay_tmp_p1_va, overlay_tmp_p2_va,
            overlay_cull_min_x_va, overlay_cull_max_x_va,
            overlay_cull_min_y_va, overlay_cull_max_y_va,
            'ov_skip_edge',
            'ov_edge_cull',
        )

        a.raw(b'\x68' + le32(overlay_edge_color_va))          # push &color
        a.raw(b'\x68' + le32(overlay_tmp_p2_va))              # push &p2
        a.raw(b'\x68' + le32(overlay_tmp_p1_va))              # push &p1
        a.raw(b'\x89\xF9')                                    # mov ecx, edi
        a.call_va(ax.SUB_4B3CB0_VA)

        a.label('ov_skip_edge')
        a.raw(b'\x46')                                        # inc esi
        a.jmp('ov_edge_loop')

    a.label('ov_popad_only')
    a.raw(b'\x61')                                            # popad

    a.label('ov_resume')
    a.raw(b'\xA0' + le32(ax.FULLSCREEN_FLAG_VA))              # mov al, byte_6210C0
    a.jmp_va(ax.S5693A0_RESUME)

    # Shared point-table draw helper used by pickup and portal overlays.
    # Inputs: EBP=table base (float x/y pairs), EBX=count, ECX=&CColor,
    # EDI=renderer. Clobbers EAX/EDX/ESI; preserves ECX across draw calls.
    a.label('ov_draw_point_table')
    a.raw(b'\x85\xDB'); a.jz('ov_dpt_ret')                    # no points
    a.raw(b'\x31\xF6')                                        # esi = idx
    a.label('ov_dpt_loop')
    a.raw(b'\x39\xDE'); a.jae('ov_dpt_ret')                   # idx >= count
    a.raw(b'\xD9\x44\xF5\x00')                                # fld [ebp + esi*8]
    a.raw(b'\xD8\x25' + le32(overlay_cam_x_va))               # fsub [cam_x]
    a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va))              # fstp [tmp_p1]
    a.raw(b'\xD9\x44\xF5\x04')                                # fld [ebp + esi*8 + 4]
    a.raw(b'\xD8\x25' + le32(overlay_cam_y_va))               # fsub [cam_y]
    a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va + 4))          # fstp [tmp_p1 + 4]
    _emit_point_cull(
        a, overlay_tmp_p1_va,
        overlay_cull_min_x_va, overlay_cull_max_x_va,
        overlay_cull_min_y_va, overlay_cull_max_y_va,
        'ov_dpt_next',
    )
    a.raw(b'\xBA' + le32(overlay_tmp_p1_va))                  # edx = &tmp_p1
    a.raw(b'\x51')                                            # save color ptr
    a.raw(b'\x51')                                            # push &color
    a.raw(b'\xFF\x35' + le32(overlay_vertex_aspect_va))       # push aspect
    a.raw(b'\xFF\x35' + le32(overlay_vertex_radius_va))       # push radius
    a.raw(b'\x89\xF9')                                        # ecx = renderer
    a.call_va(ax.SUB_4FCCC0_VA)
    a.raw(b'\x59')                                            # restore color ptr
    a.label('ov_dpt_next')
    a.raw(b'\x46')                                            # ++idx
    a.jmp('ov_dpt_loop')
    a.label('ov_dpt_ret')
    a.raw(b'\xC3')


def _emit_point_cull(a: Asm,
                     point_va: int,
                     min_x_va: int,
                     max_x_va: int,
                     min_y_va: int,
                     max_y_va: int,
                     skip_label: str) -> None:
    """Skip a point outside the expanded screen rect.

    Compares `point.x/y` after world->screen transform against static float
    bounds. Each `fcomp` pops the loaded value, so the x87 stack stays empty.
    """
    a.raw(b'\xD9\x05' + le32(point_va + 0))                   # fld [x]
    a.raw(b'\xD8\x1D' + le32(min_x_va))                       # fcomp [min_x]
    a.raw(b'\xDF\xE0\x9E')                                    # fnstsw ax; sahf
    a.jb(skip_label)                                          # x < min_x
    a.raw(b'\xD9\x05' + le32(point_va + 0))                   # fld [x]
    a.raw(b'\xD8\x1D' + le32(max_x_va))                       # fcomp [max_x]
    a.raw(b'\xDF\xE0\x9E')
    a.ja(skip_label)                                          # x > max_x
    a.raw(b'\xD9\x05' + le32(point_va + 4))                   # fld [y]
    a.raw(b'\xD8\x1D' + le32(min_y_va))                       # fcomp [min_y]
    a.raw(b'\xDF\xE0\x9E')
    a.jb(skip_label)                                          # y < min_y
    a.raw(b'\xD9\x05' + le32(point_va + 4))                   # fld [y]
    a.raw(b'\xD8\x1D' + le32(max_y_va))                       # fcomp [max_y]
    a.raw(b'\xDF\xE0\x9E')
    a.ja(skip_label)                                          # y > max_y


def _emit_segment_cull(a: Asm,
                       p1_va: int,
                       p2_va: int,
                       min_x_va: int,
                       max_x_va: int,
                       min_y_va: int,
                       max_y_va: int,
                       skip_label: str,
                       prefix: str) -> None:
    """Skip a line segment only when both endpoints are outside one side."""
    # Both endpoints left of the screen rect?
    a.raw(b'\xD9\x05' + le32(p1_va + 0))                      # fld [p1.x]
    a.raw(b'\xD8\x1D' + le32(min_x_va))                       # fcomp [min_x]
    a.raw(b'\xDF\xE0\x9E')
    a.jae(f'{prefix}_not_left')                               # p1.x >= min_x
    a.raw(b'\xD9\x05' + le32(p2_va + 0))                      # fld [p2.x]
    a.raw(b'\xD8\x1D' + le32(min_x_va))
    a.raw(b'\xDF\xE0\x9E')
    a.jb(skip_label)                                          # p2.x < min_x
    a.label(f'{prefix}_not_left')

    # Both endpoints right of the screen rect?
    a.raw(b'\xD9\x05' + le32(p1_va + 0))                      # fld [p1.x]
    a.raw(b'\xD8\x1D' + le32(max_x_va))                       # fcomp [max_x]
    a.raw(b'\xDF\xE0\x9E')
    a.jbe(f'{prefix}_not_right')                              # p1.x <= max_x
    a.raw(b'\xD9\x05' + le32(p2_va + 0))                      # fld [p2.x]
    a.raw(b'\xD8\x1D' + le32(max_x_va))
    a.raw(b'\xDF\xE0\x9E')
    a.ja(skip_label)                                          # p2.x > max_x
    a.label(f'{prefix}_not_right')

    # Both endpoints above the screen rect?
    a.raw(b'\xD9\x05' + le32(p1_va + 4))                      # fld [p1.y]
    a.raw(b'\xD8\x1D' + le32(min_y_va))                       # fcomp [min_y]
    a.raw(b'\xDF\xE0\x9E')
    a.jae(f'{prefix}_not_top')                                # p1.y >= min_y
    a.raw(b'\xD9\x05' + le32(p2_va + 4))                      # fld [p2.y]
    a.raw(b'\xD8\x1D' + le32(min_y_va))
    a.raw(b'\xDF\xE0\x9E')
    a.jb(skip_label)                                          # p2.y < min_y
    a.label(f'{prefix}_not_top')

    # Both endpoints below the screen rect?
    a.raw(b'\xD9\x05' + le32(p1_va + 4))                      # fld [p1.y]
    a.raw(b'\xD8\x1D' + le32(max_y_va))                       # fcomp [max_y]
    a.raw(b'\xDF\xE0\x9E')
    a.jbe(f'{prefix}_not_bottom')                             # p1.y <= max_y
    a.raw(b'\xD9\x05' + le32(p2_va + 4))                      # fld [p2.y]
    a.raw(b'\xD8\x1D' + le32(max_y_va))
    a.raw(b'\xDF\xE0\x9E')
    a.ja(skip_label)                                          # p2.y > max_y
    a.label(f'{prefix}_not_bottom')


def _split_rgba_static(role):
    if role == 'vertex':
        src = cfg.OVERLAY_VERTEX_COLOR
    elif role == 'edge':
        src = cfg.OVERLAY_EDGE_COLOR
    elif role == 'selected':
        src = cfg.OVERLAY_SELECTED_COLOR
    elif role == 'pickup':
        src = cfg.OVERLAY_PICKUP_COLOR
    elif role == 'portal':
        src = cfg.OVERLAY_PORTAL_COLOR
    else:
        raise ValueError(f'unknown overlay color role: {role!r}')
    return tuple(int(v) & 0xFF for v in src)
