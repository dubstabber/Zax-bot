"""Wall-slide / lava-sweep asm-immediate tuning constants."""

# --- Wall-slide tuning (asm immediates; the angle step is a runtime knob) ----
# Trigger primarily on LACK OF PROGRESS (wp_try), which catches both freeze and
# slide-along-wall-without-approach. A pure position-delta stuck_count is not
# enough for wall grinding, but it is a useful secondary backstop for the fully
# stationary case seen in R dumps where reacquiring the same nearest node kept
# resetting wp_try before the sweep could finish a full circle.
# The circling that a pure wp_try sweep caused is now bounded by the RETREAT
# below: the sweep only runs for WP_RETREAT_TIMEOUT-WP_SLIDE_TRIGGER frames, then
# the bot backs up to the previous (reachable) node instead of orbiting forever.
WP_SLIDE_TRIGGER_FRAMES = 8
# Heading steps in a full sweep (12 * 30 = 360): a blocked bot tries every
# direction to find one that frees it. slide_turn cycles 0..CAP-1 and wraps.
WP_SLIDE_TURN_CAP = 12
# Advance the sweep one heading step every (MASK+1) frames (3 => 4 frames per
# direction) so each candidate heading is held long enough to actually move.
WP_SLIDE_SWEEP_MASK = 3

# Proactive lava veto: max candidate headings tried in one frame when the
# emitted heading would step into lava. The veto rotates by cfg.LAVA_SWEEP_STEP
# per try until a lava-clear heading is found; 12 * 30deg = a full circle.
LAVA_SWEEP_COUNT = 12
