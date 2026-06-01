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
FLT_ZERO_VA   = 0x5EBED0  # engine float 0.0 constant
FLT_TWO_PI_VA = 0x5EDFFC  # engine float 2*pi constant

# --- Weapon lookup chain (used by bot fire/aim for per-weapon lead speed) --
# Replicates the lookup sub_543830 itself uses to find the firing weapon:
#   inv  = sub_4267E0(this=char)                                 ; inventory
#   hash = sub_523DF0(this=SLOT_NAME_REGISTRY, "Primary", -1)    ; slot hash
#   item = sub_425290(this=inv, hash)                            ; item id
#   wpn  = inv.vtable[+0x68](this=inv, item)                     ; weapon obj
SUB_4267E0_VA              = 0x4267E0   # __thiscall(char) -> inventory*
SUB_523DF0_VA              = 0x523DF0   # __thiscall(registry, char* name, int) -> hash; ret 8
SUB_425290_VA              = 0x425290   # __thiscall(inventory, hash) -> item; ret 4
SUB_425900_VA              = 0x425900   # __thiscall(inventory, item_def*) -> item; ret 4
SUB_4DD480_VA              = 0x4DD480   # __thiscall(item_obj) -> inventory item definition*
SLOT_NAME_REGISTRY_VA      = 0x6C0800   # ECX setup for sub_523DF0 (engine global)
PRIMARY_STR_VA             = 0x60B780   # ASCII "Primary\0..."
INVENTORY_GET_WEAPON_OFF   = 0x68       # inv.vtable[+0x68](this, item) -> weapon obj
# The generic inventory item vtable at [weapon + 0x00] is shared by multiple
# weapon objects, so per-weapon lead-speed dispatch keys off sub_4DD480(wpn).

# --- CInventoryItemDefinition / CModel field offsets (used for per-weapon -
# projectile-speed and hitscan detection in compute_proj_speed). Field offsets
# are taken from the engine's own schema-init at sub_4D5620 (CInventoryItem
# Definition, registry dword_6C0D54) and sub_5159B0 (CModel, registry
# dword_6CFDD4). [def + PROJ_PROTO_OFF] is a registry KEY (small integer,
# NOT a resolved pointer) — sub_54E560's field-descriptor type stores keys
# that resolve lazily via sub_48D8F0(registry, key) -> object*. Same pattern
# the engine uses internally at sub_489A40:0x489a57. Key == 0 means the def
# didn't define a projectile reference (hitscan weapon).
PROJ_PROTO_OFF             = 0x20       # CInventoryItemDefinition "Projectiles/Projectile" key (resolve via sub_48D8F0 on MODEL_REGISTRY_VA)
MODEL_MAX_VEL_OFF          = 0x60       # CModel "Move/Max Velocity" (float, pixels/sec)
MODEL_REGISTRY_VA          = 0x6CFDD8   # CModel registry (pass as `this` to sub_48D8F0)

# --- Add/switch weapon inventory helpers (used by force-weapon testing) ----
ITEM_DEF_REGISTRY_VA        = 0x6C0C08   # inventory item-definition registry
SUB_48D8F0_VA               = 0x48D8F0   # __thiscall(registry, index) -> definition*
SUB_5B5F20_VA               = 0x5B5F20   # () -> CZaxInventoryItemDefinition class desc
SUB_416790_VA               = 0x416790   # __thiscall(obj, class_desc) -> bool is-a
SUB_5B7AB0_VA               = 0x5B7AB0   # __thiscall(item_def) -> default entity
SUB_416760_VA               = 0x416760   # __thiscall(default_entity, 0, -1) -> clone item
SUB_417710_VA               = 0x417710   # __thiscall(size_in_ecx) -> operator new-ish
SUB_42A2B0_VA               = 0x42A2B0   # __thiscall(mem) -> CInventoryItem ctor
SUB_54FDB0_VA               = 0x54FDB0   # __thiscall(item, def_index) stores item+8; ret 4
# __thiscall(this=inv, item_id, slot_hash, a4, a5); ret 0x10.
# Args matched against the engine's own call at 0x543ACD: push 1; push ebx;
# push -1; push "Primary"; mov ecx, registry; call sub_523DF0; push eax (=hash);
# push edi (=item_id); mov ecx, esi (=inv); call sub_425590.
# a4: owner char pointer (engine passes the firing/owning char at 0x543ACD).
# a5: auto-equip flag (1 = make this the active weapon).
SUB_425590_VA              = 0x425590

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
# `sub_4FC7C0(char) -> child-list count` and `sub_4FC7D0(char, idx) -> child
# entity[idx]` are also called at bot-spawn time to reach the appearance
# component (which lives on the player char's first child, not on the char
# itself — see notes near APPEARANCE_CLASS_VA).
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

