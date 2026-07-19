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

import struct

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
    overlay_flag_color_va     = layout.va('overlay_flag_color') if layout.has_field('overlay_flag_color') else 0
    flag_count_va             = layout.va('flag_count') if layout.has_field('flag_count') else 0
    flag_table_va             = layout.va('flag_table') if layout.has_field('flag_table') else 0
    flag_entity_va            = layout.va('flag_entity') if layout.has_field('flag_entity') else 0
    flag_present_va           = layout.va('flag_present') if layout.has_field('flag_present') else 0
    flag_home_tick_radius_va  = layout.va('flag_home_tick_radius_sq') if layout.has_field('flag_home_tick_radius_sq') else 0
    route_carry_va            = layout.va('route_carry') if layout.has_field('route_carry') else 0
    route_goal_va             = layout.va('route_goal_flag') if layout.has_field('route_goal_flag') else 0
    overlay_door_color_va     = layout.va('overlay_door_color') if layout.has_field('overlay_door_color') else 0
    door_count_va             = layout.va('door_count') if layout.has_field('door_count') else 0
    door_table_va             = layout.va('door_table') if layout.has_field('door_table') else 0
    door_blocked_va           = layout.va('door_blocked') if layout.has_field('door_blocked') else 0
    overlay_switch_color_va   = layout.va('overlay_switch_color') if layout.has_field('overlay_switch_color') else 0
    switch_count_va           = layout.va('switch_count') if layout.has_field('switch_count') else 0
    switch_table_va           = layout.va('switch_table') if layout.has_field('switch_table') else 0
    switch_flags_va           = layout.va('switch_flags') if layout.has_field('switch_flags') else 0

    vr, vg, vb, va_ = _split_rgba_static('vertex')
    er, eg, eb, ea  = _split_rgba_static('edge')
    sr, sg, sb, sa  = _split_rgba_static('selected')
    pr, pg, pb, pa  = _split_rgba_static('pickup')
    tr, tg, tb, ta  = _split_rgba_static('portal')
    fr, fg, fb, fa  = _split_rgba_static('flag')
    dr, dg, db, da  = _split_rgba_static('door')
    wr, wg, wb, wa  = _split_rgba_static('switch')

    a.label('detour_5693A0')
    # Per-frame tick — bumped here (the one reliable once-per-frame site, the
    # page flip) BEFORE the overlay_enabled gate so the pickup table's lazy
    # reset keeps working even when the overlay itself is disabled.
    a.raw(b'\xFF\x05' + le32(world_frame_va))                 # ++world_frame
    # Keep bots simulated when far from the host's camera. The engine advances a
    # char's components (incl. the bot walking-controller think) only while the
    # char's ACTIVE bit (char+0x1C & ENTITY_ACTIVE_BIT) is set, and it
    # DEACTIVATES entities far from the local camera (a sticky one-shot
    # transition). Re-set each live bot char's Active bit every frame so the
    # engine keeps ticking bots everywhere (in-context, exactly once per frame).
    # Cheap fixed 16-slot loop, NOT a per-entity hot path. Runs before the
    # overlay gate so it works with the overlay hidden. bot_indices[slot] is the
    # bot's index into the mgr+0x290 char array (0 / unused -> host char, whose
    # Active bit is always set anyway, so the spurious set is a harmless no-op).
    if cfg.BOT_FORCE_ACTIVE_ENABLED:
        bot_indices_va = layout.va('bot_indices')
        bot_participants_va = layout.va('bot_participants')
        a.raw(b'\x60')                                        # pushad
        a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))           # eax = [mgr]
        a.raw(b'\x85\xC0'); a.jz('ov_ba_done')
        a.raw(b'\x8B\x98\x94\x02\x00\x00')                    # ebx = [eax+0x294] char count
        a.raw(b'\x8B\x80\x90\x02\x00\x00')                    # eax = [eax+0x290] char array
        a.raw(b'\x85\xC0'); a.jz('ov_ba_done')
        a.raw(b'\x89\xC5')                                    # ebp = char array base
        a.raw(b'\x31\xF6')                                    # esi = 0 (slot)
        a.label('ov_ba_loop')
        a.raw(b'\x8B\x14\xB5' + le32(bot_indices_va))         # edx = bot_indices[slot]
        a.raw(b'\x39\xDA')                                    # cmp edx, ebx (idx vs count)
        a.jae('ov_ba_next')                                   # idx >= count -> skip (unused/garbage)
        a.raw(b'\x8B\x44\x95\x00')                            # eax = [ebp + edx*4] (char)
        a.raw(b'\x85\xC0'); a.jz('ov_ba_next')                # NULL char
        a.raw(b'\x81\x48' + bytes([ax.ENTITY_FLAGS_OFF])
              + le32(ax.ENTITY_ACTIVE_BIT))                   # or [char+0x1C], ACTIVE_BIT
        if cfg.BOT_PARTICIPANT_POS_ENABLED:
            # Mirror the bot char's live position into its PARTICIPANT
            # (+0xC0/+0xC4) so the engine's own MP update (sub_4F37E0 ->
            # sub_4EA350) builds an activation rect around the bot like it
            # does for every real connected player (clients stream this pair
            # over DirectPlay; bots have no client, so it froze at (0,0) and
            # nothing near a far bot was ever simulated). idx==0 is the
            # host/unused sentinel — the host's participant is engine-owned.
            a.raw(b'\x85\xD2'); a.jz('ov_ba_next')            # idx==0 -> no bot participant
            a.raw(b'\x8B\x0C\xB5' + le32(bot_participants_va))  # ecx = bot_participants[slot]
            a.raw(b'\x85\xC9'); a.jz('ov_ba_next')            # NULL participant
            a.raw(b'\x8B\x50' + bytes([ax.ENTITY_POS_X_OFF]))  # edx = [char+0x4C] (x)
            a.raw(b'\x89\x91' + le32(ax.PART_POS_X_OFF))      # [part+0xC0] = x
            a.raw(b'\x8B\x50' + bytes([ax.ENTITY_POS_Y_OFF]))  # edx = [char+0x50] (y)
            a.raw(b'\x89\x91' + le32(ax.PART_POS_Y_OFF))      # [part+0xC4] = y
        a.label('ov_ba_next')
        a.raw(b'\x46')                                        # inc esi
        a.raw(b'\x83\xFE' + bytes([cfg.MAX_BOT_SLOTS]))       # cmp esi, MAX_BOT_SLOTS
        a.jb('ov_ba_loop')
        a.label('ov_ba_done')
        a.raw(b'\x61')                                        # popad
    # Force-tick bots the engine SKIPPED this frame. sub_57A030, the normal
    # active-entity driver, runs three entity vtable stages (+0x7C, +0x80,
    # +0x8C) with EBP=0x10000; the +0x8C player path then calls the component
    # advance and commits the pending walking-controller movement back to the
    # char position. Calling only sub_4FADC0 reaches our controller but leaves
    # that position-sync stage skipped, so far bots think but do not move.
    # bot_ticked=2 during that recovery tick suppresses out-of-band fire, and
    # we reset it to 0 here every frame so near bots are never double-ticked.
    # idx==0 (host/unused) is skipped so the host is never force-ticked.
    if cfg.BOT_FORCE_TICK_ENABLED and layout.has_field('bot_last_item_scan'):
        bot_indices_va = layout.va('bot_indices')
        bot_ticked_va = layout.va('bot_last_item_scan')
        a.raw(b'\x60')                                        # pushad
        a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))           # eax = [mgr]
        a.raw(b'\x85\xC0'); a.jz('ov_ft_done')
        a.raw(b'\x8B\x98\x94\x02\x00\x00')                    # ebx = [eax+0x294] char count
        a.raw(b'\x8B\x80\x90\x02\x00\x00')                    # eax = [eax+0x290] char array
        a.raw(b'\x85\xC0'); a.jz('ov_ft_done')
        a.raw(b'\x89\xC5')                                    # ebp = char array base
        a.raw(b'\x31\xF6')                                    # esi = 0 (slot)
        a.label('ov_ft_loop')
        a.raw(b'\x8B\x14\xB5' + le32(bot_indices_va))         # edx = bot_indices[slot]
        a.raw(b'\x85\xD2'); a.jz('ov_ft_next')                # idx==0 -> host/unused -> skip
        a.raw(b'\x39\xDA'); a.jae('ov_ft_next')               # idx >= count -> skip
        a.raw(b'\x8B\x44\x95\x00')                            # eax = [ebp + edx*4] (char)
        a.raw(b'\x85\xC0'); a.jz('ov_ft_next')                # NULL char -> skip
        # Force-tick only when the engine did not tick this bot's controller.
        a.raw(b'\x83\x3C\xB5' + le32(bot_ticked_va) + b'\x00')  # cmp [bot_ticked + esi*4], 0
        a.jnz('ov_ft_reset')                                  # engine ticked controller -> skip force-call
        a.raw(b'\xC7\x04\xB5' + le32(bot_ticked_va) + le32(2))  # recovery tick sentinel
        a.raw(b'\x89\xC7')                                    # mov edi, eax (char)
        a.raw(b'\xF7\x47' + bytes([ax.ENTITY_FLAGS_OFF])
              + le32(ax.ENTITY_SKIP_UPDATE_BIT))              # test [char+flags], skip-update bit
        a.jnz('ov_ft_reset')                                  # pending-delete/no-update -> skip
        a.raw(b'\x55')                                        # push ebp (loop uses ebp=char array)
        a.raw(b'\xBD' + le32(ax.ENTITY_SKIP_UPDATE_BIT))       # ebp = 0x10000 (sub_57A030 context)
        a.raw(b'\x68\x89\x88\x88\x3C')                        # push 0x3C888889 (1/60 float = dt)
        a.raw(b'\x89\xF9')                                    # mov ecx, edi (char)
        a.raw(b'\x8B\x17')                                    # mov edx, [edi]
        a.raw(b'\xFF\x52' + bytes([ax.ENTITY_TICK_PRE1_VTBL_OFF]))  # call [edx + 0x7C]; ret 4
        a.raw(b'\xF7\x47' + bytes([ax.ENTITY_FLAGS_OFF])
              + le32(ax.ENTITY_SKIP_UPDATE_BIT))              # test [char+flags], skip-update bit
        a.jnz('ov_ft_restore_ebp')
        a.raw(b'\x68\x89\x88\x88\x3C')                        # push dt
        a.raw(b'\x89\xF9')                                    # mov ecx, edi
        a.raw(b'\x8B\x17')                                    # mov edx, [edi]
        a.raw(b'\xFF\x92' + le32(ax.ENTITY_TICK_PRE2_VTBL_OFF))  # call [edx + 0x80]; ret 4
        a.raw(b'\xF7\x47' + bytes([ax.ENTITY_FLAGS_OFF])
              + le32(ax.ENTITY_SKIP_UPDATE_BIT))              # test [char+flags], skip-update bit
        a.jnz('ov_ft_restore_ebp')
        a.raw(b'\x68\x89\x88\x88\x3C')                        # push dt
        a.raw(b'\x89\xF9')                                    # mov ecx, edi
        a.raw(b'\x8B\x17')                                    # mov edx, [edi]
        a.raw(b'\xFF\x92' + le32(ax.ENTITY_TICK_MAIN_VTBL_OFF))  # call [edx + 0x8C]; ret 4
        a.label('ov_ft_restore_ebp')
        a.raw(b'\x5D')                                        # pop ebp
        a.label('ov_ft_reset')
        a.raw(b'\xC7\x04\xB5' + le32(bot_ticked_va) + le32(0))  # bot_ticked[slot] = 0
        a.label('ov_ft_next')
        a.raw(b'\x46')                                        # inc esi
        a.raw(b'\x83\xFE' + bytes([cfg.MAX_BOT_SLOTS]))       # cmp esi, MAX_BOT_SLOTS
        a.jb('ov_ft_loop')
        a.label('ov_ft_done')
        a.raw(b'\x61')                                        # popad
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
        # Dropped-flag routing rows: the scan above is the only thing that
        # changes flag_drop_node mid-match, so refresh the per-drop BFS rows
        # right behind it (no-op unless a drop appeared/moved). pushad/popad;
        # inert stub on builds without the drop-routing fields.
        if cfg.CTF_DROPPED_FLAG_ENABLED and layout.has_field('drop_route_root'):
            a.call_lbl('drop_route_refresh')
        a.label('ov_pa_skip')
    # Per-frame door state refresh + debounced open-route rebuild. The grid
    # scan above only maintains the door_entity anchor cache; the SOLID bit is
    # re-read here EVERY frame so door_blocked[] (overlay rings, failed-edge
    # fast retry, routing) can never go stale with the FPS-dependent scan
    # interval (live-reported: rings froze while the visible overlay tanked
    # FPS and 120 scan-frames stretched to many seconds). When any door
    # flips, door_dirty arms a rebuild of the closed-door-excluding BFS field
    # (flag_dist_open) — debounced by DOOR_ROUTE_REBUILD_COOLDOWN_FRAMES since
    # touch-open-door maps flip state constantly.
    if cfg.DOOR_DETECT_ENABLED and layout.has_field('door_entity'):
        a.call_lbl('door_refresh_state')
        if (cfg.DOOR_ROUTE_AWARE_ENABLED
                and layout.has_field('flag_dist_open')
                and layout.has_field('door_dirty')
                and layout.has_field('flag_routing_active')):
            door_dirty_va      = layout.va('door_dirty')
            door_rebuild_cd_va = layout.va('door_rebuild_cd')
            a.raw(b'\xA1' + le32(door_rebuild_cd_va))         # eax = cooldown
            a.raw(b'\x85\xC0'); a.jz('ov_door_cd0')
            a.raw(b'\x48')                                    # dec eax
            a.raw(b'\xA3' + le32(door_rebuild_cd_va))         # store back
            a.jmp('ov_door_rr_done')
            a.label('ov_door_cd0')
            a.raw(b'\x83\x3D' + le32(door_dirty_va) + b'\x00')  # dirty?
            a.jz('ov_door_rr_done')
            a.raw(b'\xC7\x05' + le32(door_dirty_va) + le32(0))
            a.raw(b'\xC7\x05' + le32(door_rebuild_cd_va)
                  + le32(max(1, cfg.DOOR_ROUTE_REBUILD_COOLDOWN_FRAMES)))
            a.raw(b'\x83\x3D' + le32(layout.va('flag_routing_active')) + b'\x00')
            a.jz('ov_door_rr_done')                           # non-CTF: state only
            a.call_lbl('rebuild_open_routes')
            a.label('ov_door_rr_done')
    # Switch-seek servicing: tick active seek timeouts and evaluate at most
    # ONE pending candidate (one bounded BFS) per frame. Self-gated on
    # flag_routing_active inside; inert stub on non-seek builds.
    if cfg.SWITCH_SEEK_ENABLED and layout.has_field('seek_active'):
        a.call_lbl('switch_seek_eval')
    # SK pile-ring TTL: age each registered death-pile entry once per frame
    # so stale (human-grabbed) piles expire. Register-preserving 8-slot loop;
    # inert stub on non-SK builds. The routing-field refresh runs right
    # behind it: a TTL expiry (or registration / grab) sets sk_pile_dirty
    # and the refresh reseeds the multi-source pile field — one bounded SPFA
    # only on pile events, never per frame.
    if cfg.SK_ENABLED and layout.has_field('sk_pile_valid'):
        a.call_lbl('sk_pile_tick')
        if layout.has_field('sk_pile_dirty'):
            a.call_lbl('sk_pile_route_refresh')
    # Far CTF capture support. The bot force-tick above keeps the carrier
    # moving, but capture itself is driven by the base "checker" trigger at the
    # destination. Those entities are also camera-gated by the engine, so a bot
    # standing on its far home base can keep carrying until the host walks close
    # enough to wake that area. scan_portal_active caches the exact-anchor
    # entities (checker / spawn marker / recreated flag) in flag_entity[].
    # Only tick a carrier's HOME base while flag_present[goal] says the home
    # flag is at base. flag_present[] is EVENT-driven (the CActivateAction /
    # CDeactivateAction apply detours mirror the map script's checker state),
    # so it flips the moment a steal deactivates the checker — this path can
    # therefore never re-arm a script-deactivated checker, which is the
    # engine's entire "your flag must be home to score" enforcement.
    if (cfg.BOT_FORCE_TICK_ENABLED and flag_entity_va and flag_home_tick_radius_va
            and route_carry_va and route_goal_va and flag_table_va and flag_count_va
            and flag_present_va):
        bot_indices_va = layout.va('bot_indices')
        bot_slot_tmp_va = layout.va('bot_slot_tmp')
        bot_char_tmp_va = layout.va('bot_char_tmp')
        d2_va = layout.va('scan_d2') if layout.has_field('scan_d2') else layout.va('curr_dist_sq')
        a.raw(b'\x60')                                        # pushad
        a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))           # eax = [mgr]
        a.raw(b'\x85\xC0'); a.jz('ov_ff_done')
        a.raw(b'\x8B\x98\x94\x02\x00\x00')                    # ebx = [mgr+0x294] char count
        a.raw(b'\x8B\x80\x90\x02\x00\x00')                    # eax = [mgr+0x290] char array
        a.raw(b'\x85\xC0'); a.jz('ov_ff_done')
        a.raw(b'\x89\xC5')                                    # ebp = char array base
        a.raw(b'\x31\xF6')                                    # esi = slot
        a.label('ov_ff_loop')
        a.raw(b'\x8B\x14\xB5' + le32(bot_indices_va))         # edx = bot_indices[slot]
        a.raw(b'\x85\xD2'); a.jz('ov_ff_next')                # idx==0 -> host/unused
        a.raw(b'\x39\xDA'); a.jae('ov_ff_next')               # idx >= count -> skip
        a.raw(b'\x8B\x7C\x95\x00')                            # edi = [char_array + idx*4]
        a.raw(b'\x85\xFF'); a.jz('ov_ff_next')
        a.raw(b'\x89\x35' + le32(bot_slot_tmp_va))            # bot_slot_tmp = esi
        a.raw(b'\x89\x3D' + le32(bot_char_tmp_va))            # bot_char_tmp = edi
        a.raw(b'\x53\x56\x57')                                # save ebx/esi/edi across helper
        a.call_lbl('ctf_pick_goal')                           # route_carry + route_goal_flag
        a.raw(b'\x5F\x5E\x5B')                                # restore edi/esi/ebx
        a.raw(b'\x83\x3D' + le32(route_carry_va) + b'\x00')   # carrying?
        a.jz('ov_ff_next')
        a.raw(b'\xA1' + le32(route_goal_va))                  # eax = goal flag idx
        a.raw(b'\x83\xF8\xFF'); a.jz('ov_ff_next')
        a.raw(b'\x3B\x05' + le32(flag_count_va))              # goal >= flag_count?
        a.jae('ov_ff_next')
        a.raw(b'\x83\x3C\x85' + le32(flag_present_va) + b'\x00')  # flag_present[goal]?
        a.jz('ov_ff_next')                                    # home flag away -> no capture tick
        # d2 = (flag[goal] - botchar.pos)^2
        a.raw(b'\xD9\x04\xC5' + le32(flag_table_va))          # fld flag.x
        a.raw(b'\xD8\x67\x4C')                                # fsub [botchar+0x4C]
        a.raw(b'\xD8\xC8')                                    # fmul st,st
        a.raw(b'\xD9\x04\xC5' + le32(flag_table_va + 4))      # fld flag.y
        a.raw(b'\xD8\x67\x50')                                # fsub [botchar+0x50]
        a.raw(b'\xD8\xC8')                                    # fmul st,st
        a.raw(b'\xDE\xC1')                                    # faddp -> st0 = d2
        a.raw(b'\xD9\x15' + le32(d2_va))                      # fst [d2] (keep d2)
        a.raw(b'\xD8\x1D' + le32(flag_home_tick_radius_va))   # fcomp [radius] (pop)
        a.raw(b'\xDF\xE0\x9E')                                # fnstsw ax; sahf
        a.ja('ov_ff_next')                                    # too far from home base
        # Tick every cached exact-anchor entity for this flag base, if present.
        flag_entity_slots = max(1, cfg.FLAG_ENTITY_SLOTS_PER_FLAG)
        for k in range(flag_entity_slots):
            a.raw(b'\x8B\x15' + le32(route_goal_va))          # edx = goal
            a.raw(b'\x6B\xD2' + bytes([flag_entity_slots * 4]))  # imul edx, edx, slots*4
            a.raw(b'\x8B\x92' + le32(flag_entity_va + 4 * k))  # edx = flag_entity[goal*slots + k]
            _emit_force_tick_entity_from_edx(a, f'ov_ff_e{k}', f'ov_ff_after_e{k}')
            a.label(f'ov_ff_after_e{k}')
        a.label('ov_ff_next')
        a.raw(b'\x46')                                        # inc esi
        a.raw(b'\x83\xFE' + bytes([cfg.MAX_BOT_SLOTS]))       # cmp esi, MAX_BOT_SLOTS
        a.jb('ov_ff_loop')
        a.label('ov_ff_done')
        a.raw(b'\x61')                                        # popad
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

    # --- Build flag-marker color (consumed by the flag pass) --------------
    if overlay_flag_color_va:
        a.raw(b'\x6A' + bytes([fa]))
        a.raw(b'\x6A' + bytes([fb]))
        a.raw(b'\x6A' + bytes([fg]))
        a.raw(b'\x6A' + bytes([fr]))
        a.raw(b'\xB9' + le32(overlay_flag_color_va))
        a.call_va(ax.SUB_53F010_VA)

    # --- Build door-marker color (consumed by the door pass) --------------
    if overlay_door_color_va:
        a.raw(b'\x6A' + bytes([da]))
        a.raw(b'\x6A' + bytes([db]))
        a.raw(b'\x6A' + bytes([dg]))
        a.raw(b'\x6A' + bytes([dr]))
        a.raw(b'\xB9' + le32(overlay_door_color_va))
        a.call_va(ax.SUB_53F010_VA)

    # --- Build switch-marker color (consumed by the switch pass) ----------
    if overlay_switch_color_va:
        a.raw(b'\x6A' + bytes([wa]))
        a.raw(b'\x6A' + bytes([wb]))
        a.raw(b'\x6A' + bytes([wg]))
        a.raw(b'\x6A' + bytes([wr]))
        a.raw(b'\xB9' + le32(overlay_switch_color_va))
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

    # --- Draw detected CTF flag bases ------------------------------------
    # Populated once per match by load_flags from static Data.dat-derived map
    # data. Same world->screen + oval path as portals, distinct color so flag
    # detection can be verified in-game.
    if flag_table_va and overlay_flag_color_va:
        a.raw(b'\xBD' + le32(flag_table_va))                 # ebp = table base
        a.raw(b'\x8B\x1D' + le32(flag_count_va))             # ebx = count
        a.raw(b'\xB9' + le32(overlay_flag_color_va))         # ecx = color
        a.call_lbl('ov_draw_point_table')

    # --- Draw dropped flags -------------------------------------------------
    # A valid dropped-copy position (flag_drop_valid[i], maintained by the
    # periodic grid walk's name match) renders as an oval PLUS a double-radius
    # ring at the drop spot — the ring distinguishes it from the single-oval
    # base anchors above (in the 8-bit palettized mode all B=255 markers share
    # one hue, so the ring is the signal, as with closed doors).
    if (flag_table_va and overlay_flag_color_va
            and layout.has_field('flag_drop_valid')
            and layout.has_field('flag_drop_pos')):
        flag_drop_valid_ov_va = layout.va('flag_drop_valid')
        flag_drop_pos_ov_va   = layout.va('flag_drop_pos')
        drop_ring_radius = struct.unpack(
            '<I', struct.pack('<f', cfg.OVERLAY_VERTEX_RADIUS * 2.0))[0]
        a.raw(b'\x31\xF6')                                    # esi = flag idx
        a.raw(b'\x8B\x1D' + le32(flag_count_va))              # ebx = count
        a.raw(b'\x85\xDB'); a.jz('ov_after_drops')
        a.label('ov_drop_loop')
        a.raw(b'\x39\xDE'); a.jae('ov_after_drops')           # idx >= count
        a.raw(b'\x83\x3C\xB5' + le32(flag_drop_valid_ov_va) + b'\x00')
        a.jz('ov_drop_next')                                  # no drop known
        a.raw(b'\xD9\x04\xF5' + le32(flag_drop_pos_ov_va))    # fld drop.x
        a.raw(b'\xD8\x25' + le32(overlay_cam_x_va))           # fsub cam.x
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va))          # fstp p1.x
        a.raw(b'\xD9\x04\xF5' + le32(flag_drop_pos_ov_va + 4))  # fld drop.y
        a.raw(b'\xD8\x25' + le32(overlay_cam_y_va))           # fsub cam.y
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va + 4))      # fstp p1.y
        _emit_point_cull(
            a, overlay_tmp_p1_va,
            overlay_cull_min_x_va, overlay_cull_max_x_va,
            overlay_cull_min_y_va, overlay_cull_max_y_va,
            'ov_drop_next',
        )
        a.raw(b'\xBA' + le32(overlay_tmp_p1_va))              # edx = &p1
        a.raw(b'\x68' + le32(overlay_flag_color_va))          # push &color
        a.raw(b'\xFF\x35' + le32(overlay_vertex_aspect_va))   # push aspect
        a.raw(b'\xFF\x35' + le32(overlay_vertex_radius_va))   # push radius
        a.raw(b'\x89\xF9')                                    # ecx = renderer
        a.call_va(ax.SUB_4FCCC0_VA)
        a.raw(b'\xBA' + le32(overlay_tmp_p1_va))              # edx = &p1
        a.raw(b'\x68' + le32(overlay_flag_color_va))          # push &color
        a.raw(b'\xFF\x35' + le32(overlay_vertex_aspect_va))   # push aspect
        a.raw(b'\x68' + le32(drop_ring_radius))               # push 2x radius (imm float bits)
        a.raw(b'\x89\xF9')                                    # ecx = renderer
        a.call_va(ax.SUB_4FCCC0_VA)
        a.label('ov_drop_next')
        a.raw(b'\x46')                                        # ++idx
        a.jmp('ov_drop_loop')
        a.label('ov_after_drops')

    # --- Draw detected doors ----------------------------------------------
    # Populated once per match by load_doors from static Data.dat-derived map
    # data; door_blocked[] is refreshed by the periodic grid scan. Every door
    # renders as a small oval; a CLOSED (SOLID) door additionally gets a
    # double-radius ring so open/closed state is verifiable at a glance —
    # in the 8-bit palettized mode all B=255 markers share one hue, so the
    # ring, not the color, is the state signal.
    if door_table_va and door_blocked_va and overlay_door_color_va:
        door_ring_radius = struct.unpack(
            '<I', struct.pack('<f', cfg.OVERLAY_VERTEX_RADIUS * 2.0))[0]
        a.raw(b'\x31\xF6')                                    # esi = door idx
        a.raw(b'\x8B\x1D' + le32(door_count_va))              # ebx = count
        a.raw(b'\x85\xDB'); a.jz('ov_after_doors')
        a.label('ov_door_loop')
        a.raw(b'\x39\xDE'); a.jae('ov_after_doors')           # idx >= count
        a.raw(b'\xD9\x04\xF5' + le32(door_table_va))          # fld door.x
        a.raw(b'\xD8\x25' + le32(overlay_cam_x_va))           # fsub cam.x
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va))          # fstp p1.x
        a.raw(b'\xD9\x04\xF5' + le32(door_table_va + 4))      # fld door.y
        a.raw(b'\xD8\x25' + le32(overlay_cam_y_va))           # fsub cam.y
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va + 4))      # fstp p1.y
        _emit_point_cull(
            a, overlay_tmp_p1_va,
            overlay_cull_min_x_va, overlay_cull_max_x_va,
            overlay_cull_min_y_va, overlay_cull_max_y_va,
            'ov_door_next',
        )
        a.raw(b'\xBA' + le32(overlay_tmp_p1_va))              # edx = &p1
        a.raw(b'\x68' + le32(overlay_door_color_va))          # push &color
        a.raw(b'\xFF\x35' + le32(overlay_vertex_aspect_va))   # push aspect
        a.raw(b'\xFF\x35' + le32(overlay_vertex_radius_va))   # push radius
        a.raw(b'\x89\xF9')                                    # ecx = renderer
        a.call_va(ax.SUB_4FCCC0_VA)
        a.raw(b'\x83\x3C\xB5' + le32(door_blocked_va) + b'\x00')  # closed?
        a.jz('ov_door_next')
        a.raw(b'\xBA' + le32(overlay_tmp_p1_va))              # edx = &p1
        a.raw(b'\x68' + le32(overlay_door_color_va))          # push &color
        a.raw(b'\xFF\x35' + le32(overlay_vertex_aspect_va))   # push aspect
        a.raw(b'\x68' + le32(door_ring_radius))               # push 2x radius (imm float bits)
        a.raw(b'\x89\xF9')                                    # ecx = renderer
        a.call_va(ax.SUB_4FCCC0_VA)
        a.label('ov_door_next')
        a.raw(b'\x46')                                        # ++idx
        a.jmp('ov_door_loop')
        a.label('ov_after_doors')

    # --- Draw detected switches -------------------------------------------
    # One oval per collide switch; a second double-radius ring marks the
    # door-opening ones (SWITCH_FLAG_OPENS_DOORS — the routing-relevant
    # subset: Torture Chamber pillar togglers, team doors, light walls). In
    # the 8-bit palettized mode all B=255 markers share a hue, so the ring —
    # like the closed-door ring — is the distinguishing signal, not color.
    if switch_table_va and switch_flags_va and overlay_switch_color_va:
        switch_ring_radius = struct.unpack(
            '<I', struct.pack('<f', cfg.OVERLAY_VERTEX_RADIUS * 2.0))[0]
        a.raw(b'\x31\xF6')                                    # esi = switch idx
        a.raw(b'\x8B\x1D' + le32(switch_count_va))            # ebx = count
        a.raw(b'\x85\xDB'); a.jz('ov_after_switches')
        a.label('ov_switch_loop')
        a.raw(b'\x39\xDE'); a.jae('ov_after_switches')        # idx >= count
        a.raw(b'\xD9\x04\xF5' + le32(switch_table_va))        # fld switch.x
        a.raw(b'\xD8\x25' + le32(overlay_cam_x_va))           # fsub cam.x
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va))          # fstp p1.x
        a.raw(b'\xD9\x04\xF5' + le32(switch_table_va + 4))    # fld switch.y
        a.raw(b'\xD8\x25' + le32(overlay_cam_y_va))           # fsub cam.y
        a.raw(b'\xD9\x1D' + le32(overlay_tmp_p1_va + 4))      # fstp p1.y
        _emit_point_cull(
            a, overlay_tmp_p1_va,
            overlay_cull_min_x_va, overlay_cull_max_x_va,
            overlay_cull_min_y_va, overlay_cull_max_y_va,
            'ov_switch_next',
        )
        a.raw(b'\xBA' + le32(overlay_tmp_p1_va))              # edx = &p1
        a.raw(b'\x68' + le32(overlay_switch_color_va))        # push &color
        a.raw(b'\xFF\x35' + le32(overlay_vertex_aspect_va))   # push aspect
        a.raw(b'\xFF\x35' + le32(overlay_vertex_radius_va))   # push radius
        a.raw(b'\x89\xF9')                                    # ecx = renderer
        a.call_va(ax.SUB_4FCCC0_VA)
        a.raw(b'\xF6\x04\x35' + le32(switch_flags_va)
              + b'\x01')                                      # test flags[i], OPENS_DOORS
        a.jz('ov_switch_next')
        a.raw(b'\xBA' + le32(overlay_tmp_p1_va))              # edx = &p1
        a.raw(b'\x68' + le32(overlay_switch_color_va))        # push &color
        a.raw(b'\xFF\x35' + le32(overlay_vertex_aspect_va))   # push aspect
        a.raw(b'\x68' + le32(switch_ring_radius))             # push 2x radius (imm float bits)
        a.raw(b'\x89\xF9')                                    # ecx = renderer
        a.call_va(ax.SUB_4FCCC0_VA)
        a.label('ov_switch_next')
        a.raw(b'\x46')                                        # ++idx
        a.jmp('ov_switch_loop')
        a.label('ov_after_switches')

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


