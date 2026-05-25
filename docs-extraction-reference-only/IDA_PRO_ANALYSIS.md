# IDA Pro Analysis - Zax.exe MDL Format

## Executive Summary

**Critical Discovery**: MDL files contain **2D polygon data**, NOT 3D models!

The game uses **DirectDraw (2D API)**, not Direct3D. The "3D" graphics are achieved through:
- Pre-rendered 2D sprites (FRM16 files)
- Frame-based animations (SEQ/SEQ16 files)
- Isometric camera angle creates illusion of 3D

MDL files are used for **gameplay logic only**: collision detection, pathfinding, and trigger zones.

## Evidence

### 1. DirectDraw Import
```
Address: 0x5EA010
Import: DirectDrawCreate from DDRAW.dll
```

DirectDraw is a 2D graphics API. No Direct3D imports found.

### 2. POLY2D Structure (from sub_545FA0 at 0x545FA0)

```c
// Decompiled code shows:
char v8[8];  // POLY2D marker + version
int v9;      // Vertex count

sub_4A1B00(v8, 12);  // Read 12 bytes total

if ( strcmp(v8, aPoly2d) || v8[7] != 7 )  // Check marker and version
    return 0;

v3 = v9;  // Vertex count from bytes 8-11
*(_DWORD *)(this + 8) = v9;  // Store as uint32
```

**Structure**:
```
Offset  Size  Type    Description
------  ----  ------  -----------
0x00    6     char[]  "POLY2D" marker
0x06    1     byte    Padding (0x00)
0x07    1     byte    Version (must be 0x07)
0x08    4     uint32  Vertex count
0x0C    ...   ...     Vertex data
```

### 3. Vertex Data is 2D (from sub_5460B0 at 0x5460B0)

```c
// Bounding box calculation
v4 = *(float *)(*(_DWORD *)a1 + 8 * v3);      // X coordinate
v12 = *(float *)(*(_DWORD *)a1 + 8 * v3 + 4); // Y coordinate

// Each vertex is 8 bytes = 2 floats (X, Y)
// NOT 12 bytes for 3D (X, Y, Z)
```

**Vertex Format**:
```c
struct Vertex2D {
    float x;  // 4 bytes
    float y;  // 4 bytes
};
```

### 4. Polygon Processing (from sub_546240 at 0x546240)

The function calculates:
- **Edge normals** for 2D polygon edges
- **Convexity testing** (2D algorithm)
- **Winding order** detection
- **Angle sum** to check if polygon is closed (2π test)

All operations are 2D-specific. No Z-axis calculations.

### 5. File Format Variants (from sub_516DA0 at 0x516DA0)

```c
switch ( color_depth )
{
    case 8:  use ".mdl"
    case 16: use ".mdl16"
    case 24: use ".mdl24"
    case 32: use ".mdl32"
}
```

The number refers to **color depth** or **rendering mode**, not vertex format.

## Why Previous Parser Failed

The `convert_zax_assets.py` script incorrectly assumes:

1. **Wrong vertex count location**:
   - Script reads: uint16 at POLY2D+7
   - Actual format: uint32 at POLY2D+8
   - Byte at +7 is version marker (0x07), not vertex count

2. **Wrong vertex format**:
   - Script looks for: 3D vertices (X, Y, Z) = 12 bytes each
   - Actual format: 2D vertices (X, Y) = 8 bytes each

3. **Wrong interpretation**:
   - Script tries to extract 3D models
   - Files actually contain 2D collision polygons

### Example: Orb.mdl16

```
Bytes at POLY2D+6-11: 00 07 07 00 00 00

Incorrect interpretation (script):
  - uint16 at +7: 0x0707 = 1799 vertices
  - Looks for 1799 × 12 bytes of 3D data
  - Finds garbage that happens to pass validation

Correct interpretation (IDA):
  - Byte at +7: 0x07 (version marker)
  - uint32 at +8: 0x00000007 = 7 vertices
  - Reads 7 × 8 bytes of 2D polygon data
```

