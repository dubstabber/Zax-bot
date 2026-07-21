"""CTF flag detection: static base-anchor tables + flag events."""

# --- CTF flag detection --------------------------------------------------
# CTF flag home positions are extracted from Data.dat at patch-build time by
# parsing each multiplayer .zax map for its "Red Flag Spawn" / "Blue Flag
# Spawn" Level Parts (the flag base anchors). Runtime code only compares the
# active map name against this compact static table and copies the matching
# points into flag_table on match change — identical to the portal pipeline,
# no heap-wide scanning in-game.
#
# flag_present[] ("is that team's flag home?") is EVENT-DRIVEN, mirroring the
# vanilla rule exactly. Every CTF map authors a hidden "Red Checker" / "Blue
# Checker" touch trigger exactly on the flag spawn anchor; the shared canned
# scripts (Data.dat "Picked up a Flag" / "Returned a Flag") deactivate that
# checker when the team's flag is stolen and reactivate it when the flag is
# returned or reset after a capture — a deactivated checker never fires its
# capture action, which IS the vanilla "own flag must be home" enforcement.
# The patch detours the CActivateAction/CDeactivateAction per-entity applies
# (sub_4C29F0 / sub_4C2D60) and, when the resolved target entity sits on a
# flag_table anchor (it is the checker), writes flag_present[] = 1/0. Flags
# start home (load_flags seeds 1) and there is no engine-side auto-return, so
# the two script events are the complete transition set. Heuristics that
# previously derived presence from anchor-entity counts, carried-inventory
# scans and dropped-item grid matches were removed: the world flag entity is a
# plain unnamed-or-renamed CEntityAnimated with no inventory identity, so
# those scans could not see a dropped flag and left flag_present stuck at 1.
FLAG_TABLE_MAX        = 8   # live overlay points, float[2] each (2 flags/map)
FLAG_STATIC_MAP_MAX   = 8   # shipped Data.dat currently has 7 flag maps
FLAG_STATIC_POINT_MAX = 16  # shipped Data.dat currently has 14 flag points
FLAG_MAP_NAME_SLOT    = 96  # fixed ASCII bytes per map path, including NUL
# Exact-anchor entity cache used by the far-base force-tick. Up to three
# distinct entities can legitimately sit on the anchor (checker trigger, spawn
# position marker, and the flag entity itself once a return/capture recreated
# it exactly at the spawn); two slots could evict the checker depending on
# grid iteration order, which silently broke far captures.
FLAG_ENTITY_SLOTS_PER_FLAG = 3
CTF_FLAG_ENTITY_MATCH_RADIUS_SQ = 16.0 * 16.0
CTF_FLAG_HOME_FORCE_TICK_RADIUS_SQ = 64.0 * 64.0
# Master gate for the CActivateAction/CDeactivateAction apply detours that
# keep flag_present[] in lockstep with the map script's checker state.
CTF_FLAG_EVENTS_ENABLED = True

# --- Duplicate-carrier guard (live 2026-07-20: two same-team bots each
# holding the red flag). The map script's "Picked up a Flag" canned object
# grants the flag through CGiveDefaultInventoryItemAction and deletes the
# world flag entity via a (deferred) CDeleteAction — nothing prevents TWO
# characters overlapping the flag's CPassThroughTriggerAI in the same frame
# from each executing the script and each receiving a flag item. Humans
# rarely arrive frame-synchronized; pack-routed bots do (goal routing and
# the drop pursuit deliberately send several bots at the same flag). The
# guard detours the action's per-target give (sub_5B4DA0, reachable only
# through the CGiveDefaultInventoryItemAction vtable): when the item being
# given is a Red/Blue Flag def AND any live character already carries that
# def, the give is suppressed — the rest of the script chain (delete,
# sound, checker deactivate) is idempotent, so the second toucher simply
# doesn't get a duplicate. Non-flag gives pass through untouched.
CTF_FLAG_GIVE_GUARD_ENABLED = True

