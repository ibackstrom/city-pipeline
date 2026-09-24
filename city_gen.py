#!/usr/bin/env python3
"""
Small-town city generator (Watabou-like) -> CSV points for Houdini / Unreal PCG.

Usage:
    python3 city_gen.py --seed 1555148727 --warp 0.35 --size 17 --outdir ./out_town
    python3 city_gen.py --seed 42 --warp 0.0 --radius 110 --outdir ./out

Inputs: seed (int), warp (0..1 mesh distortion like Watabou warp tool).
Output:
    outdir/buildings.csv  -> x, z, rot_y_deg, type, w, d, h  (scatter points)
    outdir/walls.csv, roads.csv, river.csv (context lines)
    outdir/preview.svg    -> quick 2D check
    outdir/manifest.json  -> seed/warp/config echo for reproducibility

Coordinate system: Y-up. CSV gives pos_x, pos_z on ground (y=0). rot_y_deg = yaw
around Y, degrees, compatible with Houdini (p@yrot) and Unreal PCG
(GetActorTransform-style yaw). 1 unit = 1 meter.

Only stdlib. No install needed.
"""
import argparse
import csv
import json
import math
import os
import random
import sys

# ---------------------------------------------------------------- utils

def warp_displacement(x, z, warp, R, P):
    """Domain-warp offset (dx, dz). P = dict of seeded phases/freqs."""
    if warp <= 0.0:
        return 0.0, 0.0
    amp = warp * R * 0.35
    # two layered sine fields -> organic twist like Watabou warp brush
    dx = amp * (0.6 * math.sin(x * P['f1'] + P['p1']) * math.cos(z * P['f1'] + P['p2'])
                + 0.4 * math.sin((x + z) * P['f2'] + P['p3']))
    dz = amp * (0.6 * math.cos(x * P['f1'] + P['p4']) * math.sin(z * P['f1'] + P['p1'])
                + 0.4 * math.cos((x - z) * P['f2'] + P['p5']))
    return dx, dz


def dist_pt_seg(px, pz, ax, az, bx, bz):
    abx, abz = bx - ax, bz - az
    l2 = abx * abx + abz * abz
    if l2 == 0:
        return math.hypot(px - ax, pz - az), ax, az, 0.0
    t = max(0.0, min(1.0, ((px - ax) * abx + (pz - az) * abz) / l2))
    cx, cz = ax + t * abx, az + t * abz
    return math.hypot(px - cx, pz - cz), cx, cz, t


def dist_to_polylines(px, pz, polylines):
    best = 1e9
    best_ang = 0.0
    for line in polylines:
        for i in range(len(line) - 1):
            ax, az = line[i]
            bx, bz = line[i + 1]
            d, _, _, _ = dist_pt_seg(px, pz, ax, az, bx, bz)
            if d < best:
                best = d
                best_ang = math.degrees(math.atan2(bz - az, bx - ax))
    return best, best_ang


