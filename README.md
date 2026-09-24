# city-pipeline — pure port of the Medieval Fantasy City Generator

A faithful Python + JS port of **Oleg Dolya's Medieval Fantasy City
Generator** ([watabou/TownGeneratorOS](https://github.com/watabou/TownGeneratorOS),
GPL-3.0). The layout algorithm is translated 1:1 from the original Haxe
sources — same Voronoi mesh, same wards, same cutter, same walls, same
rendering — so towns look and behave like the original.

**This repo is GPL-3.0** (see `LICENSE`) because it ports GPL code.
All credit for the generator and its look goes to Oleg Dolya (watabou).

Live: https://ibackstrom.github.io/city-pipeline/

## What is ported (1:1 from the Haxe sources)

- `Random` — the original LCG (g=48271, n=2^31-1), so the number stream
  matches the original's character
- `Polygon` — square/compactness/shrink/buffer/cut/inset/smooth... the whole
  geometry workhorse, including identity-based vertex sharing between wards
- `Voronoi` — Bowyer-Watson incremental triangulation, regions, relax
- `Graph`/`Topology` — street pathfinding over the mesh
- `Cutter` — bisect / radial / semiRadial / ring
- `CurtainWall` — circumference, gates, towers, outer-ward splitting
- `Model` — the full pipeline: spiral patch seeds → relax → patches →
  junction optimization → walls → streets (A*) → ward assignment
  (rateLocation) → geometry
- All wards: Craftsmen, Merchant, Patriciate, Slum, Market (statue/fountain),
  Castle (own curtain wall), Temple (ring/ortho), Gate, Administration,
  Military, Park, Farm
- Rendering per `CityMap.hx`: DEFAULT palette (paper `#ccc5b8`, light
  `#99948a`, medium `#67635c`, dark `#1a1917`), roads = casing + paper core,
  walls = thick dark + towers + gate marks, buildings = light fill + dark
  thin stroke (Castle/Temple heavier), compass, plaza

Preset locked to the reference link: **size 25, citadel, plaza, temple,
walls, river, auto gates**. (The OS build has no water, so the river is a
drawn band and buildings inside it are dropped — the closed-source app's
water handling is not public.)

## Pipeline use

```bash
python3 city_gen.py --seed 1429458196 --outdir ./out_town   # the reference city
python3 city_gen.py --outdir ./out_town                     # random city
python3 city_gen.py --size 15 --warp 0.3 --outdir ./out_s   # other sizes / warp
```

`buildings.csv` — one row per building polygon (center, yaw, bbox w/d from
its longest-edge frame, `type` = ward label: Craftsmen / Slum / Merchant /
Patriciate / Temple / Gate / Administration / Military / Market / Castle /
Farm):
`id,pos_x,pos_y,pos_z,rot_y_deg,type,width_m,depth_m,height_m,scale,district,seed,warp`

Also `walls.csv`, `roads.csv` (arteries), `river.csv`, `town.json`,
`preview.svg`, `manifest.json`. Straight into Houdini (CSV → points →
`p@orient` → Copy to Points) and UE PCG (DataTable → Selector by type).

## Browser lab

https://ibackstrom.github.io/city-pipeline/ — **new city** (random),
**size** slider (6-40, the original's ward count), **warp** slider.
Hover any building for its type + footprint. Right-click menu like the
original. Warp mode shows the mesh as **red lines**; Displace grabs the
nodes under the brush and they follow the cursor (original feel), plus
Liquify/Rotate/Bloat/Relax/Equalize/Measure with the original keys.
`Export CSV` downloads the same `buildings.csv` (warped state included).

Performance: a size-25 city (800+ buildings) generates in ~20 ms.

## Roadmap (upgrades on top of the pure copy)

1. Map ward polygons to your 5 house footprints + market + castle
   (exact sizes, inscribed per polygon)
2. Houdini/UE PCG presets for the 7 types
3. Original-style JSON export compatibility

Small towns (size < ~16) may drop the citadel when its keep would have no
gate to the city — the geometry makes it unavoidable; the original web app
handles this in newer, closed-source code.
