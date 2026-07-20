"""Waypoint-authoring overlay: toggle, colors, geometry, cull margins."""

# --- Waypoint overlay (visualization for waypoint authoring) -------------
# When True, the page-flip detour at sub_5693A0 draws OVERLAY_WAYPOINTS as
# circles and OVERLAY_EDGES as line segments on top of the rendered frame.
# Coordinates are WORLD-space — the engine's renderer applies the camera
# transform internally (vtbl[+0xAC]/[+0xB0] in CGraphics, off_5FF360), so
# vertices stay glued to the right map position as the camera scrolls.
#
# Install the page-flip hook needed for the visual waypoint overlay and the
# runtime O-key draw toggle. The hook fast-skips when overlay_enabled is 0.
OVERLAY_HOOK_ENABLED = True

# Initial draw state. Keep this False for normal FPS: the N/J/X editor and
# saved graph following still work, and O toggles the visual graph in-game
# only when authoring. Drawing every vertex/edge every frame is expensive on
# Windows 11 when large graphs are visible.
OVERLAY_ENABLED = False
OVERLAY_WAYPOINTS = [(100.0, 200.0), (300.0, 200.0), (300.0, 400.0), (100.0, 400.0)]  # type: list[tuple[float, float]]
OVERLAY_EDGES     = [(0, 1), (1, 2), (2, 3), (3, 0)]  # type: list[tuple[int, int]]

# Capacity ceilings packed into the scratch layout. Bumping them grows
# the .zaxbot section but doesn't slow rendering — the runtime loops on
# the live count fields. Each vertex is 8 B (two floats); each edge is
# 4 B (two u16 indices).
OVERLAY_VERTEX_MAX = 256
OVERLAY_EDGE_MAX   = 512

# Vertex / edge styling. RGBA bytes (0..255) get baked into a 16-byte
# CColor struct via sub_53F010 each frame so the palette index stays
# valid in 8-bit display modes.
# IMPORTANT — overlay colors are effectively BLUE-CHANNEL ONLY in the game's
# 8-bit palettized display mode (historically observed under Wine). sub_53F010 stamps each
# CColor's palette index via sub_433A10(BLUE) — derived from the blue byte alone
# — and the line drawer (sub_568D90) uses that palette index, NOT the RGB. So the
# rendered color depends only on blue: blue=0 => palette index 0 => BLACK
# (red/green are ignored); blue=255 => a visible bright color. Confirmed in-game
# (2026-06-01): colors with B=0 rendered BLACK in 8-bit mode. Keep visible
# graph elements on non-zero blue values so the authoring overlay is actually
# visible. The RGBA tuples below are kept as human labels; only blue actually
# drives the hue in palettized mode.
OVERLAY_VERTEX_COLOR   = (255, 255, 255, 255) # white; B=255 -> visible in 8-bit mode
OVERLAY_EDGE_COLOR     = (64, 160, 255, 255)  # blue/cyan; B=255 -> visible in 8-bit mode
OVERLAY_SELECTED_COLOR = (255, 0, 255, 255)   # magenta; B=255 -> visible selected node
OVERLAY_PICKUP_COLOR   = (0, 255, 255, 255)   # cyan; B=255 -> visible detected pickups
OVERLAY_PORTAL_COLOR   = (255, 64, 255, 255)  # pink; B=255 -> visible detected teleports
OVERLAY_FLAG_COLOR     = (0, 0, 255, 255)     # blue; B=255 -> visible detected CTF flags
# Doors render with the same palette index as every other B=255 marker in the
# 8-bit mode (see the color quirk above) — a CLOSED door is distinguished by a
# second, double-radius ring drawn around its marker, an OPEN door by a single
# small oval. Distinguish door vs flag/portal points by position/count.
OVERLAY_DOOR_COLOR     = (255, 128, 255, 255) # B=255 -> visible detected doors
OVERLAY_SWITCH_COLOR   = (128, 255, 255, 255) # B=255 -> visible detected switches
OVERLAY_VERTEX_RADIUS  = 8.0                  # world-space pixels
OVERLAY_VERTEX_ASPECT  = 1.0                  # y/x ratio (1.0 = round)

# Cheap screen-space cull before calling the expensive engine line/oval
# helpers. Zax.CFG currently runs at 640x480. The margin keeps near-edge
# nodes and lines visible while skipping most of the off-screen authored graph.
OVERLAY_CULL_MARGIN = 96.0
OVERLAY_CULL_MIN_X  = -OVERLAY_CULL_MARGIN
OVERLAY_CULL_MIN_Y  = -OVERLAY_CULL_MARGIN
OVERLAY_CULL_MAX_X  = 640.0 + OVERLAY_CULL_MARGIN
OVERLAY_CULL_MAX_Y  = 480.0 + OVERLAY_CULL_MARGIN

