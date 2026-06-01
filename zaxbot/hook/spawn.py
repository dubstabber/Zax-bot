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
  5. Bind the bot's team in stats+0x14 (mode-specific: see comment in body).
  6. Pre-spawn: roll the bot's name/color idx and write color1/color2 into
     pcfg so SK's collector binding picks them up at character creation.
  7. Pre-grow ``mgr+0x290`` to capacity 16 if still at initial size (avoids
     the 9th-char OOB crash; see [[garbage-slot-crash]] memory).
  8. Call ``sub_59DF90(mgr, a2, botidx, 0, 0)`` to create + place the char.
  9. Copy the picked bot name into the participant's stats CString.
 10. Cache the bot's character pointer in the ``bot_chars`` scratch table.
 11. Apply color1/color2 floats to the appearance child and let the gametype's
     vtable[+0x9C] override color1 (CTF only).
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
    bot_team_va         = layout.va('bot_team')
    host_part_va        = layout.va('host_part')
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
    bot_colors_va       = layout.va('bot_colors')
    picked_name_idx_va  = layout.va('picked_name_idx')
    used_names_va       = layout.va('used_names')
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

    # --- Bind bot team (stats+0x14). Mode-specific:
    # - CTF (menu_mode == 1): chosen team verbatim ({0=Blue, 1=Red} per the
    #   resolver at sub_4698B0).
    # - SK  (menu_mode == 2): `botidx` — unique per bot AND within [0, 16),
    #   the valid range for per-player collector ownership. Using
    #   `slot + 0x10` here makes every bot's team id land outside that range,
    #   which makes the engine fall back to a single shared collector (the
    #   "one bot has a collector, the rest are red" symptom observed in
    #   12-bot SK matches). Bots still all have *different* team ids, so
    #   sub_51D400 doesn't mis-label cross-bot kills as TEAMMATE.
    # - DM  (default): `slot + 0x10` to dodge sub_51D400's TEAMMATE mis-label
    #   when a bot kills a real client (host=0, PC2=1, …). DM has no per-
    #   player collector, so the out-of-range id doesn't bite anything.
    a.raw(b'\x83\x3D' + le32(botp_va) + b'\x00')
    a.jz('spawn_skip_team')
    a.raw(b'\x8B\x0D' + le32(botidx_va))                     # ecx = botidx (arg to sub_5BA820)
    a.call_va(ax.SUB_5BA820)                                 # eax = bot's stats
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_team')
    a.raw(b'\x83\x3D' + le32(menu_mode_va) + b'\x01')        # cmp menu_mode, 1 (CTF?)
    a.jz('spawn_team_chosen')
    a.raw(b'\x83\x3D' + le32(menu_mode_va) + b'\x02')        # cmp menu_mode, 2 (SK?)
    a.jz('spawn_team_sk')
    a.raw(b'\x8B\x15' + le32(active_bot_slot_va))            # DM: team = slot + 0x10
    a.raw(b'\x83\xC2\x10')
    a.jmp('spawn_team_write')
    a.label('spawn_team_sk')
    a.raw(b'\x8B\x15' + le32(botidx_va))                     # SK: team = botidx (unique in [0, 16))
    a.jmp('spawn_team_write')
    a.label('spawn_team_chosen')
    a.raw(b'\x8B\x15' + le32(chosen_team_va))                # CTF: chosen_team (0=Blue, 1=Red)
    a.label('spawn_team_write')
    a.raw(b'\x89\x50\x14')                                   # mov [eax+0x14], edx
    # Mirror the team into our scratch cache so the per-frame fire/aim detour
    # can read it without re-entering the engine's worldmgr sync (which
    # iterates the char array and is unsafe to spin in a hot loop).
    a.raw(b'\xA1' + le32(active_bot_slot_va))                # mov eax, [active_bot_slot]
    a.raw(b'\x89\x14\x85' + le32(bot_team_va))               # mov [bot_team + eax*4], edx
    logc(ord('T'))
    a.label('spawn_skip_team')

    # --- Pre-spawn: pick the bot's name/color idx and write color1/color2
    # into the bot's pcfg (*(stats+0x1C)+4/+8) BEFORE sub_59DF90 runs. SK's
    # collector binding reads pcfg.color1 during character creation; writing
    # only after sub_59DF90 leaves every bot's collector yellow until it dies
    # and respawns (the engine re-reads pcfg on respawn, hiding the bug).
    # The picked idx is stashed in picked_name_idx_va and reused by the
    # post-spawn name and appearance writes — they must NOT re-roll RNG, or
    # the collector (painted from the pre-spawn pcfg) and the character
    # sprite (painted from the post-spawn appearance) would disagree.
    a.raw(b'\x83\x3D' + le32(botp_va) + b'\x00')              # cmp [botp], 0
    a.jz('spawn_skip_prewrite')
    a.raw(b'\x6A' + bytes([cfg.NUM_BOT_NAMES - 1]))           # push (NUM-1)
    a.raw(b'\x6A\x00')                                         # push 0
    a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                       # mov ecx, RNG
    a.call_va(ax.RNG_SUB)                                      # eax = idx in [0,NUM-1]
    # Ensure the picked name is unique across all live bots in this match.
    # used_names[i] != 0 means slot i is already claimed. Linear-scan forward
    # from the rolled idx (wrapping once) for the first free slot; that's the
    # one we claim. Worst case after NUM steps every slot is taken — which can
    # only happen if more bots than names live at once (MAX_BOT_SLOTS bots vs
    # NUM_BOT_NAMES names) — in which case we fall through and re-use the
    # rolled idx so spawning still succeeds. The claim is cleared on match
    # change by detour_df90 (alongside the bot scratch arrays), so each new
    # match starts with every name available again.
    a.raw(b'\xB9' + le32(cfg.NUM_BOT_NAMES))                  # mov ecx, NUM_BOT_NAMES
    a.label('uname_search')
    a.raw(b'\x80\xB8' + le32(used_names_va) + b'\x00')        # cmp byte [eax+used_names], 0
    a.jz('uname_found')
    a.raw(b'\x40')                                             # inc eax
    a.raw(b'\x3D' + le32(cfg.NUM_BOT_NAMES))                  # cmp eax, NUM_BOT_NAMES
    a.jb('uname_no_wrap')
    a.raw(b'\x31\xC0')                                         # xor eax, eax  (wrap to 0)
    a.label('uname_no_wrap')
    a.raw(b'\x49')                                             # dec ecx
    a.jnz('uname_search')
    a.label('uname_found')
    a.raw(b'\xC6\x80' + le32(used_names_va) + b'\x01')        # mov byte [eax+used_names], 1
    a.raw(b'\xA3' + le32(picked_name_idx_va))                 # mov [picked_name_idx], eax
    a.raw(b'\x8B\x0D' + le32(botidx_va))                       # ecx = botidx
    a.call_va(ax.SUB_5BA820)                                   # eax = bot stats
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_prewrite')
    a.raw(b'\x8B\x40\x1C')                                     # eax = *(stats+0x1C) = pcfg*
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_prewrite')
    a.raw(b'\x3D' + le32(ax.HOST_PLAYER_CFG_VA))               # cmp eax, &dword_6BD2F8
    a.jz('spawn_skip_prewrite')                                # never clobber host local config
    a.raw(b'\x8B\x15' + le32(picked_name_idx_va))              # edx = idx
    a.raw(b'\xC1\xE2\x03')                                     # shl edx, 3
    a.raw(b'\x81\xC2' + le32(bot_colors_va))                   # add edx, bot_colors
    a.raw(b'\x8B\x0A')                                         # ecx = [edx]    color1 int
    a.raw(b'\x89\x48\x04')                                     # mov [eax+4], ecx
    a.raw(b'\x8B\x4A\x04')                                     # ecx = [edx+4]  color2 int
    a.raw(b'\x89\x48\x08')                                     # mov [eax+8], ecx
    a.label('spawn_skip_prewrite')

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
    # Zero the freshly-allocated buffer so any slots not overwritten by the
    # copy below are NULL. The engine's worldmgr sync (worldmgr->vtbl[+4])
    # iterates this array and unconditionally derefs non-NULL entries via
    # sub_4EF900 -> sub_4FC200; uninitialised heap bytes would crash on
    # `mov ecx, [esi+10h]` (see [[garbage-slot-crash]]).
    a.raw(b'\x50')                                            # push eax (save buf)
    a.raw(b'\x89\xC7')                                        # mov edi, eax
    a.raw(b'\xB9\x10\x00\x00\x00')                            # mov ecx, 16
    a.raw(b'\x31\xC0')                                        # xor eax, eax
    a.raw(b'\xFC\xF3\xAB')                                    # cld; rep stosd
    a.raw(b'\x58')                                            # pop eax (restore buf)
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

    # --- Set the bot's display name via sub_4E1930(stats, ASCII name). Reuses
    # the picked_name_idx set in the pre-spawn block above — must not re-roll
    # or the name will pair with one color and the collector with another.
    a.raw(b'\xA1' + le32(botp_va))                            # mov eax, [botp]
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_name')
    a.raw(b'\xA1' + le32(picked_name_idx_va))                  # eax = picked idx (pre-spawn)
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

    # --- Apply the bot's chosen color1/color2 to its appearance component.
    # See ``apply_bot_colors`` for the full child-walk / gametype-override
    # protocol; this is the post-spawn half (the pre-spawn pcfg mirror that
    # paints the SK collector still lives above, before sub_59DF90).
    a.call_lbl('apply_bot_colors')

    # --- Waypoint follow: force a fresh cold-acquire for the new bot --------
    # We do NOT place the bot here. Runtime R-dumps proved the engine reclaims
    # any direct position we set (the bot can only move via its steering
    # vector), so the bot STEERS from its spawn point onto the graph: the
    # movement detour cold-acquires the nearest node and walks edges. Reset the
    # nav state to -1/-1 so this newly-spawned bot re-acquires cleanly (in case
    # the slot was reused mid-match). Skipped when follow off / no graph / slot
    # out of range.
    wp_follow_enabled_va    = layout.va('wp_follow_enabled')
    overlay_vertex_count_va = layout.va('overlay_vertex_count')
    current_wp_va           = layout.va('bot_current_wp')
    prev_wp_va              = layout.va('bot_prev_wp')
    a.raw(b'\x83\x3D' + le32(wp_follow_enabled_va) + b'\x00')  # cmp [wp_follow_enabled], 0
    a.jz('spawn_skip_wp_place')
    a.raw(b'\x83\x3D' + le32(overlay_vertex_count_va) + b'\x00')  # cmp [vertex_count], 0
    a.jz('spawn_skip_wp_place')                               # no graph
    a.raw(b'\x8B\x0D' + le32(active_bot_slot_va))             # ecx = slot
    a.raw(b'\x83\xF9' + bytes([cfg.MAX_BOT_SLOTS]))           # cmp ecx, MAX_BOT_SLOTS
    a.jae('spawn_skip_wp_place')                              # slot out of range
    a.raw(b'\xC7\x04\x8D' + le32(current_wp_va) + b'\xFF\xFF\xFF\xFF')  # current_wp[slot] = -1
    a.raw(b'\xC7\x04\x8D' + le32(prev_wp_va) + b'\xFF\xFF\xFF\xFF')     # prev_wp[slot] = -1
    a.label('spawn_skip_wp_place')

    # --- Optional FORCE_BOT_ITEM_NAME override (testing aid). Bots can't move
    # and so can't pick up weapons. The engine's item ids are per-inventory
    # local indexes, so this resolves an inventory item definition by name,
    # instantiates that item through the same path as the XmasShopping cheat,
    # adds it to the bot, then switches Primary to the new local item id.
    force_name_va         = layout.va('force_bot_item_name')
    force_item_def_idx_va = layout.va('force_item_def_idx')
    primary_hash_va       = layout.va('primary_hash')

    # --- FORCE_BOT_AMMO_NAMES: stuff the bot with the configured battery +
    # ammo top-up items so the weapon force-equip step below can hand the
    # bot ANY weapon, including ones whose `can-pickup` vtable (item +0x80)
    # rejects the pickup when the bot lacks the matching ammo source — that
    # rejection is why "Alien Electrical Weapon" and similar energy weapons
    # fall back to the default Modified Laser Welder when ammo is given
    # AFTER the weapon. Running the ammo loop first mirrors the XmasShopping
    # cheat's array order (ammo items come before the weapons in
    # sub_5A22C0). The ammo table lives in scratch (force_bot_ammo_names)
    # with the live length in force_bot_ammo_count; both are populated at
    # patch time by static_data.write_static_scratch_data. Each item is
    # given via the engine's clone + can-pickup + pickup protocol; the
    # transient is destroyed after each give, mirroring sub_5A2700's loop.
    if layout.has_field('force_bot_ammo_count'):
        ammo_names_va = layout.va('force_bot_ammo_names')
        ammo_count_va = layout.va('force_bot_ammo_count')
        ammo_slot_size = cfg.FORCE_BOT_AMMO_SLOT_SIZE
        # Slot size must be encoded as a single shl k constant. 32 = shl 5.
        assert ammo_slot_size in (16, 32, 64, 128), (
            f'FORCE_BOT_AMMO_SLOT_SIZE must be a small power of two; '
            f'got {ammo_slot_size}'
        )
        ammo_slot_shift = ammo_slot_size.bit_length() - 1

        # Gate: same FORCE_BOT_ITEM_NAME byte as the weapon path below.
        a.raw(b'\x80\x3D' + le32(force_name_va) + b'\x00')        # cmp byte [force_bot_item_name], 0
        a.jz('spawn_force_ammo_done')
        a.raw(b'\x83\x3D' + le32(ammo_count_va) + b'\x00')        # cmp [ammo_count], 0
        a.jz('spawn_force_ammo_done')
        a.raw(b'\xA1' + le32(botchar_va))                         # eax = bot char
        a.raw(b'\x85\xC0')
        a.jz('spawn_force_ammo_done')

        # EBX = loop index (callee-saved across the engine calls below).
        a.raw(b'\x31\xDB')                                        # xor ebx, ebx

        a.label('spawn_force_ammo_loop')
        a.raw(b'\x3B\x1D' + le32(ammo_count_va))                  # cmp ebx, [ammo_count]
        a.jae('spawn_force_ammo_done')

        # EDI = name pointer = ammo_names_va + ebx * slot_size.
        a.raw(b'\x89\xDF')                                        # mov edi, ebx
        a.raw(b'\xC1\xE7' + bytes([ammo_slot_shift]))             # shl edi, log2(slot)
        a.raw(b'\x81\xC7' + le32(ammo_names_va))                  # add edi, names_va
        a.raw(b'\x80\x3F\x00')                                    # cmp byte [edi], 0
        a.jz('spawn_force_ammo_next')

        # def_idx = sub_523DF0(this=registry, name, -1).
        a.raw(b'\x6A\xFF')                                        # push -1
        a.raw(b'\x57')                                            # push edi
        a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))
        a.call_va(ax.SUB_523DF0_VA)
        a.raw(b'\xA3' + le32(force_item_def_idx_va))              # save def_idx

        # def = sub_48D8F0(registry, def_idx).
        a.raw(b'\x50')                                            # push def_idx (eax)
        a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))
        a.call_va(ax.SUB_48D8F0_VA)
        a.raw(b'\x85\xC0')
        a.jz('spawn_force_ammo_next')
        a.raw(b'\x89\xC7')                                        # mov edi, eax (def)

        # Type guard: only CZaxInventoryItemDefinitions are giveable.
        a.call_va(ax.SUB_5B5F20_VA)
        a.raw(b'\x50')
        a.raw(b'\x8B\xCF')                                        # mov ecx, edi
        a.call_va(ax.SUB_416790_VA)
        a.raw(b'\x84\xC0')
        a.jz('spawn_force_ammo_next')

        # Create transient item: clone the def's default entity if present
        # (sub_5A2700's preferred path), else allocate a generic CInventoryItem.
        a.raw(b'\x8B\x47\x1C')                                    # mov eax, [edi+0x1C]
        a.raw(b'\x85\xC0')
        a.jz('spawn_force_ammo_generic')
        a.raw(b'\x8B\xCF')                                        # mov ecx, edi
        a.call_va(ax.SUB_5B7AB0_VA)
        a.raw(b'\x8B\xC8')                                        # mov ecx, eax
        a.raw(b'\x6A\xFF')                                        # push -1
        a.raw(b'\x6A\x00')                                        # push 0
        a.call_va(ax.SUB_416760_VA)
        a.raw(b'\x85\xC0')
        a.jz('spawn_force_ammo_next')
        a.raw(b'\x89\xC5')                                        # mov ebp, eax (item)
        a.jmp('spawn_force_ammo_ready')

        a.label('spawn_force_ammo_generic')
        a.raw(b'\xB9\x18\x00\x00\x00')                            # mov ecx, 0x18
        a.call_va(ax.SUB_417710_VA)
        a.raw(b'\x85\xC0')
        a.jz('spawn_force_ammo_next')
        a.raw(b'\x8B\xC8')                                        # mov ecx, eax
        a.call_va(ax.SUB_42A2B0_VA)
        a.raw(b'\x85\xC0')
        a.jz('spawn_force_ammo_next')
        a.raw(b'\x89\xC5')                                        # mov ebp, eax (item)
        a.raw(b'\x8B\x55\x00')                                    # mov edx, [ebp]
        a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
        a.raw(b'\xFF\x52\x4C')                                    # call [edx+0x4C]

        a.label('spawn_force_ammo_ready')
        # Bind the resolved def_idx onto the transient item.
        a.raw(b'\xFF\x35' + le32(force_item_def_idx_va))
        a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
        a.call_va(ax.SUB_54FDB0_VA)
        a.raw(b'\x8B\x55\x00')                                    # mov edx, [ebp]
        a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
        a.raw(b'\xFF\x52\x1C')                                    # call [edx+0x1C] (init)

        # Can-pickup?  [item.vtbl+0x80](this=item, char) -> bool
        a.raw(b'\x8B\x45\x00')                                    # mov eax, [ebp]
        a.raw(b'\xFF\x35' + le32(botchar_va))
        a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
        a.raw(b'\xFF\x90' + le32(0x80))
        a.raw(b'\x84\xC0')
        a.jz('spawn_force_ammo_destroy')

        # Pickup: [item.vtbl+0x84](this=item, 0, char). Mirrors sub_5A2700.
        a.raw(b'\x8B\x45\x00')                                    # mov eax, [ebp]
        a.raw(b'\xFF\x35' + le32(botchar_va))                     # push char (a4)
        a.raw(b'\x6A\x00')                                        # push 0 (a3)
        a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
        a.raw(b'\xFF\x90' + le32(0x84))

        a.label('spawn_force_ammo_destroy')
        # Free the transient: [item.vtbl+0x04](this=item, 1).
        a.raw(b'\x85\xED')                                        # test ebp, ebp
        a.jz('spawn_force_ammo_next')
        a.raw(b'\x8B\x45\x00')                                    # mov eax, [ebp]
        a.raw(b'\x6A\x01')                                        # push 1 (free)
        a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
        a.raw(b'\xFF\x50\x04')

        a.label('spawn_force_ammo_next')
        a.raw(b'\x43')                                            # inc ebx
        a.jmp('spawn_force_ammo_loop')

        a.label('spawn_force_ammo_done')

    a.raw(b'\x80\x3D' + le32(force_name_va) + b'\x00')        # cmp byte [force_bot_item_name], 0
    a.jz('spawn_skip_force_weapon')
    a.raw(b'\xA1' + le32(botchar_va))                         # eax = bot char
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_force_weapon')
    a.raw(b'\x8B\xC8')                                        # mov ecx, eax (this = bot char)
    a.call_va(ax.SUB_4267E0_VA)                               # eax = inventory
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_force_weapon')
    a.raw(b'\x89\xC6')                                        # mov esi, eax (save inventory)

    # Resolve configured item definition from the inventory item-definition
    # registry: idx = sub_523DF0(name, -1); def = sub_48D8F0(registry, idx).
    a.raw(b'\x6A\xFF')                                        # push -1
    a.raw(b'\x68' + le32(force_name_va))                      # push force_bot_item_name
    a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))            # mov ecx, item-definition registry
    a.call_va(ax.SUB_523DF0_VA)
    a.raw(b'\xA3' + le32(force_item_def_idx_va))              # save definition index
    a.raw(b'\x50')                                            # push idx
    a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))            # mov ecx, item-definition registry
    a.call_va(ax.SUB_48D8F0_VA)                               # eax = item definition
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_force_weapon')
    a.raw(b'\x89\xC7')                                        # mov edi, eax (item definition)
    # Type guard: item definitions should be CZaxInventoryItemDefinition.
    a.call_va(ax.SUB_5B5F20_VA)
    a.raw(b'\x50')                                            # push class desc
    a.raw(b'\x8B\xCF')                                        # mov ecx, edi
    a.call_va(ax.SUB_416790_VA)
    a.raw(b'\x84\xC0'); a.jz('spawn_skip_force_weapon')

    # If the bot already owns this item definition, skip straight to switch.
    a.raw(b'\x57')                                            # push edi (item definition)
    a.raw(b'\x8B\xCE')                                        # mov ecx, esi (inventory)
    a.call_va(ax.SUB_425900_VA)
    a.raw(b'\x83\xF8\xFF')                                    # cmp eax, -1
    a.jnz('spawn_force_have_local_item')

    # Create a transient pickup item for this definition and apply it only to
    # the new bot. Mirrors the single-item inner loop of sub_5A2700 without
    # giving the item to host/PC2.
    a.raw(b'\x8B\x47\x1C')                                    # mov eax, [edi+0x1C] (default entity)
    a.raw(b'\x85\xC0')                                        # test eax, eax
    a.jz('spawn_force_generic_item')
    a.raw(b'\x8B\xCF')                                        # mov ecx, edi
    a.call_va(ax.SUB_5B7AB0_VA)                               # eax = default entity
    a.raw(b'\x8B\xC8')                                        # mov ecx, eax
    a.raw(b'\x6A\xFF')                                        # push -1
    a.raw(b'\x6A\x00')                                        # push 0
    a.call_va(ax.SUB_416760_VA)                               # eax = cloned item
    a.jmp('spawn_force_item_created')

    a.label('spawn_force_generic_item')
    a.raw(b'\xB9\x18\x00\x00\x00')                            # mov ecx, 0x18
    a.call_va(ax.SUB_417710_VA)                               # eax = memory
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_force_weapon')
    a.raw(b'\x8B\xC8')                                        # mov ecx, eax
    a.call_va(ax.SUB_42A2B0_VA)                               # eax = CInventoryItem
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_force_weapon')
    a.raw(b'\x89\xC5')                                        # mov ebp, eax (item)
    a.raw(b'\x8B\x55\x00')                                    # mov edx, [ebp]
    a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
    a.raw(b'\xFF\x52\x4C')                                    # call [edx+0x4C]
    a.jmp('spawn_force_item_ready')

    a.label('spawn_force_item_created')
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_force_weapon')
    a.raw(b'\x89\xC5')                                        # mov ebp, eax (item)

    a.label('spawn_force_item_ready')
    a.raw(b'\xFF\x35' + le32(force_item_def_idx_va))          # push item definition index
    a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
    a.call_va(ax.SUB_54FDB0_VA)                               # item->definition index = idx
    a.raw(b'\x8B\x55\x00')                                    # mov edx, [ebp]
    a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
    a.raw(b'\xFF\x52\x1C')                                    # call [edx+0x1C] (generate/init)
    a.raw(b'\x8B\x45\x00')                                    # mov eax, [ebp]
    a.raw(b'\xFF\x35' + le32(botchar_va))                     # push bot char
    a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
    a.raw(b'\xFF\x90' + le32(0x80))                           # call [eax+0x80] (can pickup?)
    a.raw(b'\x84\xC0')                                        # test al, al
    a.jz('spawn_force_destroy_item')
    a.raw(b'\x8B\x45\x00')                                    # mov eax, [ebp]
    a.raw(b'\xFF\x35' + le32(botchar_va))                     # push bot char
    a.raw(b'\x6A\x00')                                        # push 0
    a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
    a.raw(b'\xFF\x90' + le32(0x84))                           # call [eax+0x84] (pickup/add)
    a.label('spawn_force_destroy_item')
    a.raw(b'\x85\xED')                                        # test ebp, ebp
    a.jz('spawn_force_after_give')
    a.raw(b'\x8B\x45\x00')                                    # mov eax, [ebp]
    a.raw(b'\x6A\x01')                                        # push 1 (free)
    a.raw(b'\x8B\xCD')                                        # mov ecx, ebp
    a.raw(b'\xFF\x50\x04')                                    # call [eax+4] (delete transient item)

    a.label('spawn_force_after_give')
    a.raw(b'\x57')                                            # push edi (item definition)
    a.raw(b'\x8B\xCE')                                        # mov ecx, esi (inventory)
    a.call_va(ax.SUB_425900_VA)                               # eax = bot-local item index
    a.raw(b'\x83\xF8\xFF')                                    # cmp eax, -1
    a.jz('spawn_skip_force_weapon')

    a.label('spawn_force_have_local_item')
    a.raw(b'\x89\xC3')                                        # mov ebx, eax (local item id)

    # Lazy-init primary_hash if compute_proj_speed hasn't warmed it yet.
    a.raw(b'\x83\x3D' + le32(primary_hash_va) + b'\x00')      # cmp [primary_hash], 0
    a.jnz('spawn_force_have_hash')
    a.raw(b'\x6A\xFF')                                        # push -1
    a.raw(b'\x68' + le32(ax.PRIMARY_STR_VA))                  # push "Primary"
    a.raw(b'\xB9' + le32(ax.SLOT_NAME_REGISTRY_VA))           # mov ecx, registry
    a.call_va(ax.SUB_523DF0_VA)
    a.raw(b'\xA3' + le32(primary_hash_va))                    # mov [primary_hash], eax
    a.label('spawn_force_have_hash')

    # sub_425590(this=inv, item_id, slot_idx, bot_char, 1).
    # It only queues the item when the Primary slot's pending-item field
    # (+0x14) is -1. Bots spawn with a default Primary already pending/current,
    # so clear that pending field first; otherwise sub_425590 returns failure
    # and leaves the default weapon intact.
    a.raw(b'\xA1' + le32(primary_hash_va))                    # mov eax, [primary_hash]
    a.raw(b'\x8B\x56\x10')                                    # mov edx, [esi+0x10] (slot array)
    a.raw(b'\x85\xD2'); a.jz('spawn_skip_force_weapon')
    a.raw(b'\x8D\x04\x40')                                    # lea eax, [eax+eax*2]
    a.raw(b'\xC1\xE0\x03')                                    # shl eax, 3  (*24)
    a.raw(b'\xC7\x44\x02\x14\xFF\xFF\xFF\xFF')                # slot[p].pending = -1

    # a5=1 triggers the engine's auto-equip path, which routes through the
    # positional-sound wrapper at 0x4FC8A0. That wrapper is patched
    # NULL-tolerant in patch_manifest.RawBytePatch so it skips the sound
    # when the bot's audio emitter slot at char+0x48 is NULL (synthetic-DP
    # bots don't have one).
    a.raw(b'\x6A\x01')                                        # push 1 (a5: auto-equip)
    a.raw(b'\xFF\x35' + le32(botchar_va))                     # push bot char (a4)
    a.raw(b'\xFF\x35' + le32(primary_hash_va))                # push [primary_hash] (a3)
    a.raw(b'\x53')                                            # push ebx (a2: local item id)
    a.raw(b'\x8B\xCE')                                        # mov ecx, esi (this = inventory)
    a.call_va(ax.SUB_425590_VA)
    a.raw(b'\x84\xC0')                                        # test al, al
    a.jz('spawn_skip_force_weapon')
    # Force the switch immediately. sub_425590 queues into slot +0x14 and
    # leaves the previous current item at +0x10 until the normal transition
    # completes; bots keep firing the old current item in the meantime.
    a.raw(b'\xA1' + le32(primary_hash_va))                    # mov eax, [primary_hash]
    a.raw(b'\x8B\x56\x10')                                    # mov edx, [esi+0x10] (slot array)
    a.raw(b'\x85\xD2'); a.jz('spawn_skip_force_weapon')
    a.raw(b'\x8D\x04\x40')                                    # lea eax, [eax+eax*2]
    a.raw(b'\xC1\xE0\x03')                                    # shl eax, 3  (*24)
    a.raw(b'\x89\x5C\x02\x10')                                # slot[p].current = local item id
    a.raw(b'\xC7\x44\x02\x14\xFF\xFF\xFF\xFF')                # slot[p].pending = -1
    a.raw(b'\xC7\x44\x02\x0C\x00\x00\x00\x00')                # slot[p].switch_timer = 0
    logc(ord('W'))

    a.label('spawn_skip_force_weapon')

    a.label('spawn_skipai')

    # Capture host's participant ptr once per match (sentinel 0 = unset).
    # fire/aim reads team live from `*(host_part+0x14)` each frame so a
    # mid-match team switch (CTF blue→red) takes effect immediately
    # without re-spawning. Doing this AFTER sub_59DF90 has run for the new
    # bot keeps the worldmgr's internal char-array sync in a known-good
    # state — calling sub_5BA820(0) earlier crashes inside the auto-sync
    # via the [[garbage-slot-crash]] path.
    a.raw(b'\x83\x3D' + le32(host_part_va) + b'\x00')        # cmp [host_part], 0
    a.jnz('spawn_skip_host_part')
    a.raw(b'\x31\xC9')                                        # xor ecx, ecx (host idx = 0)
    a.call_va(ax.SUB_5BA820)                                  # eax = host participant
    a.raw(b'\x85\xC0'); a.jz('spawn_skip_host_part')
    a.raw(b'\xA3' + le32(host_part_va))                       # mov [host_part], eax
    a.label('spawn_skip_host_part')

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