def point_in_poly(x, z, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, z1 = poly[i]
        x2, z2 = poly[(i + 1) % n]
        if ((z1 > z) != (z2 > z)) and (x < (x2 - x1) * (z - z1) / (z2 - z1 + 1e-12) + x1):
            inside = not inside
    return inside


def obb_corners(cx, cz, w, d, rot_deg):
    r = math.radians(rot_deg)
    c, s = math.cos(r), math.sin(r)
    hw, hd = w / 2.0, d / 2.0
    pts = []
    for sx, sz in ((1, 1), (1, -1), (-1, -1), (-1, 1)):
        lx, lz = sx * hw, sz * hd
        pts.append((cx + lx * c - lz * s, cz + lx * s + lz * c))
    return pts


def obb_overlap(a, b):
    """SAT on 2 rects given as corner lists."""
    for poly in (a, b):
        for i in range(4):
            x1, z1 = poly[i]
            x2, z2 = poly[(i + 1) % 4]
            nx, nz = -(z2 - z1), x2 - x1
            l = math.hypot(nx, nz) or 1.0
            nx, nz = nx / l, nz / l
            pa = [p[0] * nx + p[1] * nz for p in a]
            pb = [p[0] * nx + p[1] * nz for p in b]
            if max(pa) < min(pb) or max(pb) < min(pa):
                return False
    return True


# ---------------------------------------------------------------- generation

def generate(seed, warp, R, lib):
    rng = random.Random(seed)
    wph = {  # warp phases from seed so warp is deterministic per seed
        'f1': 0.018 + rng.random() * 0.012,
        'f2': 0.045 + rng.random() * 0.020,
        'p1': rng.random() * 6.283, 'p2': rng.random() * 6.283,
        'p3': rng.random() * 6.283, 'p4': rng.random() * 6.283,
        'p5': rng.random() * 6.283,
    }

    def W(x, z):
        return warp_displacement(x, z, warp, R, wph)

    # ---- walls: noisy circle + warp
    N_WALL = 48
    wall = []
    wob1, wob2 = rng.random() * 6.28, rng.random() * 6.28
    for i in range(N_WALL):
        a = 2 * math.pi * i / N_WALL
        bx, bz = math.cos(a) * R, math.sin(a) * R
        # small organic wobble (always on) + warp displacement
        rr = R * (1.0 + 0.05 * math.sin(3 * a + wob1) + 0.03 * math.sin(5 * a + wob2))
        bx, bz = math.cos(a) * rr, math.sin(a) * rr
        dx, dz = W(bx, bz)
        wall.append((bx + dx, bz + dz))

    # ---- river: straight-ish band across town, meandering, broken by bridge at center
    river_ang = rng.random() * math.pi  # direction of flow
    river_w = 9.0 + rng.random() * 3.0
    rdx, rdz = math.cos(river_ang), math.sin(river_ang)   # along flow
    rnx, rnz = -rdz, rdx                                   # normal
    meander_ph = rng.random() * 6.28
    meander_amp = 12.0 + warp * 22.0
    river = []
    for t in range(-14, 15):
        s = t * R / 12.0
        off = meander_amp * math.sin(s * 0.02 + meander_ph)
        bx = rdx * s + rnx * off
        bz = rdz * s + rnz * off
        dx, dz = W(bx, bz)
        river.append((bx + dx, bz + dz))

    def river_dist(x, z):
        return dist_to_polylines(x, z, [river])[0]

    # ---- citadel (castle hill), offset from center
    cit_ang = rng.random() * 2 * math.pi
    cit_r = R * 0.32
    cit_c = (math.cos(cit_ang) * cit_r, math.sin(cit_ang) * cit_r)
    # push citadel off the river
    if river_dist(*cit_c) < river_w / 2 + 26:
        cit_c = (-cit_c[0], -cit_c[1])
    cit_R = 24.0

    # ---- center / market plaza (near origin, snapped off river)
    market_c = (0.0, 0.0)
    if river_dist(0, 0) < river_w / 2 + 14:
        # shift along river normal until clear
        for k in range(1, 8):
            for sgn in (1, -1):
                cand = (rnx * sgn * k * 8.0, rnz * sgn * k * 8.0)
                if river_dist(*cand) > river_w / 2 + 14:
                    market_c = cand
                    break
            else:
                continue
            break

    # ---- roads: radials from market to wall + 2 rings
    n_radial = 7
    radials = []
    base = rng.random() * 2 * math.pi
    for i in range(n_radial):
        a = base + 2 * math.pi * i / n_radial + rng.uniform(-0.12, 0.12)
        pts = []
        steps = 7
        # end point: raycast to wall polygon approx = wall vertex nearest angle
        ex, ez = math.cos(a) * R * 1.02, math.sin(a) * R * 1.02
        for s in range(steps + 1):
            t = s / steps
            bx = market_c[0] + (ex - market_c[0]) * t
            bz = market_c[1] + (ez - market_c[1]) * t
            # slight lateral wiggle
            wig = math.sin(t * 5.0 + a * 3.0) * (4.0 + warp * 8.0) * math.sin(t * math.pi)
            bx += -math.sin(a) * wig
            bz += math.cos(a) * wig
            dx, dz = W(bx, bz)
            pts.append((bx + dx, bz + dz))
        radials.append(pts)

    rings = []
    for frac in (0.45, 0.75):
        pts = []
        for i in range(N_WALL):
            a = 2 * math.pi * i / N_WALL
            bx, bz = market_c[0] + math.cos(a) * R * frac, market_c[1] + math.sin(a) * R * frac
            dx, dz = W(bx, bz)
            pts.append((bx + dx, bz + dz))
        pts.append(pts[0])
        rings.append(pts)

    all_roads = radials + rings
    ROAD_HALF = 2.5

    # ---- gates: where radials exit wall (just for info/export)
    gates = []
    for r in radials:
        gates.append(r[-1])

    # ---- building placement: Watabou-style tight lots facing streets
    # Phase 1 = street-front lots (rows packed along every road, both sides).
    # Phase 2 = random infill for block interiors. Strict footprints, no rescale.
    gap = lib.get('gap_m', 0.8)
    road_clear = lib.get('road_clearance_m', 1.0)
    house_types = [t for t in lib['types'] if t['weight'] > 0]
    order_small = sorted(house_types, key=lambda t: (t['w'] * t['d']))
    order_big = sorted(house_types, key=lambda t: -(t['w'] * t['d']))
    placed = []       # dicts
    placed_obbs = []  # inflated corner lists for overlap test

    def try_place(cx, cz, rot, spec, district):
        w, d = spec['w'], spec['d']
        # clearance checks
        if not point_in_poly(cx, cz, wall):
            return False
        # full ring distance
        dmin = 1e9
        for i in range(len(wall)):
            ax, az = wall[i]
            bx, bz = wall[(i + 1) % len(wall)]
            dd, _, _, _ = dist_pt_seg(cx, cz, ax, az, bx, bz)
            dmin = min(dmin, dd)
        if dmin < max(w, d) / 2 + 2.5:
            return False
        if river_dist(cx, cz) < river_w / 2 + max(w, d) / 2 + 2.0:
            return False
        if math.hypot(cx - cit_c[0], cz - cit_c[1]) < cit_R + max(w, d) / 2 + 1.5:
            return False
        droad, road_ang = dist_to_polylines(cx, cz, all_roads)
        # tight: keep off the carriageway but allow hugging the street
        if droad < ROAD_HALF + road_clear + min(w, d) / 2 * 0.35:
            return False
        corners = obb_corners(cx, cz, w + gap, d + gap, rot)
        for oc in placed_obbs:
            if obb_overlap(corners, oc):
                return False
        # corners of real footprint must also pass river/road (avoid straddling)
        for (qx, qz) in obb_corners(cx, cz, w, d, rot):
            if not point_in_poly(qx, qz, wall):
                return False
            if river_dist(qx, qz) < river_w / 2 + 1.0:
                return False
            if math.hypot(qx - cit_c[0], qz - cit_c[1]) < cit_R:
                return False
        placed.append({'x': cx, 'z': cz, 'rot': rot, 'spec': spec, 'district': district})
        placed_obbs.append(corners)
        return True

    # castle first (in citadel), rotated to face market
    castle_spec = next(t for t in lib['types'] if t['name'] == 'castle')
    ang_to_market = math.degrees(math.atan2(market_c[1] - cit_c[1], market_c[0] - cit_c[0]))
    placed.append({'x': cit_c[0], 'z': cit_c[1], 'rot': -ang_to_market,
                   'spec': castle_spec, 'district': 'citadel'})
    placed_obbs.append(obb_corners(cit_c[0], cit_c[1],
                                   castle_spec['w'] + gap, castle_spec['d'] + gap, -ang_to_market))

    # market hall at plaza
    market_spec = next(t for t in lib['types'] if t['name'] == 'market')
    placed.append({'x': market_c[0], 'z': market_c[1], 'rot': rng.uniform(0, 90),
                   'spec': market_spec, 'district': 'market'})
    placed_obbs.append(obb_corners(market_c[0], market_c[1],
                                   market_spec['w'] + gap, market_spec['d'] + gap,
                                   placed[-1]['rot']))

    # Phase 1: street-front lots — walk every road, drop lots on both sides,
    # small houses first so rows pack tightly like Watabou lots.
    street_segs = []
    for line in all_roads:
        for i in range(len(line) - 1):
            street_segs.append((line[i], line[i + 1]))
    rng.shuffle(street_segs)
    for (ax, az), (bx, bz) in street_segs:
        seg_len = math.hypot(bx - ax, bz - az)
        if seg_len < 1e-6:
            continue
        seg_ang = math.degrees(math.atan2(bz - az, bx - ax))
        nx, nz = -(bz - az) / seg_len, (bx - ax) / seg_len  # road normal
        t = rng.uniform(0, 0.3)
        while t < 1.0:
            t += (3.5 + rng.random() * 3.0) / max(seg_len, 1e-6)
            if t >= 1.0:
                break
            px, pz = ax + (bx - ax) * t, az + (bz - az) * t
            for side in (-1, 1):
                if rng.random() < 0.15:
                    continue  # occasional gap: alley / courtyard / well
                # weighted pick (district-filtered) so big houses still win rows
                # near the center — try up to 3 candidates per lot
                r_norm = math.hypot(px - market_c[0], pz - market_c[1]) / R
                if r_norm < 0.35:
                    cands = [s for s in house_types if s['w'] * s['d'] >= 48] or house_types
                elif r_norm > 0.7:
                    cands = [s for s in house_types if s['w'] * s['d'] <= 100] or house_types
                else:
                    cands = list(house_types)
                cw = [s['weight'] for s in cands]
                tried = set()
                for _ in range(3):
                    spec = rng.choices(cands, weights=cw, k=1)[0]
                    if spec['name'] in tried:
                        continue
                    tried.add(spec['name'])
                    off = ROAD_HALF + road_clear + spec['d'] / 2 + rng.uniform(0, 1.0)
                    cx = px + nx * side * off + rng.uniform(-0.8, 0.8)
                    cz = pz + nz * side * off + rng.uniform(-0.8, 0.8)
                    # parallel or perpendicular to street, tiny jitter
                    rot = seg_ang + (0 if rng.random() < 0.7 else 90) + rng.uniform(-4, 4)
                    district = 'center' if r_norm < 0.35 else ('mid' if r_norm < 0.7 else 'edge')
                    if try_place(cx, cz, rot, spec, district):
                        break

    # Phase 2: infill block interiors, big-to-small so halls still find room
    max_attempts = 25000
    attempts = 0
    # target counts roughly proportional to weight but limited by space
    while attempts < max_attempts:
        attempts += 1
        # random point in bounding box of walls
        xs = [p[0] for p in wall]; zs = [p[1] for p in wall]
        cx = rng.uniform(min(xs), max(xs))
        cz = rng.uniform(min(zs), max(zs))
        r_norm = math.hypot(cx - market_c[0], cz - market_c[1]) / R
        # district + size bias: center prefers big, edge prefers small
        if r_norm < 0.35:
            pool = [t for t in order_big if t['w'] * t['d'] >= 50]
            district = 'center'
        elif r_norm < 0.7:
            pool = order_big
            district = 'mid'
        else:
            pool = [t for t in order_big if t['w'] * t['d'] <= 100]
            district = 'edge'
        if not pool:
            pool = order_big
        # weighted pick inside pool
        pw = [t['weight'] for t in pool]
        spec = rng.choices(pool, weights=pw, k=1)[0]
        droad, road_ang = dist_to_polylines(cx, cz, all_roads)
        # align to nearest road (parallel or perpendicular), plus jitter
        base_rot = road_ang + (90 if rng.random() < 0.5 else 0)
        rot = base_rot + rng.uniform(-8, 8)
        try_place(cx, cz, rot, spec, district)

    # sort for stable output
    placed.sort(key=lambda p: (p['spec']['name'], p['x'], p['z']))
    meta = {
        'seed': seed, 'warp': warp, 'R': R,
        'citadel': {'x': cit_c[0], 'z': cit_c[1], 'r': cit_R},
        'market': {'x': market_c[0], 'z': market_c[1]},
        'river_w': river_w, 'counts': {},
    }
    for p in placed:
        meta['counts'][p['spec']['name']] = meta['counts'].get(p['spec']['name'], 0) + 1
    return placed, wall, all_roads, river, river_w, gates, meta


# ---------------------------------------------------------------- export

def write_csvs(outdir, placed, wall, roads, river, gates, meta, seed, warp, lib=None, R=None):
    os.makedirs(outdir, exist_ok=True)
    bp = os.path.join(outdir, 'buildings.csv')
    with open(bp, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['id', 'pos_x', 'pos_y', 'pos_z', 'rot_y_deg', 'type',
                    'width_m', 'depth_m', 'height_m', 'scale', 'district', 'seed', 'warp'])
        for i, p in enumerate(placed):
            s = p['spec']
            w.writerow([i, round(p['x'], 3), 0.0, round(p['z'], 3),
                        round(p['rot'] % 360, 2), s['name'],
                        s['w'], s['d'], s.get('h', 5.0), 1.0,
                        p['district'], seed, warp])
    with open(os.path.join(outdir, 'walls.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['idx', 'x', 'z'])
        for i, (x, z) in enumerate(wall):
            w.writerow([i, round(x, 3), round(z, 3)])
    with open(os.path.join(outdir, 'roads.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['road_id', 'pt_idx', 'x', 'z'])
        for rid, line in enumerate(roads):
            for j, (x, z) in enumerate(line):
                w.writerow([rid, j, round(x, 3), round(z, 3)])
    with open(os.path.join(outdir, 'river.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['pt_idx', 'x', 'z', 'width_m'])
        for j, (x, z) in enumerate(river):
            w.writerow([j, round(x, 3), round(z, 3), round(meta['river_w'], 2)])
    with open(os.path.join(outdir, 'manifest.json'), 'w') as f:
        json.dump(meta, f, indent=2)
    # town.json: full geometry for the browser warp UI (same system as Python)
    with open(os.path.join(outdir, 'town.json'), 'w') as f:
        json.dump({
            'seed': seed, 'warp': warp, 'R': R, 'meta': meta,
            'wall': [[round(x, 3), round(z, 3)] for x, z in wall],
            'roads': [[[round(x, 3), round(z, 3)] for x, z in line] for line in roads],
            'river': [[round(x, 3), round(z, 3)] for x, z in river],
            'buildings': [
                {'x': round(p['x'], 3), 'z': round(p['z'], 3),
                 'rot': round(p['rot'] % 360, 2), 'type': p['spec']['name'],
                 'w': p['spec']['w'], 'd': p['spec']['d'],
                 'h': p['spec'].get('h', 5.0), 'district': p['district']}
                for p in placed
            ],
        }, f)
    return bp


def write_preview(outdir, placed, wall, roads, river, river_w, meta, lib):
    colors = {t['name']: t.get('color', '#cccccc') for t in lib['types']}
    xs = [p[0] for p in wall] + [p['x'] for p in placed]
    zs = [p[1] for p in wall] + [p['z'] for p in placed]
    minx, maxx = min(xs) - 10, max(xs) + 10
    minz, maxz = min(zs) - 10, max(zs) + 10
    W, H = 800, 800
    sx = W / max(1e-6, (maxx - minx))
    sz = H / max(1e-6, (maxz - minz))
    sc = min(sx, sz)

    def X(x):
        return (x - minx) * sc
    def Z(z):
        return (z - minz) * sc

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    parts.append('<rect width="100%" height="100%" fill="#1a1d22"/>')
    # river
    rpts = ' '.join(f'{X(x):.1f},{Z(z):.1f}' for x, z in river)
    parts.append(f'<polyline points="{rpts}" stroke="#4a7fb5" stroke-width="{river_w*sc:.1f}" fill="none" stroke-linecap="round" opacity="0.9"/>')
    # walls
    wpts = ' '.join(f'{X(x):.1f},{Z(z):.1f}' for x, z in wall + [wall[0]])
    parts.append(f'<polygon points="{wpts}" fill="#2a2e35" stroke="#8a8f98" stroke-width="2"/>')
    # roads
    for line in roads:
        pts = ' '.join(f'{X(x):.1f},{Z(z):.1f}' for x, z in line)
        parts.append(f'<polyline points="{pts}" stroke="#5a5148" stroke-width="2.5" fill="none" opacity="0.9"/>')
    # citadel
    cc = meta['citadel']
    parts.append(f'<circle cx="{X(cc["x"]):.1f}" cy="{Z(cc["z"]):.1f}" r="{cc["r"]*sc:.1f}" fill="#3a3f47" stroke="#9aa0ab" stroke-width="1.5"/>')
    # buildings
    for p in placed:
        c = obb_corners(p['x'], p['z'], p['spec']['w'], p['spec']['d'], p['rot'])
        pts = ' '.join(f'{X(x):.1f},{Z(z):.1f}' for x, z in c + [c[0]])
        parts.append(f'<polygon points="{pts}" fill="{colors.get(p["spec"]["name"],"#ccc")}" stroke="#222" stroke-width="1" opacity="0.95"/>')
    parts.append('</svg>')
    with open(os.path.join(outdir, 'preview.svg'), 'w') as f:
        f.write('\n'.join(parts))


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description='Watabou-like small town -> CSV for Houdini/PCG')
    ap.add_argument('--seed', type=int, required=True, help='random seed (e.g. 1555148727)')
    ap.add_argument('--warp', type=float, default=0.35, help='0..1 mesh distortion (Watabou warp equivalent)')
    ap.add_argument('--size', type=float, default=None, help='Watabou-ish size param (default 17). radius = size*6.5m')
    ap.add_argument('--radius', type=float, default=110.0, help='city radius in meters (overridden by --size)')
    ap.add_argument('--buildings', default=os.path.join(os.path.dirname(__file__), 'buildings.json'))
    ap.add_argument('--outdir', required=True)
    ap.add_argument('--no-preview', action='store_true')
    a = ap.parse_args()

    if a.size is not None:
        R = float(a.size) * 6.5
    else:
        R = float(a.radius)
    warp = max(0.0, min(1.0, a.warp))

    with open(a.buildings) as f:
        lib = json.load(f)

    placed, wall, roads, river, river_w, gates, meta = generate(a.seed, warp, R, lib)
    bp = write_csvs(a.outdir, placed, wall, roads, river, gates, meta, a.seed, warp, lib, R)
    if not a.no_preview:
        write_preview(a.outdir, placed, wall, roads, river, river_w, meta, lib)
    print(f'seed={a.seed} warp={warp} R={R:.1f}m -> {len(placed)} buildings')
    print(f'  counts: {meta["counts"]}')
    print(f'  wrote: {bp} (+ walls/roads/river.csv, preview.svg, manifest.json)')


if __name__ == '__main__':
    main()
