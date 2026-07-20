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

