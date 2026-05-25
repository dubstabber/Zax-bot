"""``do_spawn_with_team`` body: Phase-B DP-queue injection + character spawn.

Inputs: ``menu_mode`` and ``chosen_team`` in scratch. Preconditions: caller did
pushad.

Flow:
  1. Re-validate MP/DP/a2 (the match may have ended between B and digit).
  2. Cap-check against the hosted netgame's advertised MaxPlayers.
  3. Find a free synthetic-id slot, claim it under the DP CritSec.
  4. Inject a synthetic "player added" entry into ``dpmgr+0x44D`` and call
     ``sub_480800`` synchronously with ``edi = host_char_ptr`` — the engine
     creates the participant via its natural factory path.
  5. Bind the bot's team in stats+0x14 via ``sub_5BA820``.
  6. Pre-grow ``mgr+0x290`` to capacity 16 if still at initial size (avoids
     the 9th-char OOB crash; see [[garbage-slot-crash]] memory).
  7. Call ``sub_59DF90(mgr, a2, botidx, 0, 0)`` to create + place the char.
  8. Pick a random name and copy it into the participant's stats CString.
  9. Cache the bot's character pointer in the ``bot_chars`` scratch table.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout
from .helpers import emit_logc_call, enter_cs, leave_cs


def emit(a: Asm, layout: ScratchLayout) -> None:
    active_bot_slot_va  = layout.va('active_bot_slot')
    cap_dpmgr           = layout.va('cap_dpmgr')
    cap_a2              = layout.va('cap_a2')
    max_players_va      = layout.va('max_players')
    cur_players_va      = layout.va('cur_players')
    bot_participants_va = layout.va('bot_participants')
    bot_indices_va      = layout.va('bot_indices')
    bot_chars_va        = layout.va('bot_chars')
    botp_va             = layout.va('botp')
    botidx_va           = layout.va('botidx')
    botchar_va          = layout.va('botchar')
    botmode_va          = layout.va('botmode')
    menu_mode_va        = layout.va('menu_mode')
    chosen_team_va      = layout.va('chosen_team')
    logbyte_va          = layout.va('logbyte')
    my_queue_slot_va    = layout.va('my_queue_slot')
    synthetic_player_id_va = layout.va('synthetic_player_id')
    phase_b_in_flight_va   = layout.va('phase_b_in_flight')
    bot_names_ascii_va  = layout.va('bot_names_ascii')
    msg_va              = layout.va('msg')
    msg_full_va         = layout.va('msg_full')

    def logc(ch): emit_logc_call(a, logbyte_va, ch)

    a.label('do_spawn_with_team')

    # MP re-gate (paranoid; match could have ended between B and digit press).
    # Uses EDX as final reg (vs EAX in the dispatcher's mp_gate), so kept inline.
    a.raw(b'\xC7\x05' + le32(active_bot_slot_va) + le32(0xFFFFFFFF))
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA)); a.raw(b'\x85\xC0'); a.jz('spawn_done')
    a.raw(b'\x89\xC1'); a.raw(b'\x8B\x10')
    a.raw(b'\xFF\x92' + le32(ax.VT_OFFSET_TO_LVL)); a.raw(b'\x85\xC0'); a.jz('spawn_done')
    a.raw(b'\x8B\x50' + bytes([ax.MP_DATA_FIELD])); a.raw(b'\x85\xD2'); a.jz('spawn_done')
    logc(ord('A'))

    # Need captured DP manager and a2
    a.raw(b'\x8B\x35' + le32(cap_dpmgr))                     # mov esi,[cap_dpmgr]
    a.raw(b'\x85\xF6'); a.jz('spawn_done')
    logc(ord('M'))
    a.raw(b'\xA1' + le32(cap_a2)); a.raw(b'\x85\xC0'); a.jz('spawn_done')
    logc(ord('2'))

    # Respect the hosted session's advertised maxplayers.
    a.raw(b'\x8B\x46' + bytes([ax.DPMGR_NETGAME_FIELD]))     # mov eax,[esi+8]
    a.raw(b'\x85\xC0'); a.jz('spawn_done')
    a.raw(b'\x8B\x40' + bytes([ax.NETGAME_MAX_PLAYERS]))     # mov eax,[eax+0x0C]
    a.raw(b'\x83\xF8\x02'); a.jb('spawn_full')               # cap < 2 -> fail closed
    a.raw(b'\x83\xF8' + bytes([cfg.MAX_BOT_SLOTS])); a.ja('spawn_cap_default')
    a.jmp('spawn_cap_ok')
    a.label('spawn_cap_default')
    a.raw(b'\xB8' + le32(cfg.MAX_BOT_SLOTS))                 # cap > 16 -> clamp to synthetic range
    a.label('spawn_cap_ok')
    a.raw(b'\xA3' + le32(max_players_va))                    # mov [max_players], eax

    # dpmgr+0x18 = live participant count (host + clients + bots).
    a.raw(b'\x8B\x46\x18')                                   # mov eax,[esi+0x18]
    a.raw(b'\xA3' + le32(cur_players_va))                    # mov [cur_players],eax
    a.raw(b'\x3B\x05' + le32(max_players_va))                # cmp eax,[max_players]
    a.jae('spawn_full')

    # First reusable bot scratch slot.
    a.raw(b'\x31\xDB')                                       # xor ebx, ebx
    a.label('find_free_bot_slot')
    a.raw(b'\x83\x3C\x9D' + le32(bot_participants_va) + b'\x00')
    a.jz('found_free_bot_slot')
    a.raw(b'\x43')                                           # inc ebx
    a.raw(b'\x83\xFB' + bytes([cfg.MAX_BOT_SLOTS]))          # cmp ebx, MAX_BOT_SLOTS
    a.jb('find_free_bot_slot')
    a.jmp('spawn_full')
    a.label('found_free_bot_slot')
    a.raw(b'\x89\x1D' + le32(active_bot_slot_va))            # mov [active_bot_slot], ebx
    a.raw(b'\xC7\x05' + le32(botp_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(botidx_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(botchar_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(my_queue_slot_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(phase_b_in_flight_va) + le32(0))

    # --- Phase B: DP-queue injection ----------------------------------------
    enter_cs(a)
    # Re-check participant count under the lock before publishing the entry.
    a.raw(b'\x8B\x46\x18')                                    # mov eax,[esi+0x18]
    a.raw(b'\x3B\x05' + le32(max_players_va))                 # cmp eax,[max_players]
    a.jae('spawn_crit_full')
    # botidx = pre-insert count (= slot in mgr+0x290 the bot occupies).
    a.raw(b'\x8B\x46\x18'); a.raw(b'\xA3' + le32(botidx_va))   # mov eax,[esi+0x18]; mov [botidx],eax
    # Record the synthetic id (read back by detour_name_query1/2).
    a.raw(b'\xA1' + le32(active_bot_slot_va))                  # mov eax,[active_bot_slot]
    a.raw(b'\x05' + le32(cfg.SYNTHETIC_ID_LO))                 # add eax,SYNTHETIC_ID_LO
    a.raw(b'\xA3' + le32(synthetic_player_id_va))              # mov [synthetic_player_id],eax
    a.raw(b'\xC7\x05' + le32(phase_b_in_flight_va) + le32(1))
    logc(ord('B'))
    # Walk dpmgr+0x44D[0..99] looking for an empty entry.
    a.raw(b'\x8D\xBE\x4D\x04\x00\x00')                         # lea edi, [esi+0x44D]
    a.raw(b'\xB9\x64\x00\x00\x00')                             # mov ecx, 100
    a.label('pb_find_slot')
    a.raw(b'\x8A\x47\xFF')                                     # mov al, [edi-1]   (added)
    a.raw(b'\x84\xC0')
    a.jnz('pb_next_slot')
    a.raw(b'\x8A\x07')                                         # mov al, [edi]      (removed)
    a.raw(b'\x84\xC0')
    a.jz('pb_found_slot')
    a.label('pb_next_slot')
    a.raw(b'\x83\xC7\x0C')                                     # add edi, 12
    a.raw(b'\x49')                                             # dec ecx
    a.jnz('pb_find_slot')
    a.jmp('spawn_crit_fail')                                   # no empty slot
    a.label('pb_found_slot')
    a.raw(b'\x89\x3D' + le32(my_queue_slot_va))                # mov [my_queue_slot], edi
    a.raw(b'\xA1' + le32(synthetic_player_id_va))              # mov eax,[synthetic_player_id]
    a.raw(b'\x89\x47\x03')                                     # mov [edi+3], eax
    a.raw(b'\xC7\x47\x07\x00\x00\x00\x00')                     # mov [edi+7], 0
    a.raw(b'\xC6\x47\xFF\x01')                                 # mov byte [edi-1], 1 (added — LAST)
    # Drive sub_480800 reach: gate flags must be on.
    a.raw(b'\xC6\x86\x38\x00\x00\x00\x01')                     # mov byte [esi+0x38], 1
    a.raw(b'\xC6\x86\x39\x00\x00\x00\x01')                     # mov byte [esi+0x39], 1
    a.raw(b'\xC6\x86\xFC\x08\x00\x00\x01')                     # mov byte [esi+0x8FC], 1
    logc(ord('C'))
    # Drive sub_480800 synchronously with edi = host_char_ptr.
    a.raw(b'\xA1' + le32(ax.WORLDMGR_GLOBAL))                  # mov eax, [worldmgr]
    a.raw(b'\x85\xC0'); a.jz('spawn_crit_fail')
    a.raw(b'\x8B\xB8\x90\x02\x00\x00')                          # mov edi, [eax+0x290]
    a.raw(b'\x85\xFF'); a.jz('spawn_crit_fail')
    a.raw(b'\x8B\x3F')                                          # mov edi, [edi]
    a.raw(b'\x85\xFF'); a.jz('spawn_crit_fail')
    a.raw(b'\x8B\xCE')                                          # mov ecx, esi
    a.call_va(ax.SUB_480800_VA)
    logc(ord('D'))
    # Read participant pointer back from queue slot.
    a.raw(b'\x8B\x3D' + le32(my_queue_slot_va))
    a.raw(b'\x8B\x47\x07')                                     # mov eax, [edi+7]
    a.raw(b'\x85\xC0'); a.jz('spawn_crit_fail')
    logc(ord('S'))
    a.raw(b'\xA3' + le32(botp_va))                             # mov [botp], eax
    a.raw(b'\x8B\x15' + le32(active_bot_slot_va))              # mov edx,[active_bot_slot]
    a.raw(b'\x89\x04\x95' + le32(bot_participants_va))         # bot_participants[edx] = eax
    a.raw(b'\xA1' + le32(botidx_va))                           # mov eax,[botidx]
    a.raw(b'\x89\x04\x95' + le32(bot_indices_va))              # bot_indices[edx] = eax
    # Clear synthetic queue item so the natural DP poll doesn't re-create it.
    a.raw(b'\x8B\x3D' + le32(my_queue_slot_va))
    a.raw(b'\x85\xFF'); a.jz('pb_clear_success_done')
    a.raw(b'\xC6\x47\xFF\x00')
    a.raw(b'\xC6\x07\x00')
    a.raw(b'\xC7\x47\x03\x00\x00\x00\x00')
    a.raw(b'\xC7\x47\x07\x00\x00\x00\x00')
    a.raw(b'\xC7\x05' + le32(my_queue_slot_va) + le32(0))
    a.label('pb_clear_success_done')
    a.raw(b'\xC7\x05' + le32(phase_b_in_flight_va) + le32(0))
    leave_cs(a)

    # --- Bind bot team (stats+0x14). DM: team = slot + 1 (unique non-zero per
    # bot to dodge the same-team spawn-picker pathology). CTF/SK: write the
    # user-chosen team verbatim — engine CTF expects {0=Blue, 1=Red} (see the
    # `gametype.vtbl[39]` resolver at sub_4698B0, which only writes a team
    # hue for team values 0 and 1). SK extends to {0..3}.
    a.raw(b'\x83\x3D' + le32(botp_va) + b'\x00')
    a.jz('spawn_skip_team')
    a.raw(b'\x8B\x0D' + le32(botidx_va))                     # ecx = botidx (arg to sub_5BA820)
    a.call_va(ax.SUB_5BA820)                                 # eax = bot's stats
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_team')
    a.raw(b'\x83\x3D' + le32(menu_mode_va) + b'\x00')
    a.jnz('spawn_team_chosen')
    a.raw(b'\x8B\x15' + le32(active_bot_slot_va))            # DM: team = slot + 1
    a.raw(b'\x83\xC2\x01')
    a.jmp('spawn_team_write')
    a.label('spawn_team_chosen')
    a.raw(b'\x8B\x15' + le32(chosen_team_va))                # CTF/SK: chosen_team (0..N-1)
    a.label('spawn_team_write')
    a.raw(b'\x89\x50\x14')                                   # mov [eax+0x14], edx
    logc(ord('T'))
    a.label('spawn_skip_team')

    # --- Create + place the character: sub_59DF90(mgr, a2, botidx, 0, 0).
    logc(ord('P'))
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))              # mov eax,[mgr]
    a.raw(b'\x85\xC0'); a.jz('spawn_done')
    a.raw(b'\x8B\x90\x90\x02\x00\x00')                       # mov edx,[eax+0x290]
    a.raw(b'\x85\xD2'); a.jz('spawn_done')
    a.raw(b'\x8B\x0D' + le32(botidx_va))                     # mov ecx,[botidx]
    a.raw(b'\xC7\x04\x8A\x00\x00\x00\x00')                   # mov dword [edx+ecx*4],0
    a.raw(b'\xC7\x05' + le32(botmode_va) + le32(1))          # botmode = 1

    # Pre-grow mgr+0x290 char-array to capacity 16 if still at initial size.
    a.raw(b'\x8B\x1D' + le32(ax.MANAGER_GLOBAL_VA))          # mov ebx,[mgr]
    a.raw(b'\x85\xDB'); a.jz('grow_skip')
    a.raw(b'\x83\xBB\x98\x02\x00\x00\x10')                   # cmp [ebx+0x298], 16
    a.jae('grow_skip')
    a.raw(b'\x6A\x40')                                        # push 64
    a.call_va(ax.OP_NEW_VA)                                   # operator new
    a.raw(b'\x83\xC4\x04')
    a.raw(b'\x85\xC0'); a.jz('grow_skip')
    a.raw(b'\x8B\xB3\x90\x02\x00\x00')                        # mov esi,[ebx+0x290]
    a.raw(b'\x8B\x8B\x94\x02\x00\x00')                        # mov ecx,[ebx+0x294]
    a.raw(b'\x89\xC7')                                        # mov edi, eax
    a.raw(b'\x50')                                            # push eax (save new buf)
    a.raw(b'\x85\xF6'); a.jz('grow_no_copy')
    a.raw(b'\xFC\xF3\xA5')                                    # cld; rep movsd
    a.label('grow_no_copy')
    a.raw(b'\xFF\xB3\x90\x02\x00\x00')                        # push [ebx+0x290]
    a.call_va(ax.OP_DELETE_VA)                                # operator delete
    a.raw(b'\x83\xC4\x04')
    a.raw(b'\x58')                                            # pop eax (= new buf)
    a.raw(b'\x89\x83\x90\x02\x00\x00')                        # mov [ebx+0x290], eax
    a.raw(b'\xC7\x83\x98\x02\x00\x00\x10\x00\x00\x00')        # mov [ebx+0x298], 16
    a.label('grow_skip')

    # Compute a2: prefer sub_4F1050(mgr); fall back to cap_a2.
    a.raw(b'\x8B\x0D' + le32(ax.MANAGER_GLOBAL_VA))
    a.call_va(ax.SUB_4F1050_VA)
    a.raw(b'\x85\xC0')
    a.jnz('df90_a2_ok')
    a.raw(b'\xA1' + le32(cap_a2))
    a.label('df90_a2_ok')
    a.raw(b'\x6A\x00')                                        # push 0 (a5)
    a.raw(b'\x6A\x00')                                        # push 0 (name)
    a.raw(b'\xFF\x35' + le32(botidx_va))                      # push [botidx]
    a.raw(b'\x50')                                            # push eax (a2)
    a.raw(b'\x8B\x0D' + le32(ax.MANAGER_GLOBAL_VA))           # mov ecx,[mgr]
    a.call_va(ax.DF90_VA)                                     # sub_59DF90 (retn 0x10)
    a.raw(b'\xC7\x05' + le32(botmode_va) + le32(0))           # botmode = 0
    logc(ord('E'))

    # Keep mgr+0x294 high enough for follow-up char lookups at this index.
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_count_bump')
    a.raw(b'\x8B\x15' + le32(botidx_va))
    a.raw(b'\x42')                                            # inc edx
    a.raw(b'\x39\x90\x94\x02\x00\x00')                        # cmp [eax+0x294], edx
    a.jae('spawn_skip_count_bump')
    a.raw(b'\x89\x90\x94\x02\x00\x00')                        # mov [eax+0x294], edx
    a.label('spawn_skip_count_bump')

    logc(ord('V'))

    # --- Set the bot's display name via sub_4E1930(stats, ASCII name).
    a.raw(b'\xA1' + le32(botp_va))                            # mov eax, [botp]
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_name')
    a.raw(b'\x6A' + bytes([cfg.NUM_BOT_NAMES - 1]))           # push (NUM-1)
    a.raw(b'\x6A\x00')                                         # push 0
    a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                       # mov ecx, RNG
    a.call_va(ax.RNG_SUB)                                      # eax = idx in [0,NUM-1]
    a.raw(b'\xC1\xE0\x04')                                     # shl eax, 4  (NAME_SLOT_ASCII=16)
    a.raw(b'\x05' + le32(bot_names_ascii_va))                  # add eax, table
    a.raw(b'\x50')                                             # push eax (Source)
    a.raw(b'\x8B\x0D' + le32(botp_va))                         # mov ecx, [botp]
    a.raw(b'\x8B\x49\x1C')                                     # mov ecx, [ecx+0x1C] (stats CString)
    a.call_va(ax.SUB_4E1930_VA)                                # CString::operator=(Source)
    logc(ord('N'))
    a.label('spawn_skip_name')

    # --- Capture the bot's char into bot_chars[slot] for the input detours.
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))
    a.raw(b'\x8B\x80\x90\x02\x00\x00')                        # eax = mgr->charArray
    a.raw(b'\x85\xC0'); a.jz('spawn_skipai')
    a.raw(b'\x8B\x0D' + le32(botidx_va))
    a.raw(b'\x8B\x04\x88')                                    # eax = char at idx
    a.raw(b'\x85\xC0'); a.jz('spawn_skipai')
    a.raw(b'\xA3' + le32(botchar_va))
    a.raw(b'\x8B\x15' + le32(active_bot_slot_va))
    a.raw(b'\x83\xFA' + bytes([cfg.MAX_BOT_SLOTS]))
    a.jae('spawn_store_char_done')
    a.raw(b'\x89\x04\x95' + le32(bot_chars_va))               # bot_chars[slot] = char
    a.label('spawn_store_char_done')
    logc(ord('F'))
    a.label('spawn_skipai')

    # Confirm with on-screen message.
    a.raw(b'\xC7\x05' + le32(active_bot_slot_va) + le32(0xFFFFFFFF))
    a.raw(b'\x6A\xFF'); a.raw(b'\x68' + le32(msg_va)); a.call_va(ax.SHOWMSG_VA)
    a.raw(b'\xC3')                                            # ret

    a.label('spawn_full')
    a.raw(b'\x6A\xFF'); a.raw(b'\x68' + le32(msg_full_va)); a.call_va(ax.SHOWMSG_VA)
    a.raw(b'\xC3')

    a.label('spawn_crit_full')
    a.raw(b'\xC7\x05' + le32(phase_b_in_flight_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(active_bot_slot_va) + le32(0xFFFFFFFF))
    leave_cs(a)
    a.jmp('spawn_full')

    a.label('spawn_crit_fail')
    a.raw(b'\xC7\x05' + le32(phase_b_in_flight_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(active_bot_slot_va) + le32(0xFFFFFFFF))
    a.raw(b'\x8B\x3D' + le32(my_queue_slot_va))
    a.raw(b'\x85\xFF'); a.jz('spawn_crit_leave')
    a.raw(b'\xC6\x47\xFF\x00')
    a.raw(b'\xC6\x07\x00')
    a.raw(b'\xC7\x47\x03\x00\x00\x00\x00')
    a.raw(b'\xC7\x47\x07\x00\x00\x00\x00')
    a.raw(b'\xC7\x05' + le32(my_queue_slot_va) + le32(0))
    a.label('spawn_crit_leave')
    leave_cs(a)
    a.label('spawn_done')
    a.raw(b'\xC3')                                            # ret
