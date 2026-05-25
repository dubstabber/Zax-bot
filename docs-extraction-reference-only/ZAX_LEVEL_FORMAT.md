# ZAX Level/Text Object Format

Status: text-object parser implemented in `tools/zax_text_object.py`.

Most remaining non-binary metadata assets are not opaque containers. `.zax`,
`.sty`, `.zgt`, `.rds`, `.quips`, `.txt`, `.can`, `.dificulty`, and `.foo`
files are text resources using Zax's object serialization style:

```text
ClassName
{
    Key=Value
    Key=NestedClass
    {
        Nested Key=Nested Value
    }
}
```

## Important Rules

- Repeated keys are valid and common. The JSON exporter preserves ordered
  `entries` instead of collapsing fields into a dictionary.
- Values remain strings. Type coercion is intentionally deferred because the
  executable's class/property registry defines semantics per class.
- Empty values are preserved as empty strings.
- Nested objects keep their class names and source line numbers.

## IDA Evidence

- `sub_4EC090` wraps `.ZAX` loading and logs `Loading .ZAX map file`.
- The `CLayerSaveData` string is heavily referenced by the map/property loader.
- `sub_588310` builds `.STY` paths for `CSurfaceType`.
- `.RDS`, `.ZGT`, `.QUIPS`, and `.TXT` samples are plain text in the shipped
  corpus.

## Outputs

`tools/batch_extract.py` now copies raw text assets and writes parsed JSON:

- `.zax` -> `extracted/levels/`
- `.sty` -> `extracted/surface_types/`
- `.zgt` -> `extracted/game_types/`
- `.rds` and `.quips` -> `extracted/dialogue/`
- `.txt` -> `extracted/text/`
- `.can` -> `extracted/canned_objects/`
- `.dificulty` -> `extracted/difficulty/`
- `.foo` -> `extracted/frame_overlays/`