# --- World-manager entity array (used by bot movement to enumerate hazards
# and pickups). Distinct from the per-class char array at `mgr + 0x290`: this
# is the master list of every spawned entity (chars, pickups, damage zones,
# projectiles, etc.). Confirmed via sub_4F1050 / sub_4F0C70 disasm — the
# latter decrements `[esi+0x2C0]` when an entity is removed. Iteration form
# from `sub_4F1050`:
#   count = *(u32*)(mgr + 0x2C0)
#   arr   = *(u32*)(mgr + 0x2BC)
#   for (i in 0..count): ent = arr[i*4]   # array of DWORD ptrs, NULL = gap
WORLDMGR_ENT_LIST_OFF  = 0x2BC
WORLDMGR_ENT_COUNT_OFF = 0x2C0

# --- AI-component class descriptors (used by world_scan to identify which
# entities are hazards vs pickups). Each accessor lazy-inits the global on
# first call and then returns the cached pointer; calling the accessor at
# scan time is safer than reading the global cold.
CPICKUP_AI_CLASS_VA          = 0x6D0B9C   # dword_6D0B9C — CPickupAI class descriptor
CPICKUP_AI_ACCESSOR_VA       = 0x53D190   # () -> dword_6D0B9C (lazy init)
CDAMAGE_RADIUS_AI_CLASS_VA   = 0x6BD74C   # dword_6BD74C — CDamageExpandingRadiusAI class descriptor
CDAMAGE_RADIUS_AI_ACCESSOR_VA = 0x4764A0  # () -> dword_6BD74C (lazy init)

# CEntityDestructable schema field: "Cur Damage" — float at char + 0x7C —
# accumulates total damage taken. Snapshotted as 83.94 on a bot that walked
# over lava, vs 0.0 on the unhurt host. Used by detour_542360 for reactive
# hazard avoidance: when cur_damage increases between frames the bot took
# damage from SOMETHING (lava, fire, weapon, etc.) and we force an immediate
# wander retarget. Source: sub_48C380 (CEntityDestructable schema init).
CHAR_CUR_DAMAGE_OFF = 0x7C

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

# --- Per-character appearance + color application --------------------------
# The engine's `sub_5ABE80` (server-side "ClientOptionsToServer" handler)
# applies color updates by walking the char's first child entity:
#
#   if (sub_4FC7C0(char) > 0)        target = sub_4FC7D0(char, 0)
#   else                              target = char
#   app = sub_418790(class=*(0x6C0520), target)
#   if (app) {  *(float*)(app+0x0C) = color1;  *(float*)(app+0x18) = color2;  }
#
# `sub_418790` is `__thiscall` and pops its stack arg (`retn 4`). Querying
# appearance on the *player* char itself returns NULL — appearance lives on
# the child entity. We mirror this exact path at bot-spawn time.
APPEARANCE_CLASS_VA   = 0x6C0520    # dword_6C0520: player look class descriptor
SUB_418790_VA         = 0x418790    # __thiscall(class, char) -> appearance* (or NULL)
SUB_4FC7C0_VA         = 0x4FC7C0    # __thiscall(char) -> child-list count (0 if none)
SUB_4FC7D0_VA         = 0x4FC7D0    # __thiscall(char, idx) -> child entity[idx]
APPEARANCE_COLOR1_OFF = 0x0C        # float color1 within appearance struct
APPEARANCE_COLOR2_OFF = 0x18        # float color2 within appearance struct

