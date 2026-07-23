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

# Per-entity component advance: __thiscall(this=char, a2=dt_float); ret 4.
# Gated internally on `char->flags(+0x1C) & 0x800000` (Active). Calls each
# component's vtbl[25] (e.g. the walking-controller think sub_543B60 -> our
# sub_542360 override). This is NOT sufficient for far-bot recovery by itself:
# the normal active-entity driver also runs the entity vtable stages below,
# including CEntityWalking's post-component position sync.
SUB_4FADC0_VA = 0x4FADC0
ENTITY_TICK_PRE1_VTBL_OFF = 0x7C   # sub_57A030 stage 1: entity.vtbl[31](dt)
ENTITY_TICK_PRE2_VTBL_OFF = 0x80   # sub_57A030 stage 2: entity.vtbl[32](dt)
ENTITY_TICK_MAIN_VTBL_OFF = 0x8C   # sub_57A030 stage 3: entity.vtbl[35](dt)
ENTITY_SKIP_UPDATE_BIT    = 0x10000
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
SUB_4267E0_VA              = 0x4267E0   # __thiscall(char) -> inventory/group*; ret 0
SUB_523DF0_VA              = 0x523DF0   # __thiscall(registry, char* name, int) -> hash; ret 8
SUB_425290_VA              = 0x425290   # __thiscall(inventory, gid) -> slot idx (-1 = none); ret 4
# "Multiplayer Flag" inventory-group id (lazily set by sub_5BAE10's first
# scoreboard render; runtime == 8). is_carrying(char) ==
# sub_425290(sub_4267E0(char), [MULTIPLAYER_FLAG_GID_VA]) != -1. 0 = not yet
# resolved (treat as "not carrying" that frame). See ctf-flag-carry-detection.
MULTIPLAYER_FLAG_GID_VA    = 0x714454
MULTIPLAYER_FLAG_GID_READY_VA = 0x714460
SUB_425900_VA              = 0x425900   # __thiscall(inventory, item_def*) -> item; ret 4
SUB_4DD480_VA              = 0x4DD480   # __thiscall(item_obj) -> inventory item definition*
SLOT_NAME_REGISTRY_VA      = 0x6C0800   # ECX setup for sub_523DF0 (engine global)
SUB_591FC0_VA              = 0x591FC0   # __thiscall(registry, char* name, int) -> group id; ret 8
PRIMARY_STR_VA             = 0x60B780   # ASCII "Primary\0..."
BLUE_FLAG_STR_VA           = 0x618364   # ASCII "Blue Flag\0"
RED_FLAG_STR_VA            = 0x618370   # ASCII "Red Flag\0"
MULTIPLAYER_FLAG_STR_VA    = 0x627068   # ASCII "Multiplayer Flag\0"
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
SUB_48DE10_VA               = 0x48DE10   # CEntityAnimated class-desc accessor (lazy-init
                                         # global dword_6BDD98; also vtbl 0x5F2010 slot 2).
                                         # All 333 MP CDoorAI door parts are authored
                                         # `Level Part=CEntityAnimated` (Data.dat census) —
                                         # the door-cache class gate filters on this.
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
# Companions (IDA 2026-07-22): sub_48D030(char) = CURRENT health
# (max - cur_damage, clamped >= 0), sub_48D150(char) = MAX health (model
# proto + 0x20) — so "full health" is simply cur_damage bits == 0 (the
# float is never negative).
CHAR_CUR_DAMAGE_OFF = 0x7C

# --- Filler-pickup "would benefit" predicates (need-gated goody pursuit) ---
# The engine's own per-class pickup-useful checks, vtable slot 32 on the
# pickup item classes (all __stdcall(char), ret 4, AL = 1 iff the pickup
# would help; NULL-guarded + is-a-character-guarded; callee-saved regs
# preserved — verified in disasm). Both walk the char's inventory BY CLASS
# via sub_4267E0 + sub_425860(inv, class_desc):
#   SUB_BATTERY_NEED_VA — CBatteryChargeInventoryItem (vtbl 0x604538[32]):
#     1 iff a carried CBatteryInventoryItem exists AND its live charge
#     (item+0x18 float) < capacity (*(float*)(sub_4DD480(item)+0x4C)).
#     No battery carried -> 0 (the pickup would do nothing).
#   SUB_SHIELD_NEED_VA — CShieldChargeInventoryItem (vtbl 0x5FFE18[32]):
#     same shape over CShieldInventoryItem — NO SHIELD CARRIED -> 0 (a
#     shield blob can't help a bot without a shield), full shield -> 0.
# (CHealthItem's slot-32 equivalent, sub_5AF3A0, is __thiscall and needs an
# item instance for its "storable" flag; the health need test reduces to
# cur_damage != 0, so the patch reads char+0x7C directly instead.)
SUB_BATTERY_NEED_VA = 0x5B06E0
SUB_SHIELD_NEED_VA  = 0x56F710

