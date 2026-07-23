"""Steer emit: ``desired = target - bot`` into dx/dy (node target or
latched final-approach target), teleport-sliver crossing guards."""

from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout, c) -> None:
    bot_pos_va = c.bot_pos_va
    bot_slot_tmp_va = c.bot_slot_tmp_va
    current_wp_va = c.current_wp_va
    prev_wp_va = c.prev_wp_va
    overlay_vertices_va = c.overlay_vertices_va
    edge_follow_enabled_va = c.edge_follow_enabled_va
    edge_lookahead_va = c.edge_lookahead_va
    wp_seg_x_va = c.wp_seg_x_va
    wp_seg_y_va = c.wp_seg_y_va
    wp_tp_va = c.wp_tp_va
    dx_accum_va = c.dx_accum_va
    dy_accum_va = c.dy_accum_va

    a.label('s542360_wp_steer')
    # Edge-following: when latched + enabled, steer toward a look-ahead point ON
    # the prev->current segment so the bot hugs the connection line (vital on
    # narrow lava corridors) instead of cutting diagonally after any drift. Else
    # (not latched / disabled / degenerate segment) steer straight at the node.
    # The wall-slide post-step still deflects the ANGLE if wedged against geometry.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x83\x3D' + le32(edge_follow_enabled_va) + b'\x00')  # cmp [edge_follow_enabled], 0
    a.jz('s542360_wp_steer_node')
    if c.diverge:
        # DIVERGENCE gate: a level-2/3 target node is followed FREELY —
        # skip the edge-hug and take the straight-at-node path (which adds
        # the per-bot lateral offset below). Level-1 (strict) nodes keep
        # the hug — that is what keeps bots on narrow lava corridors.
        a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))      # eax = cur
        a.raw(b'\x0F\xB6\x80' + le32(c.wp_node_level_va))  # movzx eax, level[cur]
        a.raw(b'\x83\xF8\x02')                            # cmp eax, 2
        a.jae('s542360_wp_steer_node')                    # level >= 2 -> free
    a.raw(b'\x8B\x04\x8D' + le32(prev_wp_va))             # eax = prev_wp[slot]
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_wp_steer_node')                         # not latched -> node-only
    a.raw(b'\x8D\x34\xC5' + le32(overlay_vertices_va))    # esi = &verts[prev] (P)
    a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))          # eax = cur
    a.raw(b'\x8D\x3C\xC5' + le32(overlay_vertices_va))    # edi = &verts[cur]  (C)
    # seg = C - P
    a.raw(b'\xD9\x07'); a.raw(b'\xD8\x26'); a.raw(b'\xD9\x1D' + le32(wp_seg_x_va))           # seg_x = C.x - P.x
    a.raw(b'\xD9\x47\x04'); a.raw(b'\xD8\x66\x04'); a.raw(b'\xD9\x1D' + le32(wp_seg_y_va))   # seg_y = C.y - P.y
    # seglen2 = seg_x^2 + seg_y^2
    a.raw(b'\xD9\x05' + le32(wp_seg_x_va)); a.raw(b'\xD8\xC8')   # fld seg_x; fmul st,st
    a.raw(b'\xD9\x05' + le32(wp_seg_y_va)); a.raw(b'\xD8\xC8')   # fld seg_y; fmul st,st
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0 = seglen2
    a.raw(b'\xD9\xEE')                                    # fldz (ST0=0, ST1=seglen2)
    a.raw(b'\xDF\xF1')                                    # fcomip st0,st1 (pop 0); CF=1 iff 0<seglen2
    a.jae('s542360_wp_steer_node_pop')                    # 0>=seglen2 -> degenerate (pop seglen2)
    # dot = (B-P).seg   (ST0=seglen2 throughout)
    a.raw(b'\xD9\x05' + le32(bot_pos_va)); a.raw(b'\xD8\x26'); a.raw(b'\xD8\x0D' + le32(wp_seg_x_va))      # (B.x-P.x)*seg_x
    a.raw(b'\xD9\x05' + le32(bot_pos_va + 4)); a.raw(b'\xD8\x66\x04'); a.raw(b'\xD8\x0D' + le32(wp_seg_y_va))  # (B.y-P.y)*seg_y
    a.raw(b'\xDE\xC1')                                    # faddp -> ST0=dot, ST1=seglen2
    a.raw(b'\xDE\xF1')                                    # fdivrp st1,st0 -> ST0 = dot/seglen2 = t
    a.raw(b'\xD8\x05' + le32(edge_lookahead_va))          # fadd lookahead_frac -> ST0 = tp
    # clamp tp to [0, 1]: upper
    a.raw(b'\xD9\xE8')                                    # fld1 (ST0=1, ST1=tp)
    a.raw(b'\xDF\xF1')                                    # fcomip st0,st1 (pop 1); CF=1 iff 1<tp
    a.jae('s542360_wp_tp_no_hi')                          # 1>=tp -> no upper clamp
    a.raw(b'\xDD\xD8'); a.raw(b'\xD9\xE8')                # fstp st0 (drop tp); fld1 (tp=1)
    a.label('s542360_wp_tp_no_hi')
    a.raw(b'\xD9\xEE')                                    # fldz (ST0=0, ST1=tp)
    a.raw(b'\xDF\xF1')                                    # fcomip st0,st1 (pop 0); CF=1 iff 0<tp
    a.jb('s542360_wp_tp_no_lo')                           # 0<tp -> no lower clamp
    a.raw(b'\xDD\xD8'); a.raw(b'\xD9\xEE')                # fstp st0 (drop tp); fldz (tp=0)
    a.label('s542360_wp_tp_no_lo')
    a.raw(b'\xD9\x1D' + le32(wp_tp_va))                   # fstp tp
    # desired = (P + tp*seg) - B
    a.raw(b'\xD9\x05' + le32(wp_tp_va)); a.raw(b'\xD8\x0D' + le32(wp_seg_x_va))    # fld tp; fmul seg_x
    a.raw(b'\xD8\x06'); a.raw(b'\xD8\x25' + le32(bot_pos_va))                      # fadd [esi] (P.x); fsub bot.x
    a.raw(b'\xD9\x1D' + le32(dx_accum_va))                # fstp dx_accum
    a.raw(b'\xD9\x05' + le32(wp_tp_va)); a.raw(b'\xD8\x0D' + le32(wp_seg_y_va))    # fld tp; fmul seg_y
    a.raw(b'\xD8\x46\x04'); a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))              # fadd [esi+4] (P.y); fsub bot.y
    a.raw(b'\xD9\x1D' + le32(dy_accum_va))                # fstp dy_accum
    a.jmp('s542360_emit')

    a.label('s542360_wp_steer_node_pop')
    a.raw(b'\xDD\xD8')                                    # fstp st0 (pop seglen2)
    a.label('s542360_wp_steer_node')
    # Straight-at-node fallback: desired = node - bot. With divergence, the
    # target is node + this bot's rolled lateral offset (zero for level-1
    # nodes) — the offset stays well inside the level's arrival radius so
    # the dsq-to-node arrival/progress machinery is unaffected.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))          # eax = cur
    if c.diverge:
        # Re-roll the per-bot offset the moment the latched node changes
        # (bot_div_node stores node+1; 0 = never rolled -> mismatch).
        a.raw(b'\x8D\x50\x01')                            # lea edx, [eax+1]
        a.raw(b'\x3B\x14\x8D' + le32(c.bot_div_node_va))  # cmp edx, div_node[slot]
        a.jz('s542360_wp_div_ok')
        a.raw(b'\x51')                                    # push ecx (slot)
        a.raw(b'\x50')                                    # push eax (cur)
        a.call_lbl('wp_div_roll')                         # in: eax=node, ecx=slot
        a.raw(b'\x58')                                    # pop eax
        a.raw(b'\x59')                                    # pop ecx
        a.label('s542360_wp_div_ok')
    a.raw(b'\x8D\x14\xC5' + le32(overlay_vertices_va))    # lea edx, [eax*8 + verts]
    a.raw(b'\xD9\x02')                                    # fld [edx]     node.x
    if c.diverge:
        a.raw(b'\xD8\x04\x8D' + le32(c.bot_div_x_va))     # fadd off.x[slot]
    a.raw(b'\xD8\x25' + le32(bot_pos_va))                 # fsub bot.x
    a.raw(b'\xD9\x1D' + le32(dx_accum_va))                # fstp dx_accum
    a.raw(b'\xD9\x42\x04')                                # fld [edx+4]   node.y
    if c.diverge:
        a.raw(b'\xD8\x04\x8D' + le32(c.bot_div_y_va))     # fadd off.y[slot]
    a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))             # fsub bot.y
    a.raw(b'\xD9\x1D' + le32(dy_accum_va))                # fstp dy_accum
    a.jmp('s542360_emit')

    a.label('s542360_fallback_zero')
    # No graph / follow disabled: emit zero (idle). The random-wander potential
    # field was removed; author a graph for maps where bots should move.
    a.raw(b'\xC7\x05' + le32(dx_accum_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(dy_accum_va) + le32(0))
    # fall through to emit