# The active game-type's vtable[39] is a `(this, stats, *color1)` callback
# that, for CTF (`sub_4698B0`), overwrites `*color1` with the team hue
# (Blue Hue at +244, Red Hue at +248) when the "Force Team Colors On
# Players" flag at +240 is set. DM and SK install a `nullsub_3` here so
# calling it unconditionally is safe and replicates the engine's own
# behavior in `sub_5ABE80` (the close-config handler).
GAMETYPE_COLOR1_VTBL_OFF = 0x9C

# --- Per-player config struct (color persistence) --------------------------
# Each participant has `*(part+0x1C)` = pointer to a CPlayerConfig-like
# struct with color1 at `+4` and color2 at `+8` (same layout as the host
# local config at `dword_6BD2F8`). The renderer doesn't read this directly,
# but the engine's join / sync code does; keeping it in sync with the bot's
# applied colors prevents the next match's setup from reverting them.
HOST_PLAYER_CFG_VA    = 0x6BD2F8    # dword_6BD2F8 — host local config (guard against clobber)

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
VK_ESC   = 0x1B
VK_B     = 0x42
VK_J     = 0x4A
VK_N     = 0x4E
VK_R     = 0x52
VK_X     = 0x58
VK_COMMA = 0xBC   # VK_OEM_COMMA — used for wp_save (S is bound to "move down" in-game)

# --- CWayPointMap probe (waypoint_diag) ------------------------------------
# `sub_4ECA80` (CLevel::LoadWayPoints) stores the per-level CWayPointMap* at
# `level + 0x134` after loading `Levels/<name>.way` or auto-generating from
# placed CWayPointsPolygon entities. Both `sub_4EBEB0` and `sub_4EC090` call
# the loader, so every level (SP or MP) has this slot populated — but the
# generator only runs when polygon entities exist, so the map may be empty
# on MP maps that were authored without bots in mind. Each CWayPointsPolygon
# is a 120-byte world entity living in the standard `mgr+0x2BC/0x2C0` array
# with `[+0] = off_602B2C` as its vtable marker; counting them is the
# cleanest "does this map have any waypoint authoring?" probe.
LEVEL_WAYPOINT_MAP_OFF      = 0x134
CWAYPOINTS_POLY_VTABLE_VA   = 0x602B2C

# --- Current-map name CString -----------------------------------------------
# `sub_4F43F0` ("Loading a map") calls `sub_4E1930(&dword_713C14, mapname)`
# during every load, copying the map name into this global CString. It is
# NOT cleared after the load completes (the loader clears the *transient*
# `dword_6C2904` loader-state pointer but leaves `dword_713C14` populated),
# so this is the canonical "currently-loaded map" name at runtime.
#
# CString convention (per sub_4DEC90 / sub_4E1930):
#   header  : single dword pointing at a heap buffer
#   buf[0]  : refcount (u32)
#   buf[4]  : length   (u32)
#   buf[8..]: NUL-terminated ASCII
# So to read the name: `eax = [0x713C14]; if (eax) name = (char*)(eax+8)`.
MAP_NAME_CSTRING_VA         = 0x713C14
MAP_NAME_ASCII_OFFSET       = 8

