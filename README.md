# Small-town pipeline: seed+warp -> CSV -> Houdini scatter -> Unreal PCG

Stdlib-only Python. No install. 1 unit = 1 meter, Y-up.

City preset copied from the original link: size 25 (~162 m radius),
citadel + castle, central plaza + market, temple facing the plaza, walls
with towers + gates, river. Only two controls: **random town** (no seed —
each click is a fresh city; the used seed is echoed for repro) and **warp**.
5 common building types + market + castle — ~450 typed scatter points per
city, packed in bands (92% of buildings have a neighbor within 10 m).

Model (Parish & Muller 2001 road growth + CityGen blocks + Watabou wards):
arterial/ring skeleton → **blocks/wards** (split by ward streets, drawn thin)
→ each block inset from its streets (`getCityBlock`) and filled with
**bands of like houses** along the street frontage — exact footprints walked
edge-to-edge, 0.9 m side gaps, 1.6 m band alleys, occasional vacant lots.
87% of buildings have a neighbor within 10 m: packed chunks, not scatter.
Rendered in the original DEFAULT palette (paper `#ccc5b8`, light `#99948a`,
medium `#67635c`, dark `#1a1917`): roads as medium casing + paper core,
walls dark thick with towers, single ink style for all buildings.

## 0. Browser lab (original look, warp UX, small town only)

https://ibackstrom.github.io/city-pipeline/ — fullscreen parchment map,
locked **City · 25** preset, random town button + warp slider (no seed).
Warp mode shows the mesh as **red lines** with a red brush ring, like the
original. Right-click opens
the menu (New town / Warp mode / Export CSV / Export JSON), like the
original; warp mode shows the tool panel + mesh lattice and the brush ring.
`Enter` = new town / apply warp, `Esc` = discard warp, `W` toggles warp mode.

| Tool | Key | Does |
|------|-----|------|
| Displace | D | pull mesh nodes (default warp brush) |
| Liquify | L | softer smear |
| Rotate | R | twist the mesh around the brush |
| Bloat | B | inflate / push cells outward |
| Relax | X | smooth the mesh, fix short edges |
| Equalize | E | snap building angles to 15° |
| Measure | M | click two points → meters |

Wheel or `+`/`-` = brush size.
`Export CSV` downloads `buildings.csv` with the same columns as the Python
output (warped state included) — straight into Houdini/PCG.

Limited geometry by design: small-town radius only (~110 m), coarse warp
lattice (~R/6 spacing), ~100 buildings, plain canvas fills — no heavy layers.
Rendering matches the original: alleys are never drawn (they exist only as
gaps between buildings), roads are thin casings, buildings one ink style.

## 1. Generate (your Watabou preset)

```bash
# random city (size 25 preset, warp 0.35) — seed echoed in manifest.json
python3 city_gen.py --warp 0.35 --outdir ./out_city

# reproducible run (same layout every time)
python3 city_gen.py --seed 1429458196 --warp 0.35 --outdir ./out_city
```

`warp` = Watabou warp-tool equivalent: domain distortion of walls, roads,
river, plots. `0` = clean circle, `1` = heavily twisted. Deterministic per seed.

Output in `outdir/`:
- `buildings.csv` — scatter points (main). Columns:
  `id,pos_x,pos_y,pos_z,rot_y_deg,type,width_m,depth_m,height_m,scale,district,seed,warp`
- `walls.csv, roads.csv (incl. alleys), river.csv` — context lines
- `blocks.csv` — block outlines (one polygon per block)
- `mesh.csv` — warp mesh nodes (base + offset); brushes edit offsets
- `town.json` — base + mesh + final for the browser lab
- `preview.svg` — blocks, lots implied, buildings, roads
- `manifest.json` — seed/warp echo + counts

Placement is band-based: each block is inset from its streets and packed
with rows of like houses (weighted type per band, district-biased: big
buildings central, cottages at the edge), exact footprints, OBB overlap
rejection. A post-warp overlap cull drops the smaller building of any pair
the mesh distortion intersects (market/castle are never dropped), so the
exported CSV is always collision-free for scattering.

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
