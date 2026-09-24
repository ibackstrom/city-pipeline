#!/usr/bin/env python3
"""
city-gen pipeline CLI on top of mfcg.py — the faithful port of Oleg Dolya's
Medieval Fantasy City Generator (watabou/TownGeneratorOS, GPL-3.0).

Preset (from the original link): size 25, citadel, plaza, temple, walls,
river, auto gates. Options kept intentionally minimal: seed (or random)
and warp.

Output: buildings.csv (pos/rot/footprint/type per building polygon) for
Houdini/PCG, walls/roads/river CSVs, town.json, preview.svg (original
DEFAULT palette rendering per CityMap.hx).
"""
import argparse
import csv
import json
import math
import os
import random

import mfcg
from mfcg import Pt, distance, p_center, p_square, Random

# original DEFAULT palette
PAPER, LIGHT, MEDIUM, DARK = '#ccc5b8', '#99948a', '#67635c', '#1a1917'
WATER, WATER_D = '#a9c1cf', '#7d99a8'
NORMAL_STROKE = 0.3
THICK_STROKE = 1.8
MAIN_STREET = 2.0


# ---------------------------------------------------------------- river (not in the OS build; drawn as a band + geometry clipped)

def build_river(R, seed_rng):
    ang = seed_rng.random() * math.pi
    w = 9.0 + seed_rng.random() * 3.0
    rdx, rdz = math.cos(ang), math.sin(ang)
    rnx, rnz = -rdz, rdx
    ph = seed_rng.random() * 6.28
    amp = 0.10 * R
    pts = []
    for t in range(-16, 17):
        s = t * R / 12.0
        off = amp * math.sin(s * 0.02 + ph)
        pts.append((rdx * s + rnx * off, rdz * s + rnz * off))
    return pts, w


def river_dist(x, z, river, w):
    best = 1e9
    for i in range(len(river) - 1):
        ax, az = river[i]
        bx, bz = river[i + 1]
        abx, abz = bx - ax, bz - az
        l2 = abx * abx + abz * abz or 1.0
        t = max(0.0, min(1.0, ((x - ax) * abx + (z - az) * abz) / l2))
        d = math.hypot(x - (ax + t * abx), z - (az + t * abz))
        best = min(best, d)
    return best


# ---------------------------------------------------------------- warp mesh (pipeline tool addition: original-style warp on all layers)

class WarpMesh:
    def __init__(self, x0, x1, z0, z1, spacing, rng, warp, R):
        self.s = spacing
        self.nx = max(2, int(math.ceil((x1 - x0) / spacing)) + 1)
        self.nz = max(2, int(math.ceil((z1 - z0) / spacing)) + 1)
        self.x0, self.z0 = x0, z0
        self.base, self.off = [], []
        amp = warp * R * 0.10
        f1 = 0.018 + rng.random() * 0.012
        f2 = 0.045 + rng.random() * 0.02
        p1, p2, p3, p4, p5 = (rng.random() * 6.283 for _ in range(5))
        for j in range(self.nz):
            for i in range(self.nx):
                bx = x0 + i * spacing
                bz = z0 + j * spacing
                self.base.append([bx, bz])
                if warp <= 0:
                    self.off.append([0.0, 0.0])
                else:
                    self.off.append([
                        amp * (0.6 * math.sin(bx * f1 + p1) * math.cos(bz * f1 + p2)
                               + 0.4 * math.sin((bx + bz) * f2 + p3)),
                        amp * (0.6 * math.cos(bx * f1 + p4) * math.sin(bz * f1 + p1)
                               + 0.4 * math.cos((bx - bz) * f2 + p5))])
        self.rad = spacing * 2.2

    def disp(self, x, z):
        dx = dz = ws = 0.0
        for (bx, bz), (ox, oz) in zip(self.base, self.off):
            if ox == 0 and oz == 0:
                continue
            d = math.hypot(x - bx, z - bz)
            if d >= self.rad:
                continue
            w = 0.5 * (1 + math.cos(math.pi * d / self.rad))
            dx += ox * w
            dz += oz * w
            ws += w
        return (dx / ws, dz / ws) if ws else (0.0, 0.0)

    def ev(self, x, z):
        dx, dz = self.disp(x, z)
        return (x + dx, z + dz)

    def ev_poly(self, poly):
        return [self.ev(p.x, p.y) for p in poly]

    def ev_list(self, pts):
        return [self.ev(x, z) for x, z in pts]

    def rot_delta(self, x, z):
        x1, z1 = self.ev(x + 1.0, z)
        x0, z0 = self.ev(x - 1.0, z)
        return math.degrees(math.atan2(z1 - z0, x1 - x0))

    def brush(self, tool, cx, cz, radius, dx=0, dz=0, strength=1.0):
        for k, ((bx, bz), o) in enumerate(zip(self.base, self.off)):
            d = math.hypot(bx - cx, bz - cz)
            if d >= radius:
                continue
            t = 1 - d / radius
            if tool in ('displace', 'bloat'):
                f = 0.5 * (1 + math.cos(math.pi * d / radius))
            else:
                f = t * t * (3 - 2 * t) * t
            if tool == 'displace':
                o[0] += dx * f * strength
                o[1] += dz * f * strength
            elif tool == 'liquify':
                o[0] += (dx * 0.8 + dz * 0.25) * f * 1.4 * strength
                o[1] += (dz * 0.8 - dx * 0.25) * f * 1.4 * strength
            elif tool == 'bloat':
                amt = (math.hypot(dx, dz) or 2.0) * 0.6 * strength
                l = math.hypot(bx - cx, bz - cz) or 1.0
                o[0] += (bx - cx) / l * amt * f
                o[1] += (bz - cz) / l * amt * f
            elif tool == 'rotate':
                ang = (dx + dz) * 0.01 * strength * f
                vx, vz = bx - cx, bz - cz
                c, s = math.cos(ang), math.sin(ang)
                o[0] += cx + vx * c - vz * s - bx
                o[1] += cz + vx * s + vz * c - bz
            elif tool == 'relax':
                o[0] += -o[0] * f * 0.25 * strength
                o[1] += -o[1] * f * 0.25 * strength