## Actual 3D Graphics Pipeline

Based on the evidence, Zax's graphics work like this:

1. **3D models** are rendered offline (during development)
2. **Rendered frames** are saved as FRM16 textures (16-bit RGB565)
3. **Animation sequences** are stored in SEQ16 files (frame lists)
4. **At runtime**: Game displays pre-rendered 2D sprites using DirectDraw
5. **MDL polygons** handle collision/pathfinding in 2D space

This is similar to games like:
- Diablo (1996) - pre-rendered 3D to 2D sprites
- Fallout (1997) - isometric with pre-rendered graphics
- Baldur's Gate (1998) - pre-rendered backgrounds

## Recommendations

### ✅ DO:
1. **Extract FRM16 textures** - These are the actual visual assets (366/366 working!)
2. **Analyze SEQ16 animations** - Frame sequences for animated sprites
3. **Document MDL polygon format** - For modding/level editing tools
4. **Extract audio files** - Already in standard WAV/OGG format

### ❌ DON'T:
1. **Try to extract 3D models from MDL** - They don't exist
2. **Expect to find 3D vertex data** - It's all 2D polygons
3. **Use the existing MDL parser** - It's based on wrong assumptions

## IDA Pro Function Reference

| Address  | Name         | Purpose                                    |
|----------|--------------|-------------------------------------------|
| 0x545FA0 | sub_545FA0   | Read POLY2D header (12 bytes)            |
| 0x5460B0 | sub_5460B0   | Calculate 2D bounding box                |
| 0x546240 | sub_546240   | Calculate edge normals, test convexity   |
| 0x5482A0 | sub_5482A0   | Write POLY2D header                      |
| 0x516DA0 | sub_516DA0   | Select MDL variant (.mdl16/.mdl24/etc)   |

## String References

| Address  | String   | Usage                                     |
|----------|----------|-------------------------------------------|
| 0x61EAB0 | "POLY2D" | Polygon marker                            |
| 0x61BC34 | ".mdl16" | 16-bit color depth variant                |
| 0x61BC2C | ".mdl24" | 24-bit color depth variant                |
| 0x61BC24 | ".mdl32" | 32-bit color depth variant                |

## Conclusion

The MDL format mystery is solved:
- **MDL files = 2D collision polygons**
- **FRM16 files = Pre-rendered 2D sprites (the actual graphics)**
- **SEQ16 files = Animation frame sequences**
- **Game uses DirectDraw for 2D rendering**

Focus extraction efforts on FRM16 and SEQ16 files for visual assets.



## SEQ/SEQ16 Animation Pipeline (Engine-side)

Addresses and names reflect our IDA database (renamed for clarity):

- 0x56B780 — Sequence_BuildPath
  - Chooses extension based on render depth: .seq, .seq16, .seq24, .seq32
  - Builds full path into a string object; toggles source via the `useAlt` flag
- 0x56B2B0 — Sequence_GetVariantName
  - Returns the variant string ("seq"/"seq16"/"seq24"/"seq32")
- 0x56B320 — Sequence_EnsureProperties
  - Ensures existence of Properties.txt at "%s/-%s/Properties.txt"; fatal if missing and cannot create
- 0x56B890 — Sequence_LoadFromFile
  - High-level open: calls Sequence_BuildPath, clears object, opens stream, then dispatches to the type-specific loader via sub_416FE0 (virtual)
  - Returns true if load succeeded and total frame count > 0
- 0x5600F0 — Sequence_PrepareAndValidate
  - Post-load validator: totalFrames = [this+40], rotations = [this+76], framesPerRotation = [this+72] = totalFrames/rotations
  - Fatal if totalFrames % rotations != 0 (string: "There are not an equal number of frames per rotation in %s.")
  - Allocates/maintains per-frame 8-byte records array at [this+68] (size = 8 * totalFrames); used as x/y anchor offsets per frame
  - Applies anchor offsets via sub_4ABAC0(-x, -y) in rotation/frame order; then calls Sequence_ComputeBounds
