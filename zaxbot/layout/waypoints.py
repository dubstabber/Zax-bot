"""Waypoint overlay vertex/edge tables, save/load state and the
weighted-routing (edge length) fields."""

from .model import ScratchField


def extend_wp_overlay(c):
    overlay_vertex_max = c.overlay_vertex_max
    overlay_edge_max = c.overlay_edge_max
    OVERLAY_BASE = c.OVERLAY_BASE
    OVERLAY_TABLE_OFF = c.OVERLAY_TABLE_OFF
    overlay_color_size = c.overlay_color_size
    overlay_edge_max_capped = c.overlay_edge_max_capped
    overlay_edge_stride = c.overlay_edge_stride
    overlay_fields = c.overlay_fields
    overlay_vertex_max_capped = c.overlay_vertex_max_capped
    overlay_vertex_stride = c.overlay_vertex_stride

    # --- Waypoint overlay state -------------------------------------------
    # Renderable waypoint set baked from cfg.OVERLAY_WAYPOINTS / EDGES at
    # build time. Capacity ceilings are passed in so detours/overlay.py can
    # iterate by the LIVE count fields without growing the section per
    # waypoint set. Anchored at 0x2000 to keep a clear visual gap from the
    # bot-AI scratch (ends near 0x1F4C) and survive future field churn.
    OVERLAY_BASE = 0x2080
    overlay_color_size = 16          # CColor struct (BGRA + palette idx + flags)
    overlay_vertex_stride = 8        # float[2] per vertex
    overlay_edge_stride   = 4        # u16[2] per edge
    overlay_vertex_max_capped = max(0, overlay_vertex_max)
    overlay_edge_max_capped   = max(0, overlay_edge_max)

    overlay_fields = [
        ScratchField('overlay_enabled',       OVERLAY_BASE + 0x00, 0x04,
                     'overlay: master enable flag (0 = skip detour body)'),
        ScratchField('overlay_vertex_color',  OVERLAY_BASE + 0x04, overlay_color_size,
                     'overlay: vertex CColor; rebuilt each frame by sub_53F010'),
        ScratchField('overlay_edge_color',    OVERLAY_BASE + 0x14, overlay_color_size,
                     'overlay: edge CColor; rebuilt each frame by sub_53F010'),
        ScratchField('overlay_vertex_radius', OVERLAY_BASE + 0x24, 0x04,
                     'overlay: oval radius (float, world-space pixels)'),
        ScratchField('overlay_vertex_aspect', OVERLAY_BASE + 0x28, 0x04,
                     'overlay: oval y/x aspect (float; 1.0 = circle)'),
        ScratchField('overlay_vertex_count',  OVERLAY_BASE + 0x2C, 0x04,
                     'overlay: live count <= overlay_vertex_max'),
        ScratchField('overlay_edge_count',    OVERLAY_BASE + 0x30, 0x04,
                     'overlay: live count <= overlay_edge_max'),
        ScratchField('overlay_renderer_tmp',  OVERLAY_BASE + 0x34, 0x04,
                     'overlay: cached renderer ptr for inner-loop reuse'),
        # Per-frame screen-edge camera read from the host's tracker layer
        # (`layer+0xC0/0xC4` floats), used to pre-transform world coords
        # to screen coords before passing to the engine. ``overlay_cam_ok``
        # is a sentinel: 0 means lookup failed and we should skip drawing.
        ScratchField('overlay_cam_x',         OVERLAY_BASE + 0x38, 0x04,
                     'overlay: screen-edge cam x (float, world coord of screen left)'),
        ScratchField('overlay_cam_y',         OVERLAY_BASE + 0x3C, 0x04,
                     'overlay: screen-edge cam y (float, world coord of screen top)'),
        ScratchField('overlay_cam_ok',        OVERLAY_BASE + 0x40, 0x04,
                     'overlay: 1 if cam_x/y are valid, 0 = skip draw'),
        ScratchField('overlay_tmp_p1',        OVERLAY_BASE + 0x44, 0x08,
                     'overlay: float[2] screen p1 for line/oval draw'),
        ScratchField('overlay_tmp_p2',        OVERLAY_BASE + 0x4C, 0x08,
                     'overlay: float[2] screen p2 for line draw'),
        # Waypoint-editor state. wp_selected_idx is the "cursor": index into
        # overlay_vertices of the currently-selected node, or 0xFFFFFFFF for
        # no selection. Auto-set to the new node by wp_drop and to the nearest
        # node by wp_select; consumed by wp_drop (auto-edge source) and by the
        # overlay draw pass (highlight render). wp_scratch is 8 bytes of
        # per-call scratch for the position read used by wp_drop/select/delete.
        ScratchField('wp_selected_idx',       OVERLAY_BASE + 0x54, 0x04,
                     'waypoint edit: selected node index (0xFFFFFFFF = none)'),
        ScratchField('wp_scratch',            OVERLAY_BASE + 0x58, 0x08,
                     'waypoint edit: float[2] for sub_4FB0A0 host-pos reads'),
        ScratchField('overlay_selected_color', OVERLAY_BASE + 0x60, overlay_color_size,
                     'overlay: selected-vertex CColor (rebuilt per-frame)'),
        ScratchField('wp_snap_radius_sq',     OVERLAY_BASE + 0x70, 0x04,
                     'waypoint edit: snap radius² (float, world units)'),
        # 1 while the overlay draw pass owns the batched DirectDraw back-
        # buffer lock (sub_567BB0 at pass start / sub_567C90 before resuming
        # into the flip). Without the batch every line — and every oval
        # SEGMENT, 10-25 per oval — pays its own Lock/Unlock inside
        # sub_568D90, a per-call GPU/GDI sync on native Windows (the 2-6 FPS
        # overlay collapse; Wine's system-memory surfaces hid it on Linux).
        ScratchField('overlay_locked',        OVERLAY_BASE + 0x74, 0x04,
                     'overlay: 1 = draw pass holds the batched surface lock'),
    ]
    # Vertex / edge tables start at +0x80 (after the per-frame scratch
    # above, including waypoint-editor state) so growing the fix-up state
    # doesn't shift the tables.
    OVERLAY_TABLE_OFF = 0x80
    if overlay_vertex_max_capped > 0:
        overlay_fields.append(ScratchField(
            'overlay_vertices', OVERLAY_BASE + OVERLAY_TABLE_OFF,
            overlay_vertex_max_capped * overlay_vertex_stride,
            'overlay: float[2] per vertex (world coords)',
        ))
    if overlay_edge_max_capped > 0:
        overlay_edge_off = OVERLAY_BASE + OVERLAY_TABLE_OFF + overlay_vertex_max_capped * overlay_vertex_stride
        overlay_fields.append(ScratchField(
            'overlay_edges', overlay_edge_off,
            overlay_edge_max_capped * overlay_edge_stride,
            'overlay: (u16 i, u16 j) per edge; indices into overlay_vertices',
        ))

    c.OVERLAY_BASE = OVERLAY_BASE
    c.OVERLAY_TABLE_OFF = OVERLAY_TABLE_OFF
    c.overlay_color_size = overlay_color_size
    c.overlay_edge_max_capped = overlay_edge_max_capped
    c.overlay_edge_stride = overlay_edge_stride
    c.overlay_fields = overlay_fields
    c.overlay_vertex_max_capped = overlay_vertex_max_capped
    c.overlay_vertex_stride = overlay_vertex_stride