# --- Overlay rendering -----------------------------------------------------
# `*(RENDERER_OWNER_VA + 4)` is the CGraphics* renderer (vtable off_5FF360,
# set up by sub_567990 inside the CGame ctor at sub_4CD780:0x4cd946). Its
# vtbl[+0xD0] is `sub_568D90` — the raw line drawer that reads the camera
# origin via vtbl[+0xAC]/[+0xB0] and dispatches to depth-specific Bresenham
# fills (sub_42C9E0 32-bit, sub_42C0F0 16-bit, sub_42B6E0 8-bit/palettized).
# All three helpers below take WORLD coords; world->screen is internal.
#
# `sub_4B3CB0(this=renderer, &p1, &p2, &color)` — draws a line between two
#   `float[2]` endpoints. ret 0xC (3 stack args).
# `sub_4FCCC0(this=renderer, edx=&center, radius, aspect, &color)` —
#   draws a closed oval / circle (calls sub_4FCD10 with angle 0..2π).
#   ret 0xC (3 stack args; ECX/EDX passed through to sub_4FCD10).
# `sub_53F010(this=&color_out, r, g, b, a)` — builds an RGBA CColor struct
#   and stamps a palette index via sub_433A10 for 8-bit modes. ret 0x10.
#
# Surface lock/unlock is automatic — `sub_568D90` self-wraps via the global
# `dword_713318` lock flag, so the helpers above are safe to call from any
# frame-aligned hook (page-flip detour, key handler).
RENDERER_OWNER_VA       = 0x6C02CC
RENDERER_OFF_IN_OWNER   = 0x04
SUB_4B3CB0_VA           = 0x4B3CB0
SUB_4FCCC0_VA           = 0x4FCCC0
SUB_53F010_VA           = 0x53F010

# `sub_4F5DA0(this=worldmgr, &out_pos, char_idx)` returns the SMOOTHED
# camera target (`layer + 0xD0` floats) for the given char. The engine's
# CCameraTrakerAI (sub_5AA520 tick) updates `layer + 200/204` with the
# instant tracker target, then `sub_4F5DD0` smooths it into `+208/+212`
# and writes the screen-edge camera `+192/+196 = smoothed - screen/2`.
# Reading smoothed target and subtracting screen/2 here matches the
# engine's per-frame camera calculation exactly — important because
# using the INSTANT host position causes visible parallax wobble when
# the host moves quickly.
SUB_4F5DA0_VA           = 0x4F5DA0

# --- sub_5693A0 (per-frame page flip / windowed Blt) ----------------------
# Called once per frame after all entity rendering, immediately before the
# DirectDraw surface presentation (Flip in fullscreen, Blt in windowed).
# The renderer in ECX is `*(RENDERER_OWNER_VA + 4)` — same value cached
# globally — so a detour here can either reuse ECX or reload from the
# global. We reload to keep the detour body independent of the saved ECX
# across pushad/popad.
#
# Original prologue is the 5-byte `mov al, byte_6210C0` (the windowed-vs-
# fullscreen flag read). RelocationPatch overwrites those 5 bytes with a
# `jmp rel32` into .zaxbot; the detour re-executes the displaced load
# before tail-jumping to RESUME.
S5693A0_VA       = 0x5693A0
S5693A0_PROLOGUE = b'\xA0\xC0\x10\x62\x00'   # mov al, byte_6210C0
S5693A0_RESUME   = 0x5693A5
FULLSCREEN_FLAG_VA = 0x6210C0                # byte_6210C0; re-encoded into the detour

# --- KERNEL32 IAT slots ----------------------------------------------------
IMP_CREATEFILEA      = 0x5EA0D4
IMP_WRITEFILE        = 0x5EA0DC
IMP_CLOSEHANDLE      = 0x5EA0D8
IMP_SETFILEPTR       = 0x5EA054
IMP_READFILE         = 0x5EA12C
IMP_CREATEDIRECTORYA = 0x5EA0C0
IMP_ENTERCS     = 0x5EA098
IMP_LEAVECS     = 0x5EA094

# --- DirectPlay manager CritSec --------------------------------------------
DP_CRITSECT_VA = 0x6BDBF0
