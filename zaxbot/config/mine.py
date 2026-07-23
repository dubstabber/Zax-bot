"""Proximity-mine support: bot placement, registration, avoidance.

Engine model (IDA session 2026-07-23, see the ``mine-deploy-mechanism``
memory): the Proximity Mine is a Secondary-slot weapon whose "projectile"
is the deployed mine entity (`Items/Weapons/Deployed States/Proximity
Mine`, a CEntityProjectile owned by the placing char). ``sub_5AB9B0(char)``
is the engine's complete secondary-deploy — it places the mine exactly AT
the character's position and runs the def's New Shot Action, whose
multiplayer branch warps the mine out and DELETES it after ~15 s. That
bounded lifetime is why the live mine table is a TTL ring and needs no
liveness scanning: the TTL mirrors the engine's own expiry.

Placement is a page-flip stage (``mine_tick``): each bot with mine rounds
rolls ``MINE_PLACE_CHANCE`` every ``MINE_PLACE_RETRY_FRAMES`` and, off a
success, force-selects the mine in its Secondary slot and calls
``sub_5AB9B0`` — bots roam constantly, so a low per-window probability
scatters mines organically over the map. CTF-specific placement rules
(e.g. mining the own-flag approach) are a planned follow-up; they slot in
as extra gates in ``mine_tick`` before the RNG roll.

Registration is a detour at ``sub_5AB9B0``'s entry (every deploy the HOST
executes funnels through it: all bot placements + the host human's
right-click; PC2 deploys happen client-side and are not seen — open).

Avoidance is a movement-emit veto stage (mirror of the portal/lava
vetoes): a heading whose lookahead point lands inside a live mine's
bubble is rotated onward. A mine the bot is already standing in is
exempt so it can always walk OUT.
"""

# Master switch for the whole layer (layout block, placement tick,
# registration detour site, avoidance veto, overlay markers).
MINE_ENABLED = True

# Live mine ring capacity. Power of two (ring cursor is masked). Mines
# live ~15 s and a bot places at most one per cooldown window, so 16
# bots + host-human spam fit comfortably.
MINE_TABLE_MAX = 32

# Ring TTL in page flips. The deployed mine's MP script deletes it after
# 14 s + 1 s warp-out; 900 frames @60fps matches that window.
MINE_TTL_FRAMES = 900

# Placement roll: every MINE_PLACE_RETRY_FRAMES an eligible bot (alive,
# carries >= 1 mine round) rolls RNG(0..99) < MINE_PLACE_CHANCE. On a
# successful DEPLOY the per-bot cooldown re-arms to
# MINE_PLACE_COOLDOWN_FRAMES instead. Chance is packed into scratch so
# it stays live-tunable from a hex editor / CE.
MINE_PLACE_CHANCE = 35
MINE_PLACE_RETRY_FRAMES = 90
MINE_PLACE_COOLDOWN_FRAMES = 600

# No placement while standing within this d^2 of an already-registered
# live mine — keeps a wedged/stalled bot from stacking its whole ammo
# reserve on one spot (128 px). With MINE_AVOID_OWN_ONLY the sweep only
# considers the bot's OWN mines (an enemy-mined chokepoint must not block
# the bot from mining it too).
MINE_SPACING_RADIUS_SQ = 128.0 * 128.0

# Avoidance veto: rotate any heading whose LAVA_LOOKAHEAD_PX lookahead
# point lands within sqrt of this d^2 of a live mine (96 px — the mine
# detonates on projectile-vs-char contact, so this is contact reach plus
# blast margin).
MINE_AVOID_ENABLED = True
MINE_AVOID_RADIUS_SQ = 96.0 * 96.0

# CTF territory placement rules (user-requested 2026-07-23): a bot only
# mines the ENEMY team's territory, RARELY the middle of the map, and
# never its own half. Territory is classified by the CTF routing BFS
# fields (path distance to each base in WP_EDGE_LEN_QUANTUM units, so
# walls count — a spot behind the enemy wall is not "enemy territory"
# just because it is Euclidean-close): own_d vs enemy_d at the bot's
# current graph node. |own_d - enemy_d| <= MID_BAND quanta = the middle
# strip (16 quanta = 256 px of path); there the placement additionally
# rolls RNG < MINE_CTF_MID_CHANCE (on top of the main MINE_PLACE_CHANCE
# roll, so mid-map mines are ~15% of an already-rolled attempt). Deeper
# into the own half the attempt is denied outright — CTF defenders
# therefore hold their rounds until they cross out. DM/SK are untouched
# (the gate keys off the detected CTF mode). Both knobs are packed into
# scratch next to mine_place_chance for live tuning.
MINE_CTF_TERRITORY_ENABLED = True
MINE_CTF_MID_BAND_QUANTA = 16
MINE_CTF_MID_CHANCE = 15

# Only avoid the bot's OWN mines (live-confirmed 2026-07-23: a deployed
# mine has NO owner or team immunity — it kills its placer, and in CTF it
# kills same-team players even with friendly fire disabled). Avoiding
# every known mine made bots immune to the host player's mines, which
# kills the point of mines against bots — so the veto (and the placement
# spacing sweep) filter on the ring's recorded owner slot instead: a bot
# never steps on its own mine, and everyone else's mines remain fully
# effective against it. Ownership is keyed by BOT SLOT in mine_owner[],
# so it survives death/respawn (the mine outlives the bot). Host-human
# mines carry owner -1 and never match a bot slot.
MINE_AVOID_OWN_ONLY = True