def _emit_force_tick_entity_from_edx(a: Asm, prefix: str, done_label: str) -> None:
    """Run the normal active-entity driver stages for entity pointer EDX.

    Sets the entity's Active bit first: the same bit encodes BOTH camera-wake
    and script (de)activation, and sub_4FADC0 gates component updates on it, so
    a camera-slept trigger will not think without it. Callers MUST therefore
    gate this helper on the event-driven flag_present[] — waking a
    script-DEACTIVATED checker would re-enable captures the map script has
    forbidden (own flag away), and the bit is sticky.
    """
    a.raw(b'\x85\xD2'); a.jz(done_label)                    # NULL
    a.raw(b'\x81\xFA\x00\x00\x40\x00'); a.jb(done_label)    # below image/heap-ish range
    a.raw(b'\x81\xFA\x00\x00\x00\x70'); a.jae(done_label)   # above normal 32-bit heap range
    a.raw(b'\x89\xD7')                                      # edi = entity
    a.raw(b'\x81\x4F' + bytes([ax.ENTITY_FLAGS_OFF])
          + le32(ax.ENTITY_ACTIVE_BIT))                     # set Active
    a.raw(b'\xF7\x47' + bytes([ax.ENTITY_FLAGS_OFF])
          + le32(ax.ENTITY_SKIP_UPDATE_BIT))                # pending-delete/no-update?
    a.jnz(done_label)
    a.raw(b'\x55')                                          # save loop EBP
    a.raw(b'\xBD' + le32(ax.ENTITY_SKIP_UPDATE_BIT))         # ebp = sub_57A030 context
    a.raw(b'\x68\x89\x88\x88\x3C')                          # push dt
    a.raw(b'\x89\xF9')                                      # ecx = entity
    a.raw(b'\x8B\x17')                                      # edx = [entity]
    a.raw(b'\xFF\x52' + bytes([ax.ENTITY_TICK_PRE1_VTBL_OFF]))
    a.raw(b'\xF7\x47' + bytes([ax.ENTITY_FLAGS_OFF])
          + le32(ax.ENTITY_SKIP_UPDATE_BIT))
    a.jnz(f'{prefix}_restore')
    a.raw(b'\x68\x89\x88\x88\x3C')                          # push dt
    a.raw(b'\x89\xF9')
    a.raw(b'\x8B\x17')
    a.raw(b'\xFF\x92' + le32(ax.ENTITY_TICK_PRE2_VTBL_OFF))
    a.raw(b'\xF7\x47' + bytes([ax.ENTITY_FLAGS_OFF])
          + le32(ax.ENTITY_SKIP_UPDATE_BIT))
    a.jnz(f'{prefix}_restore')
    a.raw(b'\x68\x89\x88\x88\x3C')                          # push dt
    a.raw(b'\x89\xF9')
    a.raw(b'\x8B\x17')
    a.raw(b'\xFF\x92' + le32(ax.ENTITY_TICK_MAIN_VTBL_OFF))
    a.label(f'{prefix}_restore')
    a.raw(b'\x5D')                                          # restore loop EBP


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
    elif role == 'flag':
        src = cfg.OVERLAY_FLAG_COLOR
    elif role == 'door':
        src = cfg.OVERLAY_DOOR_COLOR
    elif role == 'switch':
        src = cfg.OVERLAY_SWITCH_COLOR
    else:
        raise ValueError(f'unknown overlay color role: {role!r}')
    return tuple(int(v) & 0xFF for v in src)