- 0x56B570 — Sequence_ComputeBounds
  - Iterates frame pointers in [this+8] and queries each frame’s bounds via a virtual call at vfunc+144; accumulates minX/minY/maxX/maxY into object fields
- 0x4A9340 — FRM_LoadFrame
  - Core FRM16 reader: validates header and section sizes, allocates/copies up to 5 layers, supports raw (0x40) and layered RLE (0x44)
  - Emits fatal errors on corrupt headers ("The *.frm file is corrupt, change the FRAME_FILE_VERSION number.")

Loader flow (confirmed):

- Sequence_LoadFromFile → sub_416FE0 (generic dispatcher/validator)
  - sub_416FE0 reads two floats at the start of the stream (matches SEQ16 header 0x00 and 0x04), performs a check via sub_4153A0, then calls the concrete type’s parse routine (vtbl+60 on the sequence object)
  - After parsing, Sequence_PrepareAndValidate is invoked to size per-rotation data and compute bounds.

Object fields used by the sequence runtime (partially mapped):

- [this+8]   → pointer to array of frame-object pointers (count = [this+40])
- [this+40]  → total frame count (uint32)
- [this+68]  → pointer to per-frame records (8 bytes/entry: signed x,y offsets; remaining words reserved/unused in observed paths)
- [this+72]  → frames per rotation (uint32)
- [this+76]  → rotation count (uint32)
- [this+…]   → aggregate bounds (minX/minY/maxX/maxY) updated by Sequence_ComputeBounds

Notes:
- The concrete parse routine for SEQ/SEQ16 is reached via sub_416FE0’s virtual call (vtbl+60). Name TBD until vtable is recovered.
- FRM frames are embedded back-to-back; FRM_LoadFrame is used whenever a frame object needs to materialize from the stream.


### Concrete SEQ16 parser (vtbl+60 target) and frame materialization

- 0x55F910 — Sequence_ParseSEQ16(this, stream)
  - Entry is called from sub_416FE0 after two header floats are validated
  - Reads 16 bytes into [this+0x0C..+0x1B] (header block: includes frameCount, FPS, and two more fields)
  - Calls 0x56B9D0 (Sequence_ReadFramesSEQ16) to allocate [this+8] (frame pointer array, count=[this+0x28]) and load each frame:
    - For each i, reads 1 presence byte; if nonzero, constructs a frame object and calls FRM_LoadFrame(frame, stream); else stores null
  - Allocates (or re-allocates) [this+0x44] = per-frame record buffer of size 8*frameCount and reads it from stream in one shot (sub_4A1B00)
  - Returns 1 on success, else clears and returns 0

- 0x56B9D0 — Sequence_ReadFramesSEQ16(this, stream)
  - Helper called by Sequence_ParseSEQ16: does the actual per-frame loop invoking FRM_LoadFrame
  - Uses [this+0x28] (frameCount) and writes [this+8] with the pointer-array base

Per-frame 8-byte record structure (confirmed by Sequence_PrepareAndValidate):
- int16 x at +0
- int16 pad0 at +2 (unused)
- int16 y at +4
- int16 pad1 at +6 (unused)
These anchors are applied as sub_4ABAC0(-x, -y) when preparing frames per rotation.

Notes on pre-frame metadata:
- After the 16B block copied at [this+0x0C], files may contain a variable-length metadata segment (e.g., name string and bounds) before the first presence flag. The concrete parser tolerates this, and the flag+frame loop starts only after that segment. Our extractor detects the correct start by probing for a position where FRM16 headers follow present flags cleanly.


## Active Track Manager, Element Array, and Rate Method (vtbl+0x68)

