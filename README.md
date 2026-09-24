# Small-town pipeline: seed+warp -> CSV -> Houdini scatter -> Unreal PCG

Stdlib-only Python. No install. 1 unit = 1 meter, Y-up.

## 0. Browser lab (warp UI, same system as the original)

https://ibackstrom.github.io/city-pipeline/ — seed/warp/size inputs, canvas
preview, warp brushes ported from the original toolset:

| Tool | Key | Does |
|------|-----|------|
| Displace | D | pull mesh nodes (default warp brush) |
| Liquify | L | softer smear |
| Rotate | R | twist around brush center |
| Bloat | B | inflate / push cells outward |
| Relax | X | smooth mesh, fix short edges |
| Equalize | E | snap building angles to 15° |
| Measure | M | click two points → meters |

Wheel or `+`/`-` = brush size. `Enter` = regenerate, `Esc` = discard warp.
`Export CSV` downloads `buildings.csv` with the same columns as the Python
output (warped state included) — straight into Houdini/PCG.

## 1. Generate (your Watabou preset)

```bash
# exact small town from your link: citadel+walls+river, size 17
python3 city_gen.py --seed 1555148727 --warp 0.35 --size 17 --outdir ./out_town

# other options
python3 city_gen.py --seed 42 --warp 0.0  --size 17 --outdir ./out_round   # no warp = regular
python3 city_gen.py --seed 42 --warp 0.8  --size 17 --outdir ./out_organic # heavy warp
python3 city_gen.py --seed 7  --warp 0.4  --radius 110 --outdir ./out      # explicit meters
```

`warp` = Watabou warp-tool equivalent: domain distortion of walls, roads,
river, plots. `0` = clean circle, `1` = heavily twisted. Deterministic per seed.

Output in `outdir/`:
- `buildings.csv` — scatter points (main). Columns:
  `id,pos_x,pos_y,pos_z,rot_y_deg,type,width_m,depth_m,height_m,scale,district,seed,warp`
- `walls.csv, roads.csv, river.csv` — context lines
- `town.json` — full geometry for the browser lab
- `preview.svg` — open in browser for quick check
- `manifest.json` — seed/warp echo + counts

Placement is Watabou-style tight: street-front lots line both sides of every
road (weighted types, district-biased), then block interiors are infilled —
exact footprints, 0.8 m alleys, OBB overlap rejection.

## 2. Fit your actual house sizes

Edit `buildings.json`. The generator places these **exact** footprints
(no auto-rescale), with 0.8 m alleys, road/river/wall clearance, OBB overlap
rejection, road-aligned rotation:

```json
{"name": "house_A_cottage", "w": 6, "d": 8, "h": 4.5, "weight": 30}
```

- `w,d,h` = your asset footprint in meters
- `weight` = frequency (market/castle have weight 0, placed once each)
- 5 houses + market + castle = 7 rows by default; add/remove rows freely

Re-run after editing.

## 3. Houdini scatter

`buildings.csv` is already the point cloud — just instantiate:

1. `File > Import > CSV` or Python SOP:
```python
# Python SOP, reads buildings.csv -> points with attrs
import csv
node = hou.pwd()
geo = node.geometry()
with open(hou.ch(node.parm('csv').eval()) ) as f:
    for r in csv.DictReader(f):
        p = geo.createPoint()
        p.setPosition((float(r['pos_x']), float(r['pos_y']), float(r['pos_z'])))
        p.setAttribValue('type', r['type'])
        p.setAttribValue('yrot', float(r['rot_y_deg']))
        p.setAttribValue('Cd', (1,1,1))
        for k in ('width_m','depth_m','height_m'):
            p.setAttribValue(k, float(r[k]))
```
2. `Attribute Wrangle`: `p@orient = quaternion(radians(ch('yrot')), {0,1,0});`
3. `For-Each by type` + `Copy to Points` (or `Instance` SOP) mapping:
   `house_A_cottage -> /assets/house_A`, etc. Footprints already match,
   so use scale `1.0` (`scale` column reserved for LOD variants).
4. Optional: import `walls.csv` / `roads.csv` / `river.csv` as polylines
   (`Add SOP` / `Trace`) for wall spline + road decal + water plane.

Units: meters match Houdini default. Z-forward = Unreal-compatible.

## 4. Unreal PCG

1. Import `buildings.csv` as `DataTable` with struct:
   `pos_x,pos_y,pos_z,float; rot_y_deg,float; type,Name; width_m,depth_m,height_m,float; district,Name`
2. PCG graph:
   `Get Data Table -> Create Points (pos_x, 0, pos_z) -> Set Attribute rot/type/size`
   `-> Transform Points (yaw = rot_y_deg) -> Selector by type`
   `-> 7x Static Mesh Spawner (one per house_A..E, market, castle)`
3. Mapping table (must match `buildings.json` names):
   `house_A_cottage, house_B_row, house_C_merchant, house_D_hall, house_E_warehouse, market, castle`
4. Scale stays `1.0`; PCG `Point Scale` only if you intentionally resize an asset.
5. `walls/roads/river.csv` -> `Spline` actors for wall mesh, road decals, river water.

That's it: tweak seed/warp, re-export CSV, reimport DataTable.
