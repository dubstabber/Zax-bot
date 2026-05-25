"""Engine virtual addresses, prologue bytes, IAT slots, and vtable constants.

Every symbol here references a fixed location inside the original 2001
``Zax.exe`` image. They are intentionally grouped by subsystem and free of any
patch-policy logic so consumers (`patch_manifest`, hook payload builders,
detour modules) can import them as plain constants.
"""

# --- PE image base ---------------------------------------------------------
IMAGE_BASE = 0x400000

# --- WM_KEYDOWN dispatcher hook --------------------------------------------
HOOK_SITE_VA      = 0x599A1A   # 'call sub_599580' in WM_KEYDOWN handler (ECX = VK)
ORIG_TARGET_VA    = 0x599580   # sub_599580: VK -> internal keyid translator

# --- World/manager chain ---------------------------------------------------
MANAGER_GLOBAL_VA = 0x713F14   # dword_713F14: game/world manager
VT_OFFSET_TO_LVL  = 0x184      # mgr->vtbl[0x184]() -> active level/match object
MP_DATA_FIELD     = 0x30       # level->[+0x30] = live CMultiPlayerGameData (NULL outside MP)
SHOWMSG_VA        = 0x59B260   # __stdcall sub_59B260(char* text, int type); type=-1 => system msg
WORLDMGR_GLOBAL   = 0x6C2080   # dword_6C2080: world/entity manager (holds player list)
SESSION_GLOBAL    = 0x713F18   # dword_713F18: session/participant container (vtable 0x602fa8)
VT_GET_PLAYERLIST = 0xB0       # plm->vtbl[176]() -> player list
VT_LIST_COUNT     = 0x2C       # list->vtbl[44]() -> count
VT_LIST_GET       = 0x34       # list->vtbl[52](i) -> player i

# --- Participant + stats ---------------------------------------------------
PARTICIPANT_FACTORY = 0x5BA790 # __stdcall sub_5BA790(connId) -> new 280B participant
SUB_5BA820          = 0x5BA820 # sub_5BA820(participant) -> stats (auto-syncs via dword_6C2080->vtbl[4])

# --- sub_59DF90 (per-player char create+place) -----------------------------
DF90_VA       = 0x59DF90  # sub_59DF90(this=mgr, a2, index, name, a5); retn 0x10
DF90_RESUME   = 0x59DF95  # resume after displaced 5 bytes (at 'push esi')
DF90_PROLOGUE = b'\x53\x8B\x5C\x24\x0C'  # push ebx; mov ebx,[esp+0xC]

# --- sub_480BD0 (DP per-frame poll) ----------------------------------------
POLL_VA       = 0x480BD0  # DP-manager per-frame poll; ecx = DP manager
POLL_RESUME   = 0x480BD6  # resume after displaced 6 bytes (at 'call Sleep')
POLL_PROLOGUE = b'\x51\x56\x8B\xF1\x6A\x00'  # push ecx; push esi; mov esi,ecx; push 0

# --- char-array helpers (sub_4F5D60 / 4F5D80) ------------------------------
SUB_4F5D60    = 0x4F5D60  # __thiscall(mgr, idx) -> mgr->charArray[idx]
SUB_4F5D80    = 0x4F5D80  # __thiscall(mgr) -> charArray[0] (host char in MP)

# --- AI helpers (currently unused but reserved) ----------------------------
SUB_42E0F0    = 0x42E0F0  # () -> CApproachTargetAI class descriptor
SUB_42E150    = 0x42E150  # () -> new CApproachTargetAI instance (216 B)

# --- Component attach (sub_4FBC50) -----------------------------------------
SUB_4FBC50    = 0x4FBC50  # __thiscall(char, component) ret 4; attaches comp to char
FBC50_VA      = SUB_4FBC50
FBC50_RESUME  = 0x4FBC57
VT_ADD_COMP   = 0x100     # char->vtbl[0x100](classdesc, a3) -> FIND component by class

# --- sub_59BE20 (CPlayerWalkingControlAI ctor; currently not detoured) -----
BE20_VA       = 0x59BE20  # sub_59BE20(char, index); __stdcall ret 8
BE20_RESUME   = 0x59BE26
BE20_PROLOGUE = b'\x56\xB9\x20\x01\x00\x00'  # push esi; mov ecx,0x120