Findings (validated in IDA):
- Manager/array accessor: sub_5173F0 returns an element from an array held by a manager-like object:
  - Count at [mgr+0x48], base pointer at [mgr+0x4].
  - Prototype: `int __thiscall sub_5173F0(_DWORD *this, unsigned int idx)`; returns element or 0 if out-of-bounds.
- Dispatcher: sub_416FE0 (sequence load path) calls `mgr = this->vtbl[2](this)` (vtbl+0x08), then iterates elements and invokes each element’s virtual at offset 0x74:
  <augment_code_snippet mode="EXCERPT">
  ````c
  v7 = this->vtbl[2](this);         // mgr
  for (i=0; i<*(uint*)(v7+0x30); ++i) {
      v9 = *(int*)(*(int*)(v7+4) + 4*i); // element
      if (!(*(ubyte(__thiscall**)(int, void*, void*))(*(int*)v9 + 0x74))(v9, this, stream))
          break;
  }
  // on success: this->vtbl[15](this, stream)  // vtbl+0x60: Sequence_ParseSEQ16
  ````
  </augment_code_snippet>
- This establishes the element (track) vtable and confirms one virtual at +0x74 is consulted during sequence load.

Rate method target:
- By convention in this codebase, the element vtable also contains a rate query at vtbl+0x68 (GetRate). This method is used by the animation system during playback to obtain the timebase.
- Expectation from engine behavior: when the user option “Obey Sequence FPS” (string at 0x61D804) is enabled, the track’s GetRate returns the owning CSequence’s FPS, which is stored at offset +36 (float).

FPS flow (confirmed pieces):
- SEQ16 header embeds FPS (float) in the 16-byte header block; our extractor and observed binaries agree.
- CSequence stores total frame count at [this+40] and (from usage patterns) FPS at [this+36].
- Sequence_PrepareAndValidate (0x5600F0) confirms the per-rotation logic and anchor application; this happens post-parse and before playback.

To be cross-checked in IDA UI (notes added as comments):
- 0x5173F0 commented as ModelManager_GetElement (array getter).
- 0x416FE0 commented to mark element array layout and element->vtbl[0x74] call.

Planned follow-up (if needed):
- Locate update/tick sites that advance animation time and search for virtual dispatch at vtbl+0x68 on the element objects to capture the concrete function address for GetRate and annotate it explicitly.


### Concrete addresses for vtable entries (CSequence)

- VTable region: data ref to Sequence_ParseSEQ16 (0x55F910) is at 0x5FEC50, which is vtbl+0x3C. Thus vtable base ~= 0x5FEC14.
- Key entries:
  - vtbl+0x3C (60): 0x55F910 — Sequence_ParseSEQ16
  - vtbl+0x44 (68): 0x4A5980 — Sequence_GetRate

GetRate pseudocode:
<augment_code_snippet mode="EXCERPT">
````c
// 0x4A5980 — float __thiscall Sequence_GetRate(void* this)
return *(float*)((char*)this + 36); // FPS
````
</augment_code_snippet>

Call site using vtbl+0x68 (GetRate):
<augment_code_snippet mode="EXCERPT">
````c
// 0x517F20 — animation tick
seq = ModelManager_GetElement(modelMgr, currentAnimIdx);
rate = seq->vtbl[0x68/4](seq);   // Sequence_GetRate → FPS at [seq+36]
progress = progress + rate * dt; // accumulated time
````
</augment_code_snippet>

Notes on "Obey Sequence FPS":
- The UI/prop registration for the string "Obey Sequence FPS" appears under particle systems init (0x528600, 0x52AA40). We do not see that flag read in CSequence::GetRate (0x4A5980) nor at the primary tick site (0x517F20).
- Conclusion: In the core animation path examined here, GetRate always returns the SEQ16 FPS stored at [this+36]. If an override exists, it would be implemented by a different vtable/class or by rewriting the FPS field earlier; we found no evidence of a conditional in GetRate itself.
