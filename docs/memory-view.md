# Memory view (phase 2 plan)

Not built yet. Goal: like javawiz's heap view, show objects as boxes with
arrows for references, so aliasing and mutation are visible ("both carts point
at the same LineItem").

## What the trace needs (schema version 2)

Today a local is a rendered string. For a heap we need identity:

```jsonc
"locals": { "cart": { "ref": 17 }, "n": { "v": "3" } },
"heap": { "17": { "type": "Cart", "fields": { "items": { "ref": 18 } } } }   // per step, as a diff
```

- The Python tracer keeps `id(obj) → stable object number` (it already pins
  objects for actor labels) and walks reachable objects from locals with the
  same depth/size limits, recording only objects that changed since the last
  step (`heap_diff` per step) to keep files small.
- Scalars and strings stay inline values.
- DAP recorders get the same from `variablesReference` (a stable handle per
  object while the program is paused).

## Viewer

A fourth panel (tab on phones): stack frames on the left, objects on the
right, SVG arrows between them, the objects touched at the current step
highlighted. Layout: objects in columns by distance from the stack.