# ---------------------------------------------------------------- frame of a building polygon -> scatter point

def poly_frame(poly):
    """Longest-edge frame -> (cx, cz, rot_deg, w, d)."""
    n = len(poly)
    best, blen = 0, -1.0
    for i in range(n):
        l = math.hypot(poly[(i + 1) % n].x - poly[i].x,
                       poly[(i + 1) % n].y - poly[i].y)
        if l > blen:
            blen, best = l, i
    P0, P1 = poly[best], poly[(best + 1) % n]
    ang = math.degrees(math.atan2(P1.y - P0.y, P1.x - P0.x))
    r = math.radians(ang)
    c, s = math.cos(r), math.sin(r)
    xs = [p.x * c + p.y * s for p in poly]
    zs = [-p.x * s + p.y * c for p in poly]
    w = max(xs) - min(xs)
    d = max(zs) - min(zs)
    cx = (min(xs) + max(xs)) / 2 * c - (min(zs) + max(zs)) / 2 * s
    cz = (min(xs) + max(xs)) / 2 * s + (min(zs) + max(zs)) / 2 * c
    return cx, cz, ang, w, d


# ---------------------------------------------------------------- generate town

def generate(seed, warp, size, river_on=True):
    m = mfcg.Model(nPatches=int(size), seed=seed, templeNeeded=True)

    rng = random.Random(seed * 7919 + 17)
    R = max(m.cityRadius, 50.0)
    river, river_w = build_river(R, rng) if river_on else (None, 0.0)

    # warp mesh over the whole map
    allx, ally = [], []
    for p in m.patches:
        for v in p.shape:
            allx.append(v.x); ally.append(v.y)
    mesh = WarpMesh(min(allx) - 20, max(allx) + 20, min(ally) - 20, max(ally) + 20,
                    R / 5.0, rng, warp, R)

    # buildings: ward geometry polygons -> scatter rows (clipped by the river)
    rows = []
    for patch in m.patches:
        ward = patch.ward
        label = ward.getLabel() or 'Empty'
        if label in ('Empty',):
            continue
        for poly in ward.geometry:
            c = p_center(poly)
            if river and river_dist(c.x, c.y, river, river_w) < river_w / 2 + 1.0:
                continue  # in the water
            cx, cz, rot, w, d = poly_frame(poly)
            fx, fz = mesh.ev(cx, cz)
            rows.append({'x': fx, 'z': fz,
                         'rot': (rot + mesh.rot_delta(cx, cz)) % 360,
                         'w': round(w, 2), 'd': round(d, 2),
                         'h': 5.0, 'type': label,
                         'district': label})

    # layers through the mesh
    wall = mesh.ev_list([(v.x, v.y) for v in m.wall.shape]) if m.wall else []
    towers = mesh.ev_list([(t.x, t.y) for t in (m.wall.towers if m.wall else [])])
    gates = mesh.ev_list([(g.x, g.y) for g in m.gates])
    roads = [mesh.ev_list([(p.x, p.y) for p in a]) for a in m.arteries]
    river_f = mesh.ev_list(river) if river else []
    plaza = mesh.ev_poly(m.plaza.shape) if m.plaza else None
    castle_wall = None
    if m.citadel is not None and isinstance(m.citadel.ward, mfcg.Castle):
        castle_wall = mesh.ev_poly(m.citadel.ward.wall.shape)
        castle_towers = mesh.ev_list([(t.x, t.y) for t in m.citadel.ward.wall.towers])

    town = {
        'seed': seed, 'warp': warp, 'size': int(size),
        'radius': R, 'river_w': river_w,
        'rows': rows, 'wall': wall, 'towers': towers, 'gates': gates,
        'roads': roads, 'river': river_f,
        'plaza': plaza, 'castle_wall': castle_wall,
        'wards': [{'label': p.ward.getLabel() or 'Empty',
                   'shape': mesh.ev_poly(p.shape)} for p in m.patches],
        'mesh': mesh,
    }
    return town


