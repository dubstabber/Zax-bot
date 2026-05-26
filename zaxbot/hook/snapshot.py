"""``do_snapshot`` body: append one tagged snapshot to ``zax_dump.bin``.

Triggered by R-key from the dispatcher. Each call appends, in order:
  1. ``snap``      — pre-incremented snap_counter (delimits multi-snapshot files)
  2. ``mgr_root``  — *dword_713F14  (0x400 B)
  3. ``session``   — *dword_713F18  (0x200 B)
  4. ``worldmgr``  — *dword_6C2080  (0x400 B)
  5. ``dpmgr``     — captured DP manager (0x1000 B; covers queue + flag at +0x8FC)
  6. ``idx_nbhd``  — 0x6C2900..0x6C2A00 (0x100 B)
  7. ``part[i]``   — 0x118 B for each session participant
  8. ``stats[i]``  — 16 B of *(part+0x1C) for each non-null
  9. ``cstr[i]``   — 16 B of *(*(part+0x1C)) for each non-null
 10. ``charptr``   — 64 B of mgr+0x290's pointer block
 11. ``char[i]``   — 0x200 B for each non-null entry in mgr+0x290 (sanity-checked)

Variable chunks carry the index in the tag (a single ASCII digit at offset +5
or +6 of the tag template). Chunk format is documented in ``zaxbot.config``."""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    fn_va           = layout.va('fn')
    cap_dpmgr       = layout.va('cap_dpmgr')
    snap_counter_va = layout.va('snap_counter')
    snap_idx_va     = layout.va('snap_idx')
    snap_count_va   = layout.va('snap_count')
    snap_arr_va     = layout.va('snap_arr')
    saved_src_va_va = layout.va('saved_src_va')
    thdr_va         = layout.va('thdr')
    thdr_tag_va     = layout.va('thdr_tag')
    thdr_src_va_va  = layout.va('thdr_src_va')
    thdr_len_va     = layout.va('thdr_len')
    stats_tmp_va    = layout.va('stats_tmp')
    cstr_tmp_va     = layout.va('cstr_tmp')

    tag_snap_marker_va = layout.va('tag_snap_marker')
    tag_mgr_root_va    = layout.va('tag_mgr_root')
    tag_session_va     = layout.va('tag_session')
    tag_worldmgr_va    = layout.va('tag_worldmgr')
    tag_dpmgr_va       = layout.va('tag_dpmgr')
    tag_idx_nbhd_va    = layout.va('tag_idx_nbhd')
    tag_part_va        = layout.va('tag_part')
    tag_stats_va       = layout.va('tag_stats')
    tag_cstr_va        = layout.va('tag_cstr')
    tag_charptr_va     = layout.va('tag_charptr')
    tag_char_va        = layout.va('tag_char')
    tag_ai_fire_va     = layout.va('tag_ai_fire')
    tag_ai_pos_va      = layout.va('tag_ai_pos')
    tag_weapon_info_va = layout.va('tag_weapon_info')
    tag_host_weapon_va = layout.va('tag_host_weapon')
    tag_pc2_weapon_va  = layout.va('tag_pc2_weapon')
    tag_host_wpn_bytes_va = layout.va('tag_host_wpn_bytes')
    tag_pc2_wpn_bytes_va  = layout.va('tag_pc2_wpn_bytes')
    primary_hash_va    = layout.va('primary_hash')
    host_weapon_obj_va = layout.va('host_weapon_obj')
    host_proto_va_va   = layout.va('host_proto_va')
    host_item_id_va    = layout.va('host_item_id')
    pc2_weapon_obj_va  = layout.va('pc2_weapon_obj')
    pc2_proto_va_va    = layout.va('pc2_proto_va')
    pc2_item_id_va     = layout.va('pc2_item_id')

    # Bot-AI scratch dump regions:
    #   ai_fire: best_target through proj_speed (64 bytes from cand_pos onward;
    #            captures best_dx/dy, best_vx/vy, host_part, proj_speed).
    #   ai_pos:  prev_pos_table + cand_vx/vy (144 bytes from prev_pos_table).
    #   weapon_info: 8 bytes — current_weapon_obj + inventory item definition.
    #                proj_speed is already covered by ai_fire.
    ai_fire_src_va     = layout.va('cand_pos')
    ai_pos_src_va      = layout.va('prev_pos_table')
    weapon_info_src_va = layout.va('current_weapon_obj')

    a.label('do_snapshot')
    # Open zax_dump.bin (append).
    a.raw(b'\x6A\x00\x68' + le32(0x80) + b'\x6A\x04\x6A\x00\x6A\x03\x68'
          + le32(0x40000000) + b'\x68' + le32(fn_va))
    a.raw(b'\xFF\x15' + le32(ax.IMP_CREATEFILEA))
    a.raw(b'\x83\xF8\xFF'); a.jz('snap_done')
    a.raw(b'\x85\xC0'); a.jz('snap_done')
    a.raw(b'\x89\xC3')                                        # mov ebx, eax (hFile)
    a.raw(b'\x6A\x02\x6A\x00\x6A\x00\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_SETFILEPTR))

    def emit_chunk(tag_va, ptr_load, length, skip_label,
                   idx_offset=None, idx_var_va=None):
        """Write one tagged chunk: 28-byte header + ``length`` payload bytes.

        ``ptr_load`` is the x86 bytes that load the source pointer into EAX;
        a chunk is skipped when EAX==0. If ``idx_offset`` is given, the tag
        template byte at that offset is overwritten with ('0' + byte at
        idx_var_va) before the header is written. Pre: EBX = hFile. Clobbers
        EAX, ECX, EDX, ESI, EDI.
        """
        a.raw(ptr_load)
        a.raw(b'\x85\xC0'); a.jz(skip_label)
        a.raw(b'\xA3' + le32(saved_src_va_va))
        a.raw(b'\xBE' + le32(tag_va))
        a.raw(b'\xBF' + le32(thdr_tag_va))
        a.raw(b'\xB9\x04\x00\x00\x00')
        a.raw(b'\xFC\xF3\xA5')
        if idx_offset is not None:
            a.raw(b'\xA0' + le32(idx_var_va))
            a.raw(b'\x04' + bytes([ord('0')]))
            a.raw(b'\xA2' + le32(thdr_tag_va + idx_offset))
        a.raw(b'\xA1' + le32(saved_src_va_va))
        a.raw(b'\xA3' + le32(thdr_src_va_va))
        a.raw(b'\xC7\x05' + le32(thdr_len_va) + le32(length))
        a.raw(b'\xB8' + le32(thdr_va))
        a.raw(b'\xBA' + le32(cfg.DUMP_HEADER_SIZE))
        a.call_lbl('wbuf')
        a.raw(b'\xA1' + le32(saved_src_va_va))
        a.raw(b'\xBA' + le32(length))
        a.call_lbl('wbuf')
        a.label(skip_label)

    # 1. snap marker
    a.raw(b'\xFF\x05' + le32(snap_counter_va))                # inc [snap_counter]
    emit_chunk(tag_snap_marker_va, b'\xB8' + le32(snap_counter_va), 4, 'snap_skip_marker')

    # 2-6. Fixed-region chunks.
    emit_chunk(tag_mgr_root_va,  b'\xA1' + le32(ax.MANAGER_GLOBAL_VA), 0x400, 'snap_skip_mgr')
    emit_chunk(tag_session_va,   b'\xA1' + le32(ax.SESSION_GLOBAL),    0x200, 'snap_skip_session')
    emit_chunk(tag_worldmgr_va,  b'\xA1' + le32(ax.WORLDMGR_GLOBAL),   0x400, 'snap_skip_wm')
    emit_chunk(tag_dpmgr_va,     b'\xA1' + le32(cap_dpmgr),           0x1000, 'snap_skip_dp')
    emit_chunk(tag_idx_nbhd_va,  b'\xB8\x00\x29\x6C\x00',              0x100, 'snap_skip_idx')

    # Bot-AI scratch dumps for shooting-prediction debugging.
    emit_chunk(tag_ai_fire_va,   b'\xB8' + le32(ai_fire_src_va),         0x40, 'snap_skip_ai_fire')
    emit_chunk(tag_ai_pos_va,    b'\xB8' + le32(ai_pos_src_va),         0x100, 'snap_skip_ai_pos')
    emit_chunk(tag_weapon_info_va, b'\xB8' + le32(weapon_info_src_va),    0x08, 'snap_skip_weapon_info')

    # --- Host-side weapon lookup (diagnostic). Resolves the host's currently
    # equipped Primary weapon and stashes (item_id, weapon_obj, item_def) so
    # the user can discover valid item ids by picking up weapons in-game and
    # pressing R. Mirrors compute_proj_speed's chain but on charArray[0]
    # instead of the bot. Safe because R is pressed long after the host is
    # fully initialised.
    a.raw(b'\xC7\x05' + le32(host_weapon_obj_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(host_proto_va_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(host_item_id_va) + le32(0xFFFFFFFF))
    a.raw(b'\xA1' + le32(ax.WORLDMGR_GLOBAL))                 # eax = worldmgr
    a.raw(b'\x85\xC0'); a.jz('snap_skip_host_wpn')
    a.raw(b'\x8B\x80\x90\x02\x00\x00')                        # eax = [eax+0x290] (charArray)
    a.raw(b'\x85\xC0'); a.jz('snap_skip_host_wpn')
    a.raw(b'\x8B\x08')                                        # ecx = charArray[0] (host char)
    a.raw(b'\x85\xC9'); a.jz('snap_skip_host_wpn')
    a.call_va(ax.SUB_4267E0_VA)                               # eax = inv
    a.raw(b'\x85\xC0'); a.jz('snap_skip_host_wpn')
    a.raw(b'\x89\xC6')                                        # esi = inv
    # Lazy-init primary_hash (compute_proj_speed normally warms this; on a
    # very early R-press it could still be 0).
    a.raw(b'\x83\x3D' + le32(primary_hash_va) + b'\x00')
    a.jnz('snap_host_have_hash')
    a.raw(b'\x6A\xFF')                                        # push -1
    a.raw(b'\x68' + le32(ax.PRIMARY_STR_VA))
    a.raw(b'\xB9' + le32(ax.SLOT_NAME_REGISTRY_VA))
    a.call_va(ax.SUB_523DF0_VA)
    a.raw(b'\xA3' + le32(primary_hash_va))
    a.label('snap_host_have_hash')
    # sub_425290(this=inv, hash) -> item_id
    a.raw(b'\xFF\x35' + le32(primary_hash_va))                # push [primary_hash]
    a.raw(b'\x8B\xCE')                                        # ecx = inv
    a.call_va(ax.SUB_425290_VA)
    a.raw(b'\xA3' + le32(host_item_id_va))                    # [host_item_id] = eax
    # inv.vtable[+0x68](inv, item_id) -> weapon obj
    a.raw(b'\x50')                                            # push eax (item_id)
    a.raw(b'\x8B\xCE')                                        # ecx = inv
    a.raw(b'\x8B\x01')                                        # eax = [ecx] (vtable)
    a.raw(b'\xFF\x50' + bytes([ax.INVENTORY_GET_WEAPON_OFF])) # call [eax+0x68]
    a.raw(b'\x85\xC0'); a.jz('snap_skip_host_wpn')
    a.raw(b'\xA3' + le32(host_weapon_obj_va))
    a.raw(b'\x8B\xC8')                                        # ecx = weapon obj
    a.call_va(ax.SUB_4DD480_VA)                               # eax = item definition
    a.raw(b'\xA3' + le32(host_proto_va_va))
    a.label('snap_skip_host_wpn')

    emit_chunk(tag_host_weapon_va, b'\xB8' + le32(host_weapon_obj_va), 0x0C, 'snap_skip_host_weapon_dump')

    # --- PC2-side weapon lookup (diagnostic). Same chain as the host block.
    # Prefer charArray[2] so a host+bot+PC2 session samples the real remote
    # client (charArray[1] is the first bot there); fall back to charArray[1]
    # for host+PC2 sessions without an earlier bot.
    a.raw(b'\xC7\x05' + le32(pc2_weapon_obj_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(pc2_proto_va_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(pc2_item_id_va) + le32(0xFFFFFFFF))
    a.raw(b'\xA1' + le32(ax.WORLDMGR_GLOBAL))                 # eax = worldmgr
    a.raw(b'\x85\xC0'); a.jz('snap_skip_pc2_wpn')
    a.raw(b'\x8B\x80\x90\x02\x00\x00')                        # eax = [eax+0x290] (charArray)
    a.raw(b'\x85\xC0'); a.jz('snap_skip_pc2_wpn')
    a.raw(b'\x8B\x48\x08')                                    # ecx = charArray[2] (PC2 after bot)
    a.raw(b'\x85\xC9')                                        # test ecx, ecx
    a.jnz('snap_pc2_have_char')
    a.raw(b'\x8B\x48\x04')                                    # fallback: charArray[1]
    a.label('snap_pc2_have_char')
    a.raw(b'\x85\xC9'); a.jz('snap_skip_pc2_wpn')
    a.call_va(ax.SUB_4267E0_VA)                               # eax = inv
    a.raw(b'\x85\xC0'); a.jz('snap_skip_pc2_wpn')
    a.raw(b'\x89\xC6')                                        # esi = inv
    # primary_hash is shared (process-wide); should already be warmed by the
    # host block above or compute_proj_speed.
    a.raw(b'\x83\x3D' + le32(primary_hash_va) + b'\x00')
    a.jnz('snap_pc2_have_hash')
    a.raw(b'\x6A\xFF')
    a.raw(b'\x68' + le32(ax.PRIMARY_STR_VA))
    a.raw(b'\xB9' + le32(ax.SLOT_NAME_REGISTRY_VA))
    a.call_va(ax.SUB_523DF0_VA)
    a.raw(b'\xA3' + le32(primary_hash_va))
    a.label('snap_pc2_have_hash')
    a.raw(b'\xFF\x35' + le32(primary_hash_va))                # push [primary_hash]
    a.raw(b'\x8B\xCE')                                        # ecx = inv
    a.call_va(ax.SUB_425290_VA)
    a.raw(b'\xA3' + le32(pc2_item_id_va))                     # [pc2_item_id] = eax
    a.raw(b'\x50')                                            # push eax (item_id)
    a.raw(b'\x8B\xCE')                                        # ecx = inv
    a.raw(b'\x8B\x01')                                        # eax = [ecx] (vtable)
    a.raw(b'\xFF\x50' + bytes([ax.INVENTORY_GET_WEAPON_OFF])) # call [eax+0x68]
    a.raw(b'\x85\xC0'); a.jz('snap_skip_pc2_wpn')
    a.raw(b'\xA3' + le32(pc2_weapon_obj_va))
    a.raw(b'\x8B\xC8')                                        # ecx = weapon obj
    a.call_va(ax.SUB_4DD480_VA)                               # eax = item definition
    a.raw(b'\xA3' + le32(pc2_proto_va_va))
    a.label('snap_skip_pc2_wpn')

    emit_chunk(tag_pc2_weapon_va, b'\xB8' + le32(pc2_weapon_obj_va), 0x0C, 'snap_skip_pc2_weapon_dump')

    # --- Raw 128-byte dumps of each weapon object for layout comparison.
    # `ptr_load` here uses `mov eax, [scratch]` (opcode A1) so
    # the source is the weapon-obj pointer stored at host/pc2_weapon_obj.
    emit_chunk(tag_host_wpn_bytes_va, b'\xA1' + le32(host_weapon_obj_va), 0x80, 'snap_skip_host_wpn_b')
    emit_chunk(tag_pc2_wpn_bytes_va,  b'\xA1' + le32(pc2_weapon_obj_va),  0x80, 'snap_skip_pc2_wpn_b')

    # 7-9. Per-participant iteration: dpmgr.array[0..count). Entries are direct
    # 280B participant pointers — no -0x3C sink dance.
    a.raw(b'\xA1' + le32(cap_dpmgr))                          # mov eax, [cap_dpmgr]
    a.raw(b'\x85\xC0'); a.jz('snap_skip_parts')
    a.raw(b'\x8B\x48\x18')                                    # mov ecx, [eax+0x18]  (count)
    a.raw(b'\x83\xF9\x10'); a.jb('snap_part_count_ok')        # cap to 16
    a.raw(b'\xB9\x10\x00\x00\x00')
    a.label('snap_part_count_ok')
    a.raw(b'\x8B\x40\x14')                                    # mov eax, [eax+0x14]  (array)
    a.raw(b'\x85\xC0'); a.jz('snap_skip_parts')
    a.raw(b'\x89\x0D' + le32(snap_count_va))
    a.raw(b'\xA3' + le32(snap_arr_va))
    a.raw(b'\xC7\x05' + le32(snap_idx_va) + le32(0))

    a.label('snap_part_loop')
    a.raw(b'\xA1' + le32(snap_idx_va))
    a.raw(b'\x3B\x05' + le32(snap_count_va))
    a.jae('snap_skip_parts')
    a.raw(b'\x8B\x15' + le32(snap_arr_va))
    a.raw(b'\x8B\x14\x82')                                    # edx = arr[eax] (participant)
    a.raw(b'\x85\xD2'); a.jz('snap_part_skip')
    emit_chunk(tag_part_va, b'\x89\xD0', 0x118, 'snap_part_wrote',
               idx_offset=5, idx_var_va=snap_idx_va)
    # Bot-name probe: stage *(part+0x1C) and *(*(part+0x1C)) into tmps.
    a.raw(b'\xC7\x05' + le32(stats_tmp_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(cstr_tmp_va) + le32(0))
    a.raw(b'\xA1' + le32(snap_idx_va))
    a.raw(b'\x8B\x15' + le32(snap_arr_va))
    a.raw(b'\x8B\x14\x82')                                    # re-derive participant (emit_chunk clobbered edx)
    a.raw(b'\x85\xD2'); a.jz('snap_probe_emit')
    a.raw(b'\x8B\x42\x1C')                                    # eax = stats sub-object
    a.raw(b'\x85\xC0'); a.jz('snap_probe_emit')
    a.raw(b'\xA3' + le32(stats_tmp_va))
    a.raw(b'\x8B\x00')                                        # eax = CString header
    a.raw(b'\x85\xC0'); a.jz('snap_probe_emit')
    a.raw(b'\xA3' + le32(cstr_tmp_va))
    a.label('snap_probe_emit')
    emit_chunk(tag_stats_va, b'\xA1' + le32(stats_tmp_va), 16, 'snap_stats_wrote',
               idx_offset=6, idx_var_va=snap_idx_va)
    emit_chunk(tag_cstr_va,  b'\xA1' + le32(cstr_tmp_va),  16, 'snap_cstr_wrote',
               idx_offset=5, idx_var_va=snap_idx_va)
    a.label('snap_part_skip')
    a.raw(b'\xFF\x05' + le32(snap_idx_va))                    # inc [snap_idx]
    a.jmp('snap_part_loop')
    a.label('snap_skip_parts')

    # 10-11. Per-character iteration: scan mgr+0x290[0..16] with pointer sanity.
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))
    a.raw(b'\x85\xC0'); a.jz('snap_skip_chars')
    a.raw(b'\x8B\x90' + le32(0x290))                          # edx = char_arr
    a.raw(b'\x85\xD2'); a.jz('snap_skip_chars')
    a.raw(b'\x89\x15' + le32(snap_arr_va))                    # save char_arr for char loop
    emit_chunk(tag_charptr_va, b'\xA1' + le32(snap_arr_va), 64, 'snap_skip_charptr')
    a.raw(b'\xC7\x05' + le32(snap_count_va) + le32(16))
    a.raw(b'\xC7\x05' + le32(snap_idx_va) + le32(0))

    a.label('snap_char_loop')
    a.raw(b'\xA1' + le32(snap_idx_va))
    a.raw(b'\x3B\x05' + le32(snap_count_va))
    a.jae('snap_skip_chars')
    a.raw(b'\x8B\x15' + le32(snap_arr_va))
    a.raw(b'\x8B\x14\x82')
    a.raw(b'\x81\xFA\x00\x00\x40\x00')                        # cmp edx, 0x400000 (image base)
    a.jb('snap_char_skip')
    a.raw(b'\x81\xFA\x00\x00\x00\x70')
    a.jae('snap_char_skip')
    emit_chunk(tag_char_va, b'\x89\xD0', 0x200, 'snap_char_wrote',
               idx_offset=5, idx_var_va=snap_idx_va)
    a.label('snap_char_skip')
    a.raw(b'\xFF\x05' + le32(snap_idx_va))
    a.jmp('snap_char_loop')
    a.label('snap_skip_chars')

    a.raw(b'\x53'); a.raw(b'\xFF\x15' + le32(ax.IMP_CLOSEHANDLE))
    a.label('snap_done')
    a.raw(b'\xC3')