def extend_wp_saveload(c):
    OVERLAY_BASE = c.OVERLAY_BASE
    OVERLAY_TABLE_OFF = c.OVERLAY_TABLE_OFF
    overlay_edge_max_capped = c.overlay_edge_max_capped
    overlay_edge_stride = c.overlay_edge_stride
    overlay_fields = c.overlay_fields
    overlay_vertex_max_capped = c.overlay_vertex_max_capped
    overlay_vertex_stride = c.overlay_vertex_stride
    wp_io_off = c.wp_io_off


    # --- Waypoint save/load state (lives after the vertex/edge tables) ----
    # Filename buffer holds the dynamically-built "waypoints/<map>.zwpt"
    # path (resolved per save/load from MAP_NAME_CSTRING_VA). The static
    # prefix / suffix / dir-name strings are initialised from cfg and copied
    # by the asm into the buffer. wp_file_header is the 16-byte staging
    # area for the file's magic+version+counts; wp_io_count is the
    # lpNumberOfBytesTransferred receiver for ReadFile/WriteFile calls.
    wp_io_off = OVERLAY_BASE + OVERLAY_TABLE_OFF
    if overlay_vertex_max_capped > 0:
        wp_io_off += overlay_vertex_max_capped * overlay_vertex_stride
    if overlay_edge_max_capped > 0:
        wp_io_off += overlay_edge_max_capped * overlay_edge_stride
    overlay_fields.extend([
        ScratchField('wp_filename_buf', wp_io_off + 0x00, 0x100,
                     'waypoint io: dynamically-built file path'),
        ScratchField('wp_file_header',  wp_io_off + 0x100, 0x10,
                     'waypoint io: 16B header staging (magic+version+counts)'),
        ScratchField('wp_io_count',     wp_io_off + 0x110, 0x04,
                     'waypoint io: lpNumberOfBytesTransferred for Read/WriteFile'),
        ScratchField('wp_dir_static',   wp_io_off + 0x120, 0x20,
                     'waypoint io: static "waypoints" dir name for CreateDirectoryA'),
        ScratchField('wp_prefix_static', wp_io_off + 0x140, 0x20,
                     'waypoint io: static "waypoints/" path prefix'),
        ScratchField('wp_suffix_static', wp_io_off + 0x160, 0x10,
                     'waypoint io: static ".zwpt" path suffix'),
        ScratchField('wp_msg_saved',    wp_io_off + 0x170, 0x20,
                     'waypoint io: on-screen msg shown after save'),
        ScratchField('wp_msg_loaded',   wp_io_off + 0x190, 0x20,
                     'waypoint io: on-screen msg shown after auto-load'),
        ScratchField('wp_msg_nomap',    wp_io_off + 0x1B0, 0x20,
                     'waypoint io: on-screen msg shown when map name is empty'),
        ScratchField('wp_msg_failed',   wp_io_off + 0x1D0, 0x20,
                     'waypoint io: on-screen msg shown on save/load failure'),
    ])

    c.wp_io_off = wp_io_off



