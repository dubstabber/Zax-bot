"""Bot fire/aim policy: range, projectile speed / lead-shot knobs."""

# --- Bot fire/aim policy -------------------------------------------------
FIRE_RANGE_SQ = 90000.0   # squared distance; bot fires when host is within sqrt(FIRE_RANGE_SQ)

# Projectile speed used as the last-resort fallback for shot leading. Units
# are engine-world units per pick_target call (≈ per frame), matching the
# per-frame delta the perception loop uses to estimate target velocity. Only
# reached if both the manual WEAPON_SPEEDS override and the dynamic def-read
# fall through (e.g. NULL weapon or NULL item-def). Hitscan weapons (no
# projectile) bypass apply_lead entirely and ignore this value.
PROJECTILE_SPEED = 10.0

# Conversion factor applied to the engine's per-weapon raw "Move/Max
# Velocity" field (pixels per second, schema range ~300..4000) to bring it
# into the per-pick_target-call units apply_lead's time math expects.
# Default 1/60 because the engine main loop passes a fixed 1/60 tick; if frames
# slow down, the dt term cancels algebraically in
# `lead = vx*t = (real_vel*dt) * (dist/(raw*dt))`, so one tuned constant
# covers every weapon. Calibrate empirically: spawn a bot with Missile
# Launcher (slow projectile), watch a strafing host, halve/double until
# shots land. See docs/04-spawn-ai-leads.md "Calibration recipe".
SPEED_SCALE = 1.0 / 60.0

# Muzzle spawn offset (pixels) — the engine spawns projectiles at the gun
# barrel tip, which is some distance in front of the bot's character center
# along the firing angle. Without compensation, our lead math over-predicts
# the bullet's flight distance by this amount (bullet arrives at the aim
# point sooner than expected ⇒ target hasn't moved as far ⇒ bot over-leads
# by `vel * muzzle / proj_speed` pixels per shot).
#
# Confirmed empirically: with a stationary bot firing Missile Launcher
# (CModel velocity = 800 px/sec), the captured projectile entity's spawn
# position back-extrapolated to ~20 px from the bot center. Calibrate by
# testing with a strafing host: too-high MUZZLE_OFFSET makes the bot UNDER-
# lead (bullets land behind target); too-low MUZZLE_OFFSET retains the
# original OVER-lead.
#
# Applied as a constant across all weapons. CProjectileInfo at def+0x44
# stores per-weapon X/Y offsets if a more precise fix is needed later;
# for now one global constant covers the typical case.
MUZZLE_OFFSET = 20.0

# Probability that the bot applies projectile lead on a given fire-tick.
# 0.0 = always shoot at target's current position (no prediction at all),
# 1.0 = always lead (perfect tracking), 0.5 = coin-flip per shot.
#
# Why randomize: perfect lead feels robotic — every shot lands the same way
# and a human player can learn to micro-zigzag to dodge consistently. A
# 50/50 mix gives bots a more human feel: half the shots predict your
# motion, the other half just shoot where you are right now, so dodging
# requires actual reflexes rather than a fixed counter-strategy.
#
# Hitscan weapons (Semi Auto Pistol, Alien Electrical Weapon) ignore this
# knob — they're instant-hit so leading is meaningless either way.
LEAD_PROBABILITY = 0.6

# Per-weapon projectile-speed OVERRIDE table, keyed by the weapon's inventory-
# item definition pointer returned by sub_4DD480(current_weapon_obj). Looked
# up first by compute_proj_speed; on a hit the override wins. On no match
# the dispatcher falls through to a runtime read of [def + 0x20] (projectile
# prototype) and [proto + 0x60] (Move/Max Velocity), scaled by SPEED_SCALE.
# That dynamic path handles every standard weapon out of the box — leave
# this list empty unless you need to pin a specific weapon's speed for
# tuning. Populate by observation: equip/fire a weapon, press R, read the
# `current_proto_va` dword from the `weapon_info` snapshot chunk, and add a
# `(definition_va, speed)` entry here.
#
# HITSCAN OVERRIDE: use speed = 0.0 as the sentinel — the dispatcher will set
# is_hitscan and skip apply_lead entirely, even if the engine's def has a
# (perhaps very fast) projectile prototype.
WEAPON_SPEEDS = []  # type: list[tuple[int, float]]
# Slots reserved in scratch for the runtime lookup; bumping this only costs
# bytes in .zaxbot scratch, so keep some headroom.
WEAPON_SPEEDS_MAX = 32

# --- Combat strafe (dodge weave) -----------------------------------------
# While an enemy is in fight range (the pick_target scan's per-bot
# bot_enemy_near stamp, FIGHT_STALL_RADIUS_SQ) the follower WEAVES: the
# desired movement vector gets a PERPENDICULAR-to-the-enemy component added
# before the angle emit, flipping sides every 2^FIGHT_STRAFE_FLIP_SHIFT
# frames (offset by the bot slot so bots don't dance in sync). This makes
# bots dodge ACROSS the line of fire — sidestepping projectiles — instead
# of the useless along-the-engagement-axis bobbing they showed before
# (user-reported: "they dodge vertically instead of horizontally"; on the
# mostly vertical CTF routes a fight lines up vertically, so route jitter
# looked like vertical dodging while real dodging needs the horizontal
# perpendicular). The weave preserves a dominant goal-ward component
# (progress continues, just slower), scales the lateral magnitude with the
# remaining goal distance (close-in it shrinks, so final approaches still
# land), and is suppressed outright while the wall-slide sweep owns the
# heading (stuck/wp_try at the trigger) — a weave into a wall would fight
# the sweep. The fire detour stamps the per-bot enemy vector
# (bot_enemy_dx/dy) alongside bot_enemy_near.
FIGHT_STRAFE_ENABLED = True
# Lateral gain relative to the goal-ward vector: the perpendicular
# component's magnitude = GAIN * |desired|. 0.9 swings the heading ~42 deg
# to alternating sides (progress factor cos ~0.74). 0 disables the weave
# without removing the machinery.
FIGHT_STRAFE_GAIN = 0.9
# log2 of the side-flip period in frames: 4 -> flip every 16 frames
# (~0.27 s per side at 60 fps, a full zigzag cycle ~0.53 s).
FIGHT_STRAFE_FLIP_SHIFT = 4