# --- Plasma "Plasma Ground" lava system (CPlasmaTileMap) -------------------
# Lava on molten maps is a CPlasmaTileMap: a 64px tile grid with two embedded
# 2D grids sharing a bounds-checked element getter at vtable offset +0xD8
# (index 54), __thiscall(grid, tileX, tileY) -> byte, callee-clean (ret 8).
# Out-of-range / negative tile coords return 0 (= not plasma), so no clamp is
# needed. The CPlasmaTileMap object's first dword is its vtable address
# (off_5FCD98), which scan_plasma uses to VALIDATE a candidate pointer before
# trusting it (the live-layer field offset is otherwise ambiguous; see below).
# Sources: sub_53F490 (ctor, *this=&off_5FCD98), sub_540000/sub_5405E0/
# sub_540640 (tile = world/tilepx, footprint/heat getter), sub_480E90
# (footprint getter, ret 8 confirmed), sub_4F4AE0/sub_4E69F0/sub_4E8900
# (layer holds the plasma map at +0x7C live / +0x40 on the render/save object).
CPLASMA_TILEMAP_VTBL_VA = 0x5FCD98   # off_5FCD98: CPlasmaTileMap vtable; *(plasma+0)==this
CPLASMA_FOOTPRINT_OFF   = 0x08       # embedded static "is plasma ground" grid (vtable off_5F162C)
CPLASMA_HEAT_OFF        = 0x2C6C     # embedded dynamic heat/elevation grid (vtable off_5F4814)
CPLASMA_TILEPX_W_OFF    = 0x2D04     # u32 tile width  px (==64)
CPLASMA_TILEPX_H_OFF    = 0x2D08     # u32 tile height px (==64)
CPLASMA_TILECNT_W_OFF   = 0x2D0C     # u32 tiles across
CPLASMA_TILECNT_H_OFF   = 0x2D10     # u32 tiles down
CPLASMA_GRID_GETTER_VOFF = 0xD8      # vtable[54]: __thiscall(grid, tx, ty) -> byte (ret 8)
# Candidate offsets of the plasma-map pointer on the active CLayer (mgr+0x2BC[0]).
# scan_plasma tries A then B and validates each by the vtable check above.
LAYER_PLASMA_MAP_OFF_A  = 0x7C       # live CLayer field (sub_4F4AE0 a1[31])
LAYER_PLASMA_MAP_OFF_B  = 0x40       # render/save-data field (sub_4E69F0/sub_4E8900)

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

# --- CGiveTeamAPointAction::execute (CTF capture score action) -------------
# Map scripts use this action at flag bases to award a capture point. The
# original action only calls the active gametype's vtable[+0x68] and does not
# itself verify that the scoring team's own flag is home.
S5A9960_VA       = 0x5A9960
S5A9960_RESUME   = 0x5A9969
S5A9960_PROLOGUE = b'\x56\x8B\xF1\x8B\x0D\x14\x3F\x71\x00'

# --- CUseInventoryItemAction::execute (CTF capture flag consume action) ----
# Flag-base scripts first consume the carried enemy flag through this action,
# then award the capture point. NOTE: the shared drop-on-death canned script
# ("Does player have a flag") consumes the dying carrier's flag through the
# SAME action, so a home-flag guard here cannot tell a capture consume from a
# drop consume and wrongly blocks drops whenever both flags are out. The old
# use-guard detour was removed for that reason; the site constants stay for
# reference.
S5B3100_VA       = 0x5B3100
S5B3100_RESUME   = 0x5B3106
S5B3100_PROLOGUE = b'\x53\x55\x8B\x6C\x24\x10'

# --- CActivateAction / CDeactivateAction per-entity apply -------------------
# The vanilla CTF rule "your own flag must be home to capture" is enforced by
# the map scripts through the base "checker" touch trigger ("Red Checker" /
# "Blue Checker", authored exactly on the flag spawn anchor on every CTF map):
# the shared canned scripts run CDeactivateAction on the checker when that
# team's flag is stolen and CActivateAction when it is returned/reset, so a
# deactivated checker simply never fires its capture Enter Action.
#
# Both action executes (vtable slot 23) funnel through the generic by-name
# multi-target resolver sub_41AED0, which calls the class's PER-ENTITY apply
# (vtable slot 27) once per resolved target entity:
#   sub_4C29F0 — CActivateAction apply:  set entity Active bit (+0x1C, 0x800000)
#   sub_4C2D60 — CDeactivateAction apply: clear entity Active bit
# Each is reachable only through its own vtable (single data xref), receives
# the RESOLVED entity at [esp+0x10], and returns with ret 0x10. Detouring the
# applies (not the executes) yields the exact script transition PLUS the live
# checker entity pointer, with no name strings or grid walks needed: the
# detour matches the entity's raw +0x4C/+0x50 position against flag_table and
# writes flag_present[] (1 on activate, 0 on deactivate).
S4C29F0_VA       = 0x4C29F0
S4C29F0_RESUME   = 0x4C29F6
S4C29F0_PROLOGUE = b'\x53\x57\x8B\x7C\x24\x18'  # push ebx; push edi; mov edi,[esp+0x18]
S4C2D60_VA       = 0x4C2D60
S4C2D60_RESUME   = 0x4C2D66   # the jz consuming the replayed `test ecx, ecx` flags
S4C2D60_PROLOGUE = b'\x8B\x4C\x24\x10\x85\xC9'  # mov ecx,[esp+0x10]; test ecx,ecx
VT_CACTIVATE_ACTION_VA   = 0x5F6374  # CActivateAction vtable
VT_CDEACTIVATE_ACTION_VA = 0x5F63E4  # CDeactivateAction vtable