def extend_weighted_routing(c):
    fields = c.fields
    overlay_edge_max_capped = c.overlay_edge_max_capped
    overlay_fields = c.overlay_fields
    overlay_vertex_max_capped = c.overlay_vertex_max_capped


    # --- Weighted routing (physical-length BFS) ------------------------------
    # Appended at the very tail. edge_len[e] = round(edge pixel length /
    # elen_quantum), min 1 — the traversal cost bfs_run adds per edge (the
    # SPFA conversion; hop counting was live-refuted on Hydroplant
    # Bouncefest where the door route and the around route tie at 9 hops
    # but differ by 681 px). bfs_inq is the SPFA in-queue byte per node
    # (re-enqueue dedup; cleared at each bfs_run entry).
    if overlay_edge_max_capped > 0 and overlay_vertex_max_capped > 0:
        wlen_base = max(
            [f.end for f in fields] + [f.end for f in overlay_fields]
        )
        wlen_base = (wlen_base + 7) & ~7
        overlay_fields.extend([
            ScratchField('edge_len', wlen_base,
                         overlay_edge_max_capped * 4,
                         'route: per-edge quantized physical length (bfs_run edge cost, min 1)'),
            ScratchField('bfs_inq',
                         wlen_base + overlay_edge_max_capped * 4,
                         overlay_vertex_max_capped,
                         'route: SPFA in-queue flag per node (bfs_run re-enqueue dedup)'),
            ScratchField('elen_quantum',
                         wlen_base + overlay_edge_max_capped * 4
                         + overlay_vertex_max_capped, 0x04,
                         'route: float px per distance unit (build_edge_lens divisor)'),
        ])