# --- sub_5AA4E0 (CCameraTrakerAI ctor) -------------------------------------
SAA4E0_VA     = 0x5AA4E0
SAA4E0_RESUME = 0x5AA4E8   # after displaced 8 bytes (at 'mov ecx, esi')
SUB_4FD060_VA = 0x4FD060   # call re-emitted from sub_5AA4E0 prologue

# --- sub_542360 (movement-vector helper) -----------------------------------
S542360_VA       = 0x542360
S542360_RESUME   = 0x542365
S542360_PROLOGUE = b'\x83\xEC\x10\x53\x55'

# --- Positional sound wrappers (currently not detoured) --------------------
S4EA880_VA       = 0x4EA880
S4EA880_RESUME   = 0x4EA886
S4EA880_PROLOGUE = b'\x83\xEC\x08\x56\x8B\xF1'
S4EAA60_VA       = 0x4EAA60
S4EAA60_RESUME   = 0x4EAA66
S4EAA60_PROLOGUE = b'\x83\xEC\x08\x56\x8B\xF1'

# --- Live entity position helper (used by bot fire/aim detour) -------------
SUB_4FB0A0_VA  = 0x4FB0A0   # position getter; sub_4FB0A0(char, &out_pos)
SUB_4FC6F0_VA  = 0x4FC6F0   # walks parent chain at +0x0C to find root
S4FB0A0_RESUME = 0x4FB0A6

# --- sub_5436F0 (input -> fire/aim gate) -----------------------------------
S5436F0_VA       = 0x5436F0
S5436F0_RESUME   = 0x5436F7
S5436F0_PROLOGUE = b'\x53\x56\x8B\xF1\x57\x6A\x18'

# --- atan2-ish helper (used by bot fire/aim) -------------------------------
SUB_509100    = 0x509100  # __stdcall(dy, dx) -> angle in ST0

# --- Engine line-of-sight check (used by monster AI fire decision) ---------
# __thiscall(this=src_char, target_char, a3, target_off, a5, src_off) -> al
# Returns 1 if the swept trace from src to target ends at target with no wall
# in between (and the target's vtable[77] filter accepts src); 0 otherwise.
# Canonical caller pattern (sub_46E890): sub_491380(src, tgt, 0, NULL, 2, NULL)
SUB_491380_VA = 0x491380

# --- sub_542550 (controller player_num init) -------------------------------
S542550_VA     = 0x542550
S542550_RESUME = 0x542557  # after displaced 7 bytes

# --- sub_4EF900 garbage-slot defang ----------------------------------------
S4EF900_TEST_VA      = 0x4EF90F
S4EF900_TEST_RESUME  = 0x4EF920
S4EF900_TEST_ORIG    = b'\x85\xC9\x74\x0D\x68'
VALUENAME_VA         = 0x62E558
SUB_4FC200_VA        = 0x4FC200

# --- sub_4FC7C0 / sub_417390 garbage-this defang --------------------------
S4FC7C0_VA           = 0x4FC7C0
S4FC7C0_RESUME       = 0x4FC7C5
S4FC7C0_ORIG         = b'\x8B\x41\x08\x85\xC0'

S417390_VA           = 0x417390
S417390_RESUME       = 0x417395
S417390_ORIG         = b'\x8B\x49\x04\x85\xC9'

# --- sub_4F5D60 count-load cap (not currently patched) ---------------------
S4F5D60_LOAD_VA      = 0x4F5D64
S4F5D60_LOAD_ORIG    = b'\x8B\x91\x94\x02\x00\x00'
S4F5D60_LOAD_CAP     = 32

# --- sub_4F5150 char-iter null-skip ---------------------------------------
S4F5204_VA           = 0x4F5204
S4F5204_RESUME_VA    = 0x4F520A
S4F5204_SKIP_VA      = 0x4F522E
S4F5204_LEN          = 6
S4F5204_ORIG         = b'\x8B\x04\xBA\x8B\x48\x48'