# --- CGiveDefaultInventoryItemAction per-target give (flag dup guard) ------
# sub_5B4DA0 = vtable slot 28 (+0x70) of CGiveDefaultInventoryItemAction
# (vtable 0x604A4C; single data xref, so patching the function IS patching
# the class). __thiscall: ECX = action, [esp+4] = target entity (the resolved
# $Instigator from the base execute sub_5B3B80), ret 4. The action stores its
# "Inventory Item Definition" as the item-def KEY at action+0x10 (the reader
# sub_5B4650 fills it via sub_482DE0 = item+8) — the SAME key space
# sub_523DF0(registry, name, -1) resolves and sub_426860(ECX=char, EDX=key)
# counts, so the guard needs no new resolution machinery. The give itself
# resolves the def record (table dword_6C0C38, stride 0x80) and calls
# def->vtbl[+0x84](def, 0, target).
S5B4DA0_VA       = 0x5B4DA0
S5B4DA0_RESUME   = 0x5B4DA5
S5B4DA0_PROLOGUE = b'\xA1\x38\x0C\x6C\x00'  # mov eax, [dword_6C0C38] (def-record table)
VT_CGIVEDEFAULT_ACTION_VA = 0x604A4C  # CGiveDefaultInventoryItemAction vtable

# --- Salvage King (SK) engine anchors --------------------------------------
# Carried-mineral count getter (used by the SK gametype's own stats sync
# sub_5616B0): __usercall — ECX = character, EDX = item-def registry KEY
# (the sub_591FC0(dword_6C0C08, name, -1) result, NOT a def pointer);
# returns the carried count in EAX (0 when no inventory / item absent).
# Walks char->vtbl[+0x90]() inventory, matches sub_482DE0(item) == key,
# returns item->vtbl[+0xA4](). Plain ret, preserves ebx/esi/edi/ebp.
SUB_426860_VA = 0x426860
# The two SK mineral item-definition NAME strings in the image — resolved
# per match by load_sk exactly like the engine's lazy caches
# (dword_713160 / dword_71315C in sub_5616B0).
ORE_DEPOSITS_STR_VA = 0x60B7D4  # "Ore Deposits"
CRYSTALS_STR_VA     = 0x60B7C8  # "Crystals"

# --- CDropAllOreAndCrystalsAction per-target apply (SK death pile) ---------
# Every MP death runs the canned 'Drop Cystals and Ore' script; its action's
# apply (vtable 0x603578 slot 22) clones an UNNAMED pile entity from the
# "Ore_Crystals01" model template, places it collision-aware within 500 px of
# the corpse (sub_4EB7B0), moves the victim's whole Ore Deposits + Crystals
# load into a fresh CollideTrigger on the pile, and bails early when the
# victim carried nothing. No New Name is ever assigned, so the CTF-style
# name match cannot detect piles — the detour hooks this apply instead and
# records the DYING CHARACTER's position (ECX = action, victim entity at
# [esp+8] at entry, ret 0x10).
SUB_5A6E60_VA       = 0x5A6E60
S5A6E60_RESUME      = 0x5A6E66  # after the 6-byte prologue, at `mov esi,[esp+24h]`
S5A6E60_PROLOGUE    = b'\x83\xEC\x10\x53\x55\x56'  # sub esp,10h; push ebx; push ebp; push esi

# --- Proximity-mine deploy (secondary fire) --------------------------------
# sub_5AB9B0(char) is the engine's COMPLETE secondary-item deploy: resolves
# the char's Secondary-group selection (sub_523DF0(dword_6C0800, "Secondary",
# -1) -> sub_425290 -> inv.vtbl[+0x68]), gates on item->vtbl[+0x98](item,
# char) (reuse delay + rounds + !(char+0x1C & 0x10000000)), consumes a round
# (item vtbl+0x5C), creates the deployed entity from the def's Projectile
# model key ([def+0x20]) via sub_5176F0(key, CEntityProjectile-desc, char, 0,
# -1) — the OWNER is the placing char — places it exactly AT sub_4FB0A0(char)
# (ent vtbl+0xA4), registers it into the layer (sub_4EB6F0) and runs the
# def's New Shot Action ([def+0x54]) — whose MP branch warp-deletes the mine
# after ~15 s. __stdcall(char), ret 4. Only caller in-image is the pending-
# action event execute sub_5AB970 (the human right-click enqueues that event
# at 0x5448be); remote clients deploy CLIENT-side, so a host detour here
# observes host-human + bot deploys only. Detoured for mine registration;
# the bot placement path calls THROUGH the patched entry so one site
# registers both.
SUB_5AB9B0_VA       = 0x5AB9B0
S5AB9B0_RESUME      = 0x5AB9B7  # after the 7-byte prologue, at `mov edi,[esp+30h]`
S5AB9B0_PROLOGUE    = b'\x83\xEC\x1C\x53\x55\x56\x57'  # sub esp,1Ch; push ebx/ebp/esi/edi
SECONDARY_STR_VA      = 0x60B788  # "Secondary" (inventory-group name)
PROXIMITY_MINE_STR_VA = 0x6251E0  # "Proximity Mine" (item-def name)
MODIFIED_LASER_WELDER_STR_VA = 0x624CA4  # "Modified Laser Welder" (item-def
                                  # name; the MP SPAWN weapon, user-confirmed
                                  # 2026-07-23 — currently unused: the ENGINE
                                  # auto-switches to better picked-up weapons,
                                  # no patch-side equip needed)