# ---------------------------------------------------------------- export

def write_outputs(outdir, town):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, 'buildings.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['id', 'pos_x', 'pos_y', 'pos_z', 'rot_y_deg', 'type',
                    'width_m', 'depth_m', 'height_m', 'scale', 'district',
                    'seed', 'warp'])
        for i, r in enumerate(sorted(town['rows'], key=lambda r: (r['type'], r['x']))):
            w.writerow([i, round(r['x'], 3), 0.0, round(r['z'], 3),
                        round(r['rot'] % 360, 2), r['type'],
                        r['w'], r['d'], r['h'], 1.0, r['district'],
                        town['seed'], town['warp']])
    with open(os.path.join(outdir, 'walls.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['idx', 'x', 'z'])
        for i, (x, z) in enumerate(town['wall']):
            w.writerow([i, round(x, 3), round(z, 3)])
    with open(os.path.join(outdir, 'roads.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['road_id', 'pt_idx', 'x', 'z'])
        for rid, line in enumerate(town['roads']):
            for j, (x, z) in enumerate(line):
                w.writerow([rid, j, round(x, 3), round(z, 3)])
    with open(os.path.join(outdir, 'river.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['pt_idx', 'x', 'z', 'width_m'])
        for j, (x, z) in enumerate(town['river']):
            w.writerow([j, round(x, 3), round(z, 3), round(town['river_w'], 2)])
    counts = {}
    for r in town['rows']:
        counts[r['type']] = counts.get(r['type'], 0) + 1
    with open(os.path.join(outdir, 'manifest.json'), 'w') as f:
        json.dump({'seed': town['seed'], 'warp': town['warp'], 'size': town['size'],
                   'radius': round(town['radius'], 1), 'counts': counts,
                   'total': len(town['rows'])}, f, indent=2)
    with open(os.path.join(outdir, 'town.json'), 'w') as f:
        json.dump({
            'seed': town['seed'], 'warp': town['warp'], 'size': town['size'],
            'radius': town['radius'], 'river_w': town['river_w'],
            'wall': town['wall'], 'towers': town['towers'], 'gates': town['gates'],
            'roads': town['roads'], 'river': town['river'],
            'plaza': town['plaza'], 'castle_wall': town['castle_wall'],
            'wards': town['wards'],
            'buildings': [{'x': round(r['x'], 3), 'z': round(r['z'], 3),
                           'rot': round(r['rot'] % 360, 2), 'type': r['type'],
                           'w': r['w'], 'd': r['d'], 'h': r['h']}
                          for r in town['rows']],
        }, f)
    return counts


# ---------------------------------------------------------------- preview (CityMap.hx rendering rules)

def write_preview(outdir, town):
    rows = town['rows']
    xs = [p[0] for p in town['wall']] + [r['x'] for r in rows]
    zs = [p[1] for p in town['wall']] + [r['z'] for r in rows]
    minx, maxx = min(xs) - 15, max(xs) + 15
    miny, maxy = min(zs) - 15, max(zs) + 15
    W = H = 900
    sc = min(W / (maxx - minx), H / (maxy - miny))

    def X(x):
        return (x - minx) * sc

    def Y(y):
        return (y - miny) * sc

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">']
    parts.append(f'<rect width="100%" height="100%" fill="{PAPER}"/>')
    # river
    if town['river']:
        rpts = ' '.join(f'{X(x):.1f},{Y(z):.1f}' for x, z in town['river'])
        parts.append(f'<polyline points="{rpts}" stroke="{WATER_D}" '
                     f'stroke-width="{town["river_w"]*sc+2:.1f}" fill="none" stroke-linecap="round"/>')
        parts.append(f'<polyline points="{rpts}" stroke="{WATER}" '
                     f'stroke-width="{town["river_w"]*sc:.1f}" fill="none" stroke-linecap="round"/>')
    # roads: medium casing + paper core (drawRoad)
    for line in town['roads']:
        pts = ' '.join(f'{X(x):.1f},{Y(z):.1f}' for x, z in line)
        parts.append(f'<polyline points="{pts}" stroke="{MEDIUM}" '
                     f'stroke-width="{(MAIN_STREET+NORMAL_STROKE)*sc:.1f}" fill="none" stroke-linecap="round"/>')
        parts.append(f'<polyline points="{pts}" stroke="{PAPER}" '
                     f'stroke-width="{(MAIN_STREET-NORMAL_STROKE)*sc:.1f}" fill="none" stroke-linecap="round"/>')
    # walls (drawWall): thick dark + towers + gates
    if town['wall']:
        wpts = ' '.join(f'{X(x):.1f},{Y(z):.1f}' for x, z in town['wall'] + [town['wall'][0]])
        parts.append(f'<polygon points="{wpts}" fill="none" stroke="{DARK}" '
                     f'stroke-width="{THICK_STROKE*sc:.1f}"/>')
        for x, z in town['towers']:
            parts.append(f'<circle cx="{X(x):.1f}" cy="{Y(z):.1f}" '
                         f'r="{THICK_STROKE*sc:.1f}" fill="{DARK}"/>')
        for gx, gz in town['gates']:
            parts.append(f'<circle cx="{X(gx):.1f}" cy="{Y(gz):.1f}" '
                         f'r="{THICK_STROKE*1.2*sc:.1f}" fill="{PAPER}" stroke="{DARK}" '
                         f'stroke-width="{NORMAL_STROKE*2*sc:.1f}"/>')
    if town['castle_wall']:
        cw = town['castle_wall']
        wpts = ' '.join(f'{X(p[0]):.1f},{Y(p[1]):.1f}' for p in cw + [cw[0]])
        parts.append(f'<polygon points="{wpts}" fill="none" stroke="{DARK}" '
                     f'stroke-width="{THICK_STROKE*1.5*sc:.1f}"/>')
        for x, z in town.get('castle_towers', []):
            parts.append(f'<circle cx="{X(x):.1f}" cy="{Y(z):.1f}" '
                         f'r="{THICK_STROKE*1.5*sc:.1f}" fill="{DARK}"/>')
    # buildings: one ink style per CityMap (light fill, dark thin stroke)
    for r in rows:
        a = math.radians(r['rot'])
        c, s = math.cos(a), math.sin(a)
        hw, hd = r['w'] / 2, r['d'] / 2
        pts = []
        for sx, sz in ((1, 1), (1, -1), (-1, -1), (-1, 1), (1, 1)):
            lx, lz = sx * hw, sz * hd
            pts.append(f'{X(r["x"]+lx*c-lz*s):.1f},{Y(r["z"]+lx*s+lz*c):.1f}')
        stroke = NORMAL_STROKE * 2 * (2 if r['type'] in ('Castle', 'Temple') else 1)
        parts.append(f'<polygon points="{" ".join(pts)}" fill="{LIGHT}" '
                     f'stroke="{DARK}" stroke-width="{stroke*sc:.2f}"/>')
    parts.append('</svg>')
    with open(os.path.join(outdir, 'preview.svg'), 'w') as f:
        f.write('\n'.join(parts))


def main():
    ap = argparse.ArgumentParser(
        description='Pure port of the Medieval Fantasy City Generator -> CSV for Houdini/PCG')
    ap.add_argument('--seed', type=int, default=None,
                    help='omit for a random town (echoed in manifest)')
    ap.add_argument('--size', type=int, default=25,
                    help='number of wards (URL size param), default 25')
    ap.add_argument('--warp', type=float, default=0.0, help='0..1 mesh distortion')
    ap.add_argument('--no-river', action='store_true')
    ap.add_argument('--outdir', required=True)
    ap.add_argument('--no-preview', action='store_true')
    a = ap.parse_args()

    seed = a.seed if a.seed is not None else random.randrange(2 ** 31)
    warp = max(0.0, min(1.0, a.warp))
    town = generate(seed, warp, a.size, river_on=not a.no_river)
    counts = write_outputs(a.outdir, town)
    if not a.no_preview:
        write_preview(a.outdir, town)
    print(f'seed={seed} size={a.size} warp={warp} -> {len(town["rows"])} buildings')
    print(f'  counts: {counts}')
    print(f'  wrote: {a.outdir}/buildings.csv (+ walls/roads/river.csv, town.json, preview.svg)')


if __name__ == '__main__':
    main()