# --- sub_480800 name-block + DP name queries -------------------------------
S480800_DPQ1_VA       = 0x4808A1
S480800_DPQ1_END_VA   = 0x4808B6
S480800_DPQ1_LEN      = S480800_DPQ1_END_VA - S480800_DPQ1_VA  # 21
S480800_DPQ1_ORIG     = b'\xFF\x52\x24\x3D\x00'
S480800_DPQ2_VA       = 0x4808F3
S480800_DPQ2_END_VA   = 0x480901
S480800_DPQ2_LEN      = S480800_DPQ2_END_VA - S480800_DPQ2_VA  # 14
S480800_DPQ2_ORIG     = b'\xFF\x51\x24\x85\xC0'
SUB_47F350_VA         = 0x47F350
S480800_NAMEBLK_VA       = 0x480889
S480800_NAMEBLK_ORIG     = b'\xA1\x2C\xDC\x6B\x00'
S480800_NAMEBLK_AFTER_VA = 0x48088E
S480800_NAMEBLK_END_VA   = 0x480993

# --- sub_5AC230 wrap (count-bump after sub_59DF90) -------------------------
S5AC299_CALL_VA   = 0x5AC299
S5AC299_CALL_ORIG = b'\xFF\x93\xC4\x01\x00\x00'

# --- sub_480800 (DirectPlay join handler) ----------------------------------
SUB_480800_VA = 0x480800   # ecx=dpmgr, edi=host_char; consumes our DP-queue entry

# --- sub_4F1050 (mgr -> active char) ---------------------------------------
SUB_4F1050_VA = 0x4F1050   # __thiscall(mgr) -> active char ptr (0 if none)

# --- operator new / delete (used by mgr+0x290 pre-grow) --------------------
OP_NEW_VA     = 0x5D034A   # __cdecl operator new(size_t) -> ptr in eax
OP_DELETE_VA  = 0x5D0330   # __cdecl operator delete(ptr)

# --- DP manager queue field layout (sub_47EE70 / sub_480BD0) ---------------
DPMGR_NETGAME_FIELD   = 0x08        # dpmgr+8 -> hosted net-game descriptor
NETGAME_MAX_PLAYERS   = 0x0C        # descriptor+0x0C -> advertised maxplayers

# --- RNG + CString helpers -------------------------------------------------
RNG_OBJ_VA            = 0x7124C0    # dword_7124C0 — engine's RNG instance
RNG_SUB               = 0x55C4E0    # sub_55C4E0(this, low, high, opt)
SUB_4E1930_VA         = 0x4E1930    # CString::operator=(this, char* Source)

# --- Game-type vtables (detect_mode lookup) --------------------------------
VT_DM_VA  = 0x5F0D54  # CDeathMatchGameType vtable
VT_CTF_VA = 0x5EF544  # CCaptureTheFlagGameType vtable
VT_SK_VA  = 0x5FED48  # CSalvageKingGameType vtable

# --- Active-gametype getter (used by detect_mode) --------------------------
# `sub_59FF90(ecx=mgr)` returns the active CMultiPlayerGameType-derived
# instance (or NULL). [result+0] is one of `VT_DM_VA`/`VT_CTF_VA`/`VT_SK_VA`.
# Found via sub_5BAD10 which uses this to emit a "gametype" property string.
# Note: not to be confused with mpd (`[level+0x30]`), which is the
# polymorphic `CMultiPlayerGameData` *base* and shares its vtable across
# all modes — see the [[mode-detection-mpd-pitfall]] memory.
SUB_59FF90_VA = 0x59FF90

# --- Virtual-key codes used by the dispatcher ------------------------------
VK_ESC = 0x1B
VK_B   = 0x42
VK_R   = 0x52

# --- KERNEL32 IAT slots ----------------------------------------------------
IMP_CREATEFILEA = 0x5EA0D4
IMP_WRITEFILE   = 0x5EA0DC
IMP_CLOSEHANDLE = 0x5EA0D8
IMP_SETFILEPTR  = 0x5EA054
IMP_ENTERCS     = 0x5EA098
IMP_LEAVECS     = 0x5EA094

# --- DirectPlay manager CritSec --------------------------------------------
DP_CRITSECT_VA = 0x6BDBF0