# Inventory-group iteration (the engine's own Secondary auto-cycle at
# 0x544932..): next item id in a group / item object by id. Both __thiscall
# on the inventory (sub_4267E0 result).
# WARNING — sub_425350 WRAPS (live-frozen 2026-07-23, decompile-confirmed):
# with prev == the group's LAST item it returns the FIRST item again; -1
# comes back only when the group holds NO items at all. Any iteration over
# a group MUST therefore terminate on `id == sub_425470(inv; -1, key)`
# (the last-item id) exactly like the engine's own cycle loops at
# 0x543a48/0x544973 — a naive "-1 ends the walk" loop spins forever on a
# single-item group (the weapon-equip freeze) and a bounded count
# overcounts (a lone item counts as N — the weapon-need bug).
SUB_425350_VA = 0x425350  # (inv; prev_id or -1, group_key) -> next id in group,
                          # WRAPPING past the end; -1 only if group empty; ret 8
SUB_425470_VA = 0x425470  # (inv; -1, group_key) -> LAST item id in group / -1;
                          # the engine's own loop-termination guard; ret 8
SUB_424F60_VA = 0x424F60  # (inv; item_id) -> CInventoryItem*; ret 4
ITEM_DEF_KEY_OFF = 0x8    # [item+8] = item-def KEY (sub_482DE0; same id space
                          # as sub_523DF0(dword_6C0C08, name, -1))
ITEM_TRY_FIRE_OFF = 0x98  # item vtbl slot: (item, char) -> AL can-fire; pure
                          # checks (sub_42A4A0 -> def vtbl+0x8C sub_5B8020)

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
VK_O     = 0x4F
VK_R     = 0x52
VK_X     = 0x58
VK_COMMA = 0xBC   # VK_OEM_COMMA — used for wp_save (S is bound to "move down" in-game)
VK_OEM_PLUS  = 0xBB   # main-row '+' — node divergence level up
VK_OEM_MINUS = 0xBD   # main-row '-' — node divergence level down
VK_ADD       = 0x6B   # numpad '+' — same as VK_OEM_PLUS
VK_SUBTRACT  = 0x6D   # numpad '-' — same as VK_OEM_MINUS

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

