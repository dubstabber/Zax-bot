# WAY Format

Status: IDA-backed reader implemented in `tools/extract_way.py`.

Zax `.way` files store runtime waypoint maps. The loader path is:

- `sub_4ECA80`: opens `Levels/<map>.way`.
- `sub_537AE0`: parses `CWayPointMap`.
- `sub_53A470`: registers `CPathWayPoint` fields `Flags`, `Position X`, and `Position Y`.
- `sub_53A6A0`: reads each waypoint connection list.

All integers are little-endian.

## CWayPointMap Header

| Offset | Type | Meaning |
| --- | --- | --- |
| `0x00` | float32 | object key, always `27.0` in observed files |
| `0x04` | float32 | class key, `43.0` for `CWayPointMap` |
| `0x08` | uint32 | serialized base field, unknown, preserved as `base_unknown` |
| `0x0C` | uint32 | `MinDistBetweenWayPoints` |
| `0x10` | uint32 | `MaxDistToConnect` |
| `0x14` | uint32 | waypoint count |
| `0x18` | ... | `CPathWayPoint` records |

## CPathWayPoint Record

| Offset | Type | Meaning |
| --- | --- | --- |
| `+0x00` | float32 | object key, `27.0` |
| `+0x04` | float32 | class key, `43.0` |
| `+0x08` | uint32 | serialized base field, unknown |
| `+0x0C` | uint32 | `Flags` |
| `+0x10` | uint32 | `Position X` |
| `+0x14` | uint32 | `Position Y` |
| `+0x18` | uint32 | connection count |
| `+0x1C` | uint32[] | connected waypoint IDs |

Record size is `0x1C + connection_count * 4`.

## Edge Records

After all waypoints, a uint32 edge count is followed by fixed-size 36-byte
records. These records have class key `64.0`. Observed fields match path-edge
metrics:

| Offset | Type | Meaning |
| --- | --- | --- |
| `+0x00` | float32 | object key, `27.0` |
| `+0x04` | float32 | class key, `64.0` |
| `+0x08` | uint32 | serialized base field, unknown |
| `+0x0C` | uint32 | flags |
| `+0x10` | uint32 | reserved/unknown |
| `+0x14` | float32 | distance |
| `+0x18` | float32 | angle in radians |
| `+0x1C` | uint32 | endpoint waypoint index A |
| `+0x20` | uint32 | endpoint waypoint index B |

The exporter writes this as `extracted/waypoints/<source>/<name>.json`.