# --- CString helpers + the "%s joined the game" announcement ---------------
# The host-side join handler (sub_5AC230, runs after a real client answers the
# CD-key challenge) announces a new player with this exact recipe:
#   sub_4DEC90(&slot)                     ; init a CString SLOT (one dword ->
#                                         ; shared empty buffer, refcounted)
#   fmt  = sub_4E13A0(&unk_71407C)        ; c_str of the LIVE format global
#   sub_4E09B0(&slot, fmt, name_cstr)     ; CString sprintf (CDECL, caller
#                                         ; cleans: add esp, 0xC for 3 args)
#   sub_59B260(sub_4E13A0(&slot), -1)     ; broadcast on-screen
#   sub_4DEFD0(&slot)                     ; release (NULL-safe, slot = 0)
# `unk_71407C` is a global CString registered by the static initializer
# sub_5AC1C0 with the English "%s joined the game" (string at 0x626000) into
# the localization-replaceable string list (sub_501030 registrar), so reading
# it LIVE at runtime — not hardcoding the English text — keeps a translated
# build's wording. The bot spawn path mirrors this recipe verbatim so a bot
# joining prints exactly what a real player joining prints.
CSTRING_INIT_VA             = 0x4DEC90  # __thiscall(ecx=&slot): slot = empty string
CSTRING_FREE_VA             = 0x4DEFD0  # __thiscall(ecx=&slot): release, slot = 0
CSTRING_CSTR_VA             = 0x4E13A0  # __thiscall(ecx=&slot) -> char* (buf+8 / "")
CSTRING_SPRINTF_VA          = 0x4E09B0  # cdecl(&dest_slot, char* fmt, ...) sprintf
JOIN_FMT_CSTRING_VA         = 0x71407C  # global CString: "%s joined the game" (live/localized)

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
#   QUIRK: the palette index is `sub_433A10(b)` — computed from the BLUE byte
#   ALONE (red/green ignored). In the game's 8-bit palettized display mode (how
#   it runs under Wine) the line drawer uses that palette index, so the rendered
#   overlay color is driven only by blue: blue=0 -> index 0 -> BLACK; blue=255 ->
#   a visible color. This is why overlay elements need a non-zero blue component
#   to show up at all (see cfg.OVERLAY_*_COLOR). Confirmed in-game 2026-06-01.
#
# Surface lock/unlock is automatic BUT PER-PRIMITIVE — `sub_568D90` (and every
# other CGraphics primitive) self-wraps via the global `dword_713318` lock
# flag: if the back buffer is NOT already locked it calls `sub_567BB0` (lock),
# draws ONE line, then immediately Unlocks (back-buffer vtbl+0x80) and clears
# the flag. Only when `dword_713318` is already set does it take the fast path
# (raw Bresenham into the locked pixels, no DirectDraw calls). The engine's
# own render loop (sub_40F5F0) therefore brackets a whole frame with ONE
# sub_567BB0 ... sub_567C90 pair so every internal draw is fast-path.
#
# A frame-aligned hook that issues many draws (the waypoint overlay) MUST do
# the same: on native Windows each DirectDraw Lock/Unlock is a GPU/GDI sync
# costing milliseconds (Wine's system-memory surfaces make it nearly free,
# which is why the 2-6 FPS overlay collapse was Windows-only), and an oval is
# 10-25 line segments = 10-25 Lock/Unlock pairs unbatched. Lock once before
# the draw pass, unlock before resuming into sub_5693A0 — its fullscreen path
# checks `dword_713318` and SKIPS the flip while the surface is locked (and
# the windowed Blt would fail), so a leaked lock = a dropped frame.
#
# `sub_567BB0(this=renderer)` — Restore-on-lost + Lock(back buffer,
#   DDLOCK_WAIT); on success sets `dword_713318 = 1` and caches the surface
#   pixel base at renderer+0xC. Does NOT check the flag first — never call it
#   while already locked (100 failing Lock retries). ret 0.
# `sub_567C90(this=renderer)` — Unlock + clears renderer+0xC and the flag;
#   idempotent (no-op while unlocked). ret 0.
RENDERER_OWNER_VA       = 0x6C02CC
RENDERER_OFF_IN_OWNER   = 0x04
SUB_4B3CB0_VA           = 0x4B3CB0
SUB_4FCCC0_VA           = 0x4FCCC0
SUB_53F010_VA           = 0x53F010
SUB_567BB0_VA           = 0x567BB0   # renderer back-buffer batch LOCK
SUB_567C90_VA           = 0x567C90   # renderer back-buffer batch UNLOCK
DDRAW_LOCKED_FLAG_VA    = 0x713318   # 1 while the back buffer is locked

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

# --- sub_53DA40 (CPickupAI per-frame update) -------------------------------
# The per-pickup, per-frame update (a CPickupAI vtable slot holding the
# respawn-timer logic, keyed off game-time `dword_6C02CC` deltas). It runs
# once per pickup ENTITY every frame; the pickup entity is the value the
# engine loads into EBX via `mov ebx, [esp+0x30]` right after the 8-byte
# prologue (it then does `sub_4FB0A0(ebx, ...)` and reads `ebx[7]` state
# flags). detour_53DA40 re-executes this prologue first so EBX = the entity
# exactly as the engine computes it (no stack-offset guessing), then
# self-registers the pickup's world position into pickup_table.
S53DA40_VA       = 0x53DA40
S53DA40_RESUME   = 0x53DA48   # after the 8-byte prologue, at `test ebx, ebx`
S53DA40_PROLOGUE = b'\x83\xEC\x24\x53\x8B\x5C\x24\x2C'  # sub esp,24h; push ebx; mov ebx,[esp+0x2C]

# --- sub_4C11A0 (CRelocateAction/CTeleportAction execute) — portal detect ---
# The single chokepoint every teleport/relocate funnels through. Both
# CRelocateAction and CTeleportAction override their "execute" vtable slot
# (slot 27 == vtbl+0x6C) with sub_5A5A60, which runs the warp (vtbl+0x74) then
# tail-calls sub_4C1060 -> sub_4C11A0. At sub_4C11A0 entry (__thiscall):
#   ecx     = the action object; [ecx] = its primary vtable (single inheritance,
#             never this-adjusted): CSwitchMapAction 0x6032C4 /
#             CRelocateAction 0x603338 / CTeleportAction 0x6033B0.
#   [esp+4] = a2 = the entity being teleported, still at its SOURCE position
#             (the relocate itself happens later inside sub_4C11A0 via
#             sub_4F4AC0). sub_4FB0A0(entity) here therefore yields the portal
#             pad world coordinates.
# Detouring here detects teleporters GENERALLY — touch-trigger, script/event-
# driven, and conditional portals that only fire once a map condition activates
# them — which the static Data.dat parse (world_scan.py) cannot. The site fires
# only on an actual teleport (not per frame), so it is not a hot path.
# Sources: sub_4C11A0 (relocate executor, holds "Relocate can only be used to
# move animated entities"), sub_4C1060 (thin wrapper), sub_5A5A60 (the
# relocate/teleport execute override), and the action factory stubs sub_5A56C0
# / sub_5A5B90 (which `mov [esi], <vtable>`).
S4C11A0_VA       = 0x4C11A0
S4C11A0_RESUME   = 0x4C11A7   # after displaced 7 bytes (at 'push esi')
S4C11A0_PROLOGUE = b'\x8B\x44\x24\x08\x83\xEC\x0C'  # mov eax,[esp+8]; sub esp,0xC

# Action instance vtables (primary), used by the teleport-portal detour to
# classify the action object at sub_4C11A0 entry. CTeleportAction is the genuine
# warp teleporter (Warp Behavior + Teleporter.wav); the detour filters to it so
# plain CRelocateAction "$return"/non-warp moves are not registered as portals.
VT_SWITCHMAP_ACTION_VA = 0x6032C4
VT_RELOCATE_ACTION_VA  = 0x603338
VT_TELEPORT_ACTION_VA  = 0x6033B0

# --- World entity enumeration (the spatial-grid walk) ----------------------
# The general "find live entities" primitive (zaxbot/detours/entity_scan.py).
# The old hazard/pickup scans were dormant because they iterated `mgr+0x2BC` as
# a flat entity list, but that's the LAYER list (count `mgr+0x2C0` == 1 in MP);
# real entities live one level down, inside each layer's spatial grid. Recipe
# decompiled from the engine's own by-name finder `sub_57A7E0` and validated
# live (no-ASLR, runtime VAs == IDB VAs):
#   mgr            = [MANAGER_GLOBAL_VA]
#   layer          = [[mgr + MGR_LAYER_ARRAY_OFF]]      (active CLayer, vtbl 0x5F8BAC)
#   grid           = layer + 0x50 (embedded); fields below are layer-relative:
#     rows         = [layer + LAYER_GRID_ROWS_OFF]
#     cols         = [layer + LAYER_GRID_COLS_OFF]
#     cells        = [layer + LAYER_GRID_CELLS_OFF]     (array of rows*cols 16B cells)
#   each cell      = [vtbl 0x600A90, list@+4, count@+8, cap@+0xC]
#   each entity    = list[k]; carries flags@ENTITY_FLAGS_OFF, visit-id@ENTITY_VISIT_OFF
# Walk all rows*cols cells linearly; an entity that spans multiple cells is
# de-duplicated via the engine's own visit-id protocol: bump global counter
# `ENTITY_VISIT_COUNTER_VA`, stamp each entity's `+ENTITY_VISIT_OFF` with it, and
# skip any entity already stamped >= the current id (exactly what the engine does
# during name lookups — safe, since the engine always bumps to a fresh higher id
# before its own next lookup). Classify with `sub_416790(ent, classdesc)`, read
# position with `sub_4FB0A0(ent, &out)`. See [[world-entity-enumeration]].
MGR_LAYER_ARRAY_OFF    = 0x2BC      # [mgr + this] -> layer array (element 0 = active CLayer)
LAYER_GRID_ROWS_OFF    = 0x60       # [layer + this] -> grid rows
LAYER_GRID_COLS_OFF    = 0x64       # [layer + this] -> grid cols
LAYER_GRID_CELLS_OFF   = 0x68       # [layer + this] -> grid cells array base
GRID_CELL_STRIDE       = 0x10       # bytes per cell record
GRID_CELL_LIST_OFF     = 0x04       # [cell + this] -> entity-pointer array
GRID_CELL_COUNT_OFF    = 0x08       # [cell + this] -> entity count in this cell
ENTITY_FLAGS_OFF       = 0x1C       # entity flags dword
ENTITY_ACTIVE_BIT      = 0x800000   # "Active" bit within the flags dword (set by CActivateAction)
ENTITY_SOLID_BIT       = 0x40000    # SOLID/collidable bit; a CLOSED door carries it, the door
                                    # open path (CDoorAI update slot 25 / COpenDoorAction apply
                                    # sub_4BD870) clears it — the clean passable/blocked readback
ENTITY_VISIT_OFF       = 0x2C       # entity per-scan visit-id (dedup)
ENTITY_NAME_CSTR_OFF   = 0x18       # entity name CString header ptr; ASCII at [hdr]+8
                                    # (sub_4FBF20 -> sub_4E13A0: return *(ent+0x18) + 8; the
                                    # engine's own by-name finder sub_57A7E0 reads names this
                                    # way for every grid entity). Header may be NULL/garbage on
                                    # odd entities — range-check before deref.
ENTITY_VISIT_COUNTER_VA = 0x622200  # dword_622200: engine global visit-id counter
ENTITY_POS_X_OFF       = 0x4C       # entity world position X (float)
ENTITY_POS_Y_OFF       = 0x50       # entity world position Y (float)

# --- Participant activation point (engine-native anti-culling) -------------
# The MP world update sub_4F37E0 (virtual; vtables 0x5F909C/0x602EA4 slots)
# walks ALL participants and, for each with a valid layer index at +0xDC,
# appends the float pair at +0xC0/+0xC4 to a point list; sub_4EA350 turns each
# point into a screen-sized rect and sub_4E74A0 updates every Active entity
# inside the rect union (sub_57A100 grid collect, Active-bit-masked). Real
# clients stream +0xC0/+0xC4 over DirectPlay; the host's own participant is
# updated engine-side (live-verified tracking the host char). Bot participants
# are never written by anyone -> stuck at (0,0) (live-verified), which is the
# root cause of every far-from-host culling bug. The page-flip hook mirrors
# each bot char's +0x4C/+0x50 into these fields per frame.
PART_POS_X_OFF         = 0xC0       # participant "last known position" X (float)
PART_POS_Y_OFF         = 0xC4       # participant "last known position" Y (float)
PART_LAYER_IDX_OFF     = 0xDC       # participant layer index (-1 = not in world); bots get 0 at spawn

# --- In-game widget/dialog system (bot menu GUI) ---------------------------
# The engine's retained-mode widget tree, reused to build the B-key bot menu
# the same way the Esc quit dialog is built (sub_5BF240 -> sub_46B050 pattern).
# A dialog is a plain CWindow (base vtable WIN_BASE_VTABLE_VA) whose command
# handler (vtable slot 21) and destructor (slot 0) are overridden — exactly how
# the "lose your changes?" confirm dialog (sub_4721B0, vtable off_5F02B0)
# derives from the base. All object sizes passed to the allocator are byte
# counts (WIDGET_ALLOC_VA is a pooled `operator new`), and every child text is a
# plain char* (copied into the widget's own CString).
#
# Construction recipe (mirrors sub_4721B0 verbatim):
#   dlg = WIDGET_ALLOC_VA(ecx=DIALOG_OBJ_SIZE)
#   WIN_BASE_CTOR_VA(dlg, parent, 0)            ; base ctor, sets base vtable
#   *(dlg) = <our cloned vtable>                ; override vtable
#   lbl = WIDGET_ALLOC_VA(0x128); LABEL_CTOR_VA(lbl, dlg, "title")
#   WIDGET_ADD_CHILD_VA(dlg, lbl, WIDGET_ANCHOR_TITLE, 0, -1000, -1000, 0)
#   btn = WIDGET_ALLOC_VA(0x138); BUTTON_CTOR_VA(btn, dlg, "text", 0)
#   WIDGET_ADD_CHILD_VA(dlg, btn, WIDGET_ANCHOR_BELOW, 0, -1000, -1000, 0)
#   WIDGET_SET_DEFAULT_VA(dlg, btn)             ; keyboard focus/default
#   WIDGET_SHOW_MODAL_VA(parent, dlg, WIDGET_SHOW_ANCHOR)
# The parent is the DESKTOP ROOT widget, NOT the world manager. `*dword_713F14`
# is a `CGame` (vtable `off_602DC8`), not a `CWindow`, so parenting a dialog to
# it faults inside `sub_40C6E0` (which derefs `parent+0xE8` / `parent->vtbl
# [0x80]` expecting widget layout — this was the observed live crash). The
# engine attaches every screen and dialog to `sub_4CDF30(uimgr)` =
# `*(uimgr + 0x34)`, the screen-host / desktop root that owns the pushed-screen
# list (verified in the main-menu creation paths `sub_59BAB0` and `sub_5A0E80`,
# and the screen-stack walk `sub_5A11F0`). `sub_5BF240`'s parent is likewise its
# own screen — that vtable is `CMainMenu` (`~CMainMenu` in `sub_5BE4B0`), never
# the world manager.
UI_MANAGER_GLOBAL_VA   = 0x6C02CC   # app/UI manager pointer (== RENDERER_OWNER_VA)
UI_DESKTOP_ROOT_OFF    = 0x34       # *(uimgr + 0x34) = desktop/screen-host root (sub_4CDF30 -> this[13])
# "UI is active" is a modal/menu-depth counter at *(uimgr + 0x2C). While > 0
# the engine suppresses gameplay input (checked in sub_470050 via the app-mgr
# vtable slot 30 = sub_4CDD70 `return this[11] > 0`) and switches from the
# gameplay crosshair to the menu pointer cursor — the "Window with pause"
# behavior the Esc exit menu uses. Enter/leave are app-mgr vtable slots 28/29
# (2-instruction inc/dec, __thiscall ecx = uimgr, plain ret). A hand-built
# modal on the desktop does NOT touch this counter, so the bot menu must
# bracket itself: enter on show, leave on teardown.
UI_ENTER_MODE_VA       = 0x4CDD50   # __thiscall(ecx=uimgr): ++uimgr[0x2C]  (enter UI/menu mode)
UI_LEAVE_MODE_VA       = 0x4CDD60   # __thiscall(ecx=uimgr): --uimgr[0x2C]  (leave UI/menu mode)
WIDGET_ALLOC_VA        = 0x417710   # __thiscall(ecx=size) -> pooled object; plain ret
WIN_BASE_CTOR_VA       = 0x403D00   # sub_403D00(this, parent, 0) base CWindow ctor; ret 8
WIN_BASE_VTABLE_VA     = 0x5EAAC4   # off_5EAAC4 base CWindow vtable (77 slots / 0x134 bytes)
WIN_BASE_TEARDOWN_VA   = 0x403D70   # sub_403D70(this) base teardown chain; plain ret
WIN_POOL_FREE_VA       = 0x54D130   # __fastcall(ecx=this, edx=size) pool free; plain ret
LABEL_CTOR_VA          = 0x4127D0   # sub_4127D0(this, parent, char* text) text label; ret 8
BUTTON_CTOR_VA         = 0x40F9B0   # sub_40F9B0(this, parent, char* text, 0) push button; ret 0xC
WIDGET_ADD_CHILD_VA    = 0x40C7C0   # sub_40C7C0(this, child, anchor, 0, x, y, flags); ret 0x18
WIDGET_SET_DEFAULT_VA  = 0x40CA40   # sub_40CA40(this, child) mark keyboard default/focus; ret 4
WIDGET_SHOW_MODAL_VA   = 0x40C6E0   # sub_40C6E0(this=parent, dialog, anchor) show modal; ret 8
WIDGET_DTOR_VTBL_OFF   = 0x00       # slot 0: (deleting) destructor(this, char flag); ret 4
WIDGET_CLOSE_VTBL_OFF  = 0x14       # slot 5: close/dismiss(this) (sub_40BB90); plain ret
WIDGET_CMD_VTBL_OFF    = 0x54       # slot 21: notify handler(this, widget, code); ret 8
WIDGET_VTABLE_DWORDS   = 0x4D       # 77 vtable slots to clone from WIN_BASE_VTABLE_VA
DIALOG_OBJ_SIZE        = 0x140      # dialog object allocation size (same as the confirm dialog)
WIDGET_ANCHOR_TITLE    = 1          # add-child anchor: title area (top)
WIDGET_ANCHOR_BELOW    = 12         # add-child anchor: centered X, below the previous sibling
WIDGET_SHOW_ANCHOR     = 6          # show-modal anchor: centered on the parent
# Native close box + Esc wiring + post-add layout fix (all decompiled from the
# base CWindow, 2026-07-21):
# - sub_4038A0 builds the engine's own 13x13 close button (text = glyph 0x18,
#   the font's X symbol), stores it at window+0x100 and add-childs it with
#   (anchor 3, 0, x=2, y=2, flags=1). The base set-rect handler (sub_403640)
#   re-anchors whatever sits at +0x100 to the TOP-RIGHT corner on every
#   move/resize, so the box stays glued after any window growth. The BASE
#   command handler (sub_4035F0) closes via vtbl+0x14 when that widget
#   notifies — an overriding handler (menu_cmd) must do the same compare.
# - The base key handler (vtable slot 16 = sub_403E40) maps key 27 (Esc) to
#   "activate the widget at +0x120" and key 13 (Enter) to +0xEC/+0x11C, so
#   storing the close box at +0x120 gives Esc-close for free (exactly how the
#   confirm dialog's No button cancels).
# - Anchor-12 (WIDGET_ANCHOR_BELOW) centers a child against the parent's
#   CLIENT width AT ADD TIME ((clientW-childW)/2, parent-relative, from
#   sub_40DB20 case 12), and the add-child growth hook (sub_40E590) only ever
#   GROWS the window to fit child x2 — a child whose computed x1 went negative
#   stays clipped off the left edge. Engine dialogs fix positions after adding
#   (sub_4721B0 uses sub_40D680); the bot menu re-centers every button against
#   the FINAL client width the same way.
WIN_ENSURE_CLOSEBOX_VA = 0x4038A0   # __thiscall(ecx=window): create native top-right X close box at +0x100
WIDGET_SET_XPOS_VA     = 0x40D680   # __thiscall(ecx=widget, x): move to x, keep y/size; ret 4
WIN_RESIZE_VTBL_OFF    = 0xEC       # vtbl slot 59: resize(this, w, h) keeping origin (sub_40D510); ret 8
WIN_CLOSE_BOX_OFF      = 0x100      # window+0x100: native close-box button ptr (auto re-anchored top-right)
WIN_CANCEL_WIDGET_OFF  = 0x120      # window+0x120: widget the base key handler activates on Esc (case 27)
WIN_RECT_X1_OFF        = 0x04       # widget rect dwords: x1/y1/x2/y2 at +0x04/+0x08/+0x0C/+0x10
WIN_RECT_Y1_OFF        = 0x08       #   (parent-relative for children; sub_402D80 pre-sizes to the title)
WIN_RECT_X2_OFF        = 0x0C
WIN_RECT_Y2_OFF        = 0x10
WIN_CLIENT_X1_OFF      = 0x14       # client rect dwords: x1/y1/x2/y2 at +0x14/+0x18/+0x1C/+0x20
WIN_CLIENT_X2_OFF      = 0x1C
WIN_PAD_X_BYTE_OFF     = 0x78       # signed byte: default x padding used by anchors 1-4 (sub_40DB20)

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
