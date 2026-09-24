#!/usr/bin/env python3
"""
Small-town city generator -> CSV points for Houdini / Unreal PCG.

Model (follows Parish & Muller 2001 / CityGen / Watabou block-centric design):
  1. Road skeleton grown from the market square (radial arterials + ring
     streets; local-constraints style snapping at crossings).
  2. Roads partition the town into BLOCKS (quads between consecutive
     radials x rings, split by alleys while edges are long).
  3. Each block is subdivided into LOTS along its street frontages.
  4. One building footprint is inscribed per lot -> perimeter blocks
     with empty courtyards, exactly like the original generator.
  5. Everything lives on a shared WARP MESH (lattice of nodes). The warp
     parameter distorts the mesh; brush tools move mesh nodes; all layers
     (walls, roads, river, blocks, buildings) are evaluated through it.

Usage:
    python3 city_gen.py --seed 1555148727 --warp 0.35 --size 17 --outdir ./out_town

Only stdlib. 1 unit = 1 meter, Y-up. CSV gives pos_x, pos_z, rot_y_deg.
"""
import argparse
import csv
import json
import math
import os
import random

# ---------------------------------------------------------------- utils

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


# ---------------------------------------------------------------- warp mesh
# Shared lattice: every layer is evaluated through it, exactly like the
# original's mesh (districts are its cells, roads/rivers run along edges,
# warp brushes move its nodes).

class WarpMesh:
    def __init__(self, x0, x1, z0, z1, spacing, rng, warp, R, phases):
        self.s = spacing
        self.nx = max(2, int(math.ceil((x1 - x0) / spacing)) + 1)
        self.nz = max(2, int(math.ceil((z1 - z0) / spacing)) + 1)
        self.x0, self.z0 = x0, z0
        self.base = []  # [bx, bz]
        self.off = []   # [ox, oz] (seeded warp + brush edits)
        amp = warp * R * 0.20
        f1, f2 = phases['f1'], phases['f2']
        for j in range(self.nz):
            for i in range(self.nx):
                bx = x0 + i * spacing + (rng.uniform(-0.3, 0.3) * spacing if 0 < i < self.nx - 1 else 0)
                bz = z0 + j * spacing + (rng.uniform(-0.3, 0.3) * spacing if 0 < j < self.nz - 1 else 0)
                self.base.append([bx, bz])
                if warp <= 0:
                    self.off.append([0.0, 0.0])
                else:
                    ox = amp * (0.6 * math.sin(bx * f1 + phases['p1']) * math.cos(bz * f1 + phases['p2'])
                                + 0.4 * math.sin((bx + bz) * f2 + phases['p3']))
                    oz = amp * (0.6 * math.cos(bx * f1 + phases['p4']) * math.sin(bz * f1 + phases['p1'])
                                + 0.4 * math.cos((bx - bz) * f2 + phases['p5']))
                    self.off.append([ox, oz])
        self.rad = spacing * 2.2

    def disp(self, x, z):
        dx = dz = wsum = 0.0
        R2 = self.rad
        # brute force is fine at our sizes; restrict roughly by grid step
        for (bx, bz), (ox, oz) in zip(self.base, self.off):
            if ox == 0 and oz == 0:
                continue
            d = math.hypot(x - bx, z - bz)
            if d >= R2:
                continue
            w = 0.5 * (1 + math.cos(math.pi * d / R2))
            dx += ox * w
            dz += oz * w
            wsum += w
        if wsum == 0:
            return 0.0, 0.0
        return dx / wsum, dz / wsum

    def eval(self, x, z):
        dx, dz = self.disp(x, z)
        return (x + dx, z + dz)

    def eval_poly(self, poly):
        return [self.eval(x, z) for x, z in poly]

    def rot_delta(self, x, z):
        """Local rotation induced by the mesh (degrees)."""
        x1, z1 = self.eval(x + 1.0, z)
        x0, z0 = self.eval(x - 1.0, z)
        return math.degrees(math.atan2(z1 - z0, x1 - x0))

    # -- brush tools (original toolset), operate on NODES --
    def brush(self, tool, cx, cz, radius, dx=0, dz=0, strength=1.0):
        for k, ((bx, bz), o) in enumerate(zip(self.base, self.off)):
            d = math.hypot(bx - cx, bz - cz)
            if d >= radius:
                continue
            t = 1 - d / radius
            if tool in ('displace', 'bloat'):
                f = 0.5 * (1 + math.cos(math.pi * d / radius))
            else:  # liquify / rotate / relax: smoother falloff
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
                nb = (cx + vx * c - vz * s, cz + vx * s + vz * c)
                o[0] += nb[0] - bx
                o[1] += nb[1] - bz
            elif tool == 'relax':
                o[0] += (0 - o[0]) * f * 0.25 * strength
                o[1] += (0 - o[1]) * f * 0.25 * strength


# ---------------------------------------------------------------- generation (base space, unwarped)

ROAD_HALF = 2.5
SETBACK = 1.0
ALLEY_HALF = 1.5


def catmull_rom(pts, sub=6):
    """Smooth a control polyline like the original's curved streets."""
    if len(pts) < 3:
        return list(pts)
    out = [pts[0]]
    n = len(pts)
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
        for s in range(1, sub + 1):
            t = s / sub
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            z = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, z))
    return out


def generate(seed, warp, R, lib, count=55):
    rng = random.Random(seed)
    phases = {
        'f1': 0.018 + rng.random() * 0.012,
        'f2': 0.045 + rng.random() * 0.020,
        'p1': rng.random() * 6.283, 'p2': rng.random() * 6.283,
        'p3': rng.random() * 6.283, 'p4': rng.random() * 6.283,
        'p5': rng.random() * 6.283,
    }

    N_WALL = 48
    wob1, wob2 = rng.random() * 6.28, rng.random() * 6.28
    wall = []
    for i in range(N_WALL):
        a = 2 * math.pi * i / N_WALL
        rr = R * (1.0 + 0.05 * math.sin(3 * a + wob1) + 0.03 * math.sin(5 * a + wob2))
        wall.append((math.cos(a) * rr, math.sin(a) * rr))

    river_ang = rng.random() * math.pi
    river_w = 9.0 + rng.random() * 3.0
    rdx, rdz = math.cos(river_ang), math.sin(river_ang)
    rnx, rnz = -rdz, rdx
    meander_ph = rng.random() * 6.28
    meander_amp = (0.10 + 0.22 * warp) * R
    river = []
    for t in range(-14, 15):
        s = t * R / 12.0
        off = meander_amp * math.sin(s * 0.02 + meander_ph)
        river.append((rdx * s + rnx * off, rdz * s + rnz * off))

    def river_dist(x, z):
        return dist_to_polylines(x, z, [river])[0]

    # preset citadel=0: castle ward against the wall instead of a hill fort
    castle_spec = next(t for t in lib['types'] if t['name'] == 'castle')
    cast_ang = rng.random() * 2 * math.pi
    cast_c = (math.cos(cast_ang) * (R - castle_spec['d'] / 2 - 7.0),
              math.sin(cast_ang) * (R - castle_spec['d'] / 2 - 7.0))
    if river_dist(*cast_c) < river_w / 2 + castle_spec['d'] / 2 + 4:
        cast_c = (-cast_c[0], -cast_c[1])
    cast_R = max(castle_spec['w'], castle_spec['d']) / 2 + 3.0
    cit_c = cast_c          # exclusion center (shared checks below)
    cit_R = cast_R

    market_c = (0.0, 0.0)
    if river_dist(0, 0) < river_w / 2 + 14:
        for k in range(1, 8):
            for sgn in (1, -1):
                cand = (rnx * sgn * k * 8.0, rnz * sgn * k * 8.0)
                if river_dist(*cand) > river_w / 2 + 14:
                    market_c = cand
                    break
            else:
                continue
            break

    # -- road skeleton: a few radial arterials + two ring streets,
    #   drawn as smooth curves (catmull-rom) like the original's streets
    n_radial = 7
    base_ang = rng.random() * 2 * math.pi
    radial_ang = [base_ang + 2 * math.pi * i / n_radial + rng.uniform(-0.12, 0.12)
                  for i in range(n_radial)]

    def radial_pt(a, frac, wiggle):
        ex, ez = math.cos(a) * R * 1.02, math.sin(a) * R * 1.02
        bx = market_c[0] + (ex - market_c[0]) * frac
        bz = market_c[1] + (ez - market_c[1]) * frac
        wig = math.sin(frac * 5.0 + a * 3.0) * (1.5 + warp * 3.0) * math.sin(frac * math.pi)
        return (bx - math.sin(a) * wig * wiggle, bz + math.cos(a) * wig * wiggle)

    radials_ctrl = []
    for a in radial_ang:
        radials_ctrl.append([radial_pt(a, s / 7, 1.0) for s in range(8)])

    ring_fracs = [0.42, 0.8]
    rings_ctrl = []
    for frac in ring_fracs:
        pts = []
        for i in range(N_WALL):
            a = 2 * math.pi * i / N_WALL
            pts.append((market_c[0] + math.cos(a) * R * frac,
                        market_c[1] + math.sin(a) * R * frac))
        pts.append(pts[0])
        rings_ctrl.append(pts)

    radials = [catmull_rom(r, 6) for r in radials_ctrl]
    rings = [catmull_rom(r, 2) for r in rings_ctrl]
    all_roads = radials + rings
    all_roads_ctrl = radials_ctrl + rings_ctrl

    # -- warp mesh over the base layout
    xs = [p[0] for p in wall] + [r[0] for r in river]
    zs = [p[1] for p in wall] + [r[1] for r in river]
    mesh = WarpMesh(min(xs) - 12, max(xs) + 12, min(zs) - 12, max(zs) + 12,
                    R / 4.5, rng, warp, R, phases)

    # -- blocks: quads between consecutive radials x rings (CityGen faces).
    #   ring boundary 3 = wall: nearest wall vertex per radial angle.
    def wall_at(a):
        return min(wall, key=lambda p: abs((math.atan2(p[1], p[0]) - a + math.pi * 3) % (math.pi * 2) - math.pi))

    ring_pts = []  # ring_pts[j][i]
    for frac in ring_fracs:
        ring_pts.append([radial_pt(a, frac, 1.0) for a in radial_ang])
    ring_pts.append([wall_at(a) for a in radial_ang])

    alleys = []  # split lines (drawn + kept clear)

    def split_quad(q, depth=0):
        """Recursively split long quads with alleys (watabou wards)."""
        edges = [(q[k], q[(k + 1) % 4]) for k in range(4)]
        lens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in edges]
        if max(lens) < 50 or depth > 3:
            return [q]
        e = lens.index(max(lens))
        e2 = (e + 2) % 4  # opposite edge
        t = 0.5 + rng.uniform(-0.12, 0.12)
        a1, b1 = edges[e]
        a2, b2 = edges[e2]
        m1 = (a1[0] + (b1[0] - a1[0]) * t, a1[1] + (b1[1] - a1[1]) * t)
        m2 = (a2[0] + (b2[0] - a2[0]) * t, a2[1] + (b2[1] - a2[1]) * t)
        alleys.append([m1, m2])
        # order: q = [p0,p1,p2,p3]; children share split line
        pts = [q[0], q[1], q[2], q[3]]
        chain1, k = [m1], (e + 1) % 4
        while k != (e2 + 1) % 4:
            chain1.append(pts[k])
            k = (k + 1) % 4
        chain1.append(m2)
        chain2, k = [m2], (e2 + 1) % 4
        while k != (e + 1) % 4:
            chain2.append(pts[k])
            k = (k + 1) % 4
        chain2.append(m1)
        out = []
        for ch in (chain1, chain2):
            # reduce pentagon-ish chains to quad via centroid corners
            if len(ch) == 4:
                out += split_quad(ch, depth + 1)
            else:
                cx = sum(p[0] for p in ch) / len(ch)
                cz = sum(p[1] for p in ch) / len(ch)
                # fan into quads around centroid
                for n in range(len(ch)):
                    fan = [ch[n], ch[(n + 1) % len(ch)], (cx, cz)]
                    out.append(fan)
        return out

    blocks = []
    for i in range(n_radial):
        i2 = (i + 1) % n_radial
        for j in range(len(ring_pts) - 1):
            q = [ring_pts[j][i], ring_pts[j][i2], ring_pts[j + 1][i2], ring_pts[j + 1][i]]
            # skip blocks drowned by the river / citadel
            cx = sum(p[0] for p in q) / 4
            cz = sum(p[1] for p in q) / 4
            if river_dist(cx, cz) < river_w / 2 + 8:
                continue
            if math.hypot(cx - cit_c[0], cz - cit_c[1]) < cit_R + 6:
                continue
            blocks += split_quad(q)

    # -- lots: subdivide each block along street frontages; inscribe one
    #   footprint per lot -> perimeter blocks with free courtyards.
    # Dense fill: every lot gets one of the 5 house types (weighted, only
    # types that fit); market + castle placed once. No floating buildings.
    gap = lib.get('gap_m', 0.8)
    house_types = [t for t in lib['types'] if t['weight'] > 0]
    placed = []
    placed_obbs = []
    lots = []
    # plaza: open square around the market (kept clear, drawn as paper)
    plaza_r = market_spec_w = next(t['w'] for t in lib['types'] if t['name'] == 'market')
    plaza_r = plaza_r / 2 + 9.0

    def clear_of_water_hill(qx, qz, near_wall_ok=False):
        if not point_in_poly(qx, qz, wall):
            return False
        if river_dist(qx, qz) < river_w / 2 + 1.0:
            return False
        if math.hypot(qx - cit_c[0], qz - cit_c[1]) < cit_R:
            return False
        if math.hypot(qx - market_c[0], qz - market_c[1]) < plaza_r:
            return False
        if not near_wall_ok:
            # keep buildings off the curtain wall
            dmin = 1e9
            for i in range(len(wall)):
                ax, az = wall[i]
                bx, bz = wall[(i + 1) % len(wall)]
                dd, _, _, _ = dist_pt_seg(qx, qz, ax, az, bx, bz)
                dmin = min(dmin, dd)
            if dmin < 2.5:
                return False
        return True

    def try_place(cx, cz, rot, spec, district, near_wall_ok=False):
        w, d = spec['w'], spec['d']
        if not clear_of_water_hill(cx, cz, near_wall_ok):
            return False
        for (qx, qz) in obb_corners(cx, cz, w, d, rot):
            if not clear_of_water_hill(qx, qz, True):
                return False
        # no alley-distance check: lots already share the original 0.6 m
        # alley gaps, and OBB overlap below keeps footprints apart
        corners = obb_corners(cx, cz, w + gap, d + gap, rot)
        for oc in placed_obbs:
            if obb_overlap(corners, oc):
                return False
        placed.append({'x': cx, 'z': cz, 'rot': rot, 'spec': spec, 'district': district})
        placed_obbs.append(corners)
        return True

    # -- lots via the original Ward.createAlleys: inset the block from the
    #   streets, then recursively bisect the longest edge (alley gap 0.6).
    #   Leaf polygons are lots; one library footprint is inscribed per lot.
    #   Lot target ~ house scale -> packed chunks like the original.
    GRID_CHAOS = 0.7
    EMPTY_PROB = 0.03

    def poly_area(poly):
        return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                       - poly[(i + 1) % len(poly)][0] * poly[i][1]
                       for i in range(len(poly)))) / 2.0

    def shrink_poly(poly, d):
        n = len(poly)
        cx = sum(p[0] for p in poly) / n
        cz = sum(p[1] for p in poly) / n
        out = []
        for i in range(n):
            P, Q = poly[i], poly[(i + 1) % n]
            ex, ez = Q[0] - P[0], Q[1] - P[1]
            el = math.hypot(ex, ez) or 1.0
            # inward normal (toward centroid)
            nx, nz = -ez / el, ex / el
            mx, mz = (P[0] + Q[0]) / 2, (P[1] + Q[1]) / 2
            if (cx - mx) * nx + (cz - mz) * nz < 0:
                nx, nz = -nx, -nz
            out.append((P, Q, nx, nz))
        # intersect adjacent offset lines
        res = []
        for i in range(n):
            P1, Q1, nx1, nz1 = out[i]
            P2, Q2, nx2, nz2 = out[(i + 1) % n]
            # line1: P1+d*n1 -> dir e1 ; line2: P2+d*n2 -> dir e2
            e1x, e1z = Q1[0] - P1[0], Q1[1] - P1[1]
            e2x, e2z = Q2[0] - P2[0], Q2[1] - P2[1]
            a1x, a1z = P1[0] + nx1 * d, P1[1] + nz1 * d
            a2x, a2z = P2[0] + nx2 * d, P2[1] + nz2 * d
            den = e1x * e2z - e1z * e2x
            if abs(den) < 1e-9:
                res.append((a1x + e1x * 0.5, a1z + e1z * 0.5))
                continue
            t = ((a2x - a1x) * e2z - (a2z - a1z) * e2x) / den
            res.append((a1x + e1x * t, a1z + e1z * t))
        return res

    def split_poly(poly, t, jitter_ang, gap):
        """Bisect convex polygon across its longest edge (Cutter.bisect)."""
        n = len(poly)
        best, blen = 0, -1.0
        for i in range(n):
            l = math.hypot(poly[(i + 1) % n][0] - poly[i][0],
                           poly[(i + 1) % n][1] - poly[i][1])
            if l > blen:
                blen, best = l, i
        P0, P1 = poly[best], poly[(best + 1) % n]
        A = (P0[0] + (P1[0] - P0[0]) * t, P0[1] + (P1[1] - P0[1]) * t)
        # cut toward the centroid: always enters the interior (convex)
        ccx = sum(p[0] for p in poly) / n - A[0]
        ccz = sum(p[1] for p in poly) / n - A[1]
        cl = math.hypot(ccx, ccz) or 1.0
        ca, sa = math.cos(jitter_ang), math.sin(jitter_ang)
        dx, dz = (ccx / cl) * ca - (ccz / cl) * sa, (ccx / cl) * sa + (ccz / cl) * ca
        # opposite edge: farthest midpoint along the cut direction
        # (Cutter.bisect semantics — always yields two solid halves)
        scored = []
        for i in range(n):
            if i == best:
                continue
            Q0, Q1 = poly[i], poly[(i + 1) % n]
            mx, mz = (Q0[0] + Q1[0]) / 2 - A[0], (Q0[1] + Q1[1]) / 2 - A[1]
            scored.append((mx * dx + mz * dz, i))
        scored.sort(reverse=True)
        B = e1 = None
        for _, i in scored:
            Q0, Q1 = poly[i], poly[(i + 1) % n]
            ex2, ez2 = Q1[0] - Q0[0], Q1[1] - Q0[1]
            den = dx * ez2 - dz * ex2
            if abs(den) < 1e-12:
                continue
            v = ((Q0[0] - A[0]) * dz - (Q0[1] - A[1]) * dx) / den
            if 0.02 <= v <= 0.98:
                B = (Q0[0] + ex2 * v, Q0[1] + ez2 * v)
                e1 = i
                break
        if B is None:
            return None
        # child 1: A -> walk forward -> B
        ch1 = [A]
        k = best
        while True:
            ch1.append(poly[(k + 1) % n])
            if k == e1:
                break
            k = (k + 1) % n
        ch1.append(B)
        # child 2: B -> walk forward -> A
        ch2 = [B]
        k = e1
        while True:
            ch2.append(poly[(k + 1) % n])
            if k == best:
                break
            k = (k + 1) % n
        ch2.append(A)
        # alley gap: push cut points apart along cut dir
        g = gap / 2.0
        A1, A2 = (A[0] + dx * g, A[1] + dz * g), (A[0] - dx * g, A[1] - dz * g)
        Ba, Bb = (B[0] + dx * g, B[1] + dz * g), (B[0] - dx * g, B[1] - dz * g)
        c1 = [A1] + ch1[1:-1] + [Ba]
        c2 = [Bb] + ch2[1:-1] + [A2]
        if min(poly_area(c1), poly_area(c2)) < poly_area(poly) * 0.15:
            return None  # sliver cut: keep as one lot
        alleys.append([A, B])
        return c1, c2

    def lot_bbox(lot):
        """Axis frame of the lot -> (w, h) in its longest-edge orientation."""
        n = len(lot)
        best, blen = 0, -1.0
        for i in range(n):
            l = math.hypot(lot[(i + 1) % n][0] - lot[i][0],
                           lot[(i + 1) % n][1] - lot[i][1])
            if l > blen:
                blen, best = l, i
        P0, P1 = lot[best], lot[(best + 1) % n]
        ang = math.atan2(P1[1] - P0[1], P1[0] - P0[0])
        c, s = math.cos(ang), math.sin(ang)
        xs = [p[0] * c + p[1] * s for p in lot]
        zs = [-p[0] * s + p[1] * c for p in lot]
        return max(xs) - min(xs), max(zs) - min(zs)

    def lot_fits(lot):
        """True if any library footprint fits inside the lot bbox."""
        bw, bh = lot_bbox(lot)
        for t in house_types:
            if (t['w'] <= bw - 0.4 and t['d'] <= bh - 0.4) or \
               (t['d'] <= bw - 0.4 and t['w'] <= bh - 0.4):
                return True
        return False

    MAX_LOT = 260.0   # recovery ceiling for unsplittable lots
    LOT_TARGET = 72.0  # snug single-building lots (original ward scale)

    def create_alleys(poly, depth=0):
        """Split down to house scale; every leaf can hold one of OUR
        footprints. Slivers become alley gaps; unsplittable parents that
        still fit something are kept whole (large ward buildings)."""
        a = poly_area(poly)
        if depth > 8 or a < 30.0:
            return []  # alley sliver
        fits = lot_fits(poly)
        if fits and a <= LOT_TARGET:
            if rng.random() >= EMPTY_PROB:
                return [poly]
            return []  # 3% vacant lot -> courtyard
        spread = 0.8 * GRID_CHAOS
        res = None
        for _ in range(5):  # retry grazed cuts with a fresh ratio/angle
            ratio = (1 - spread) / 2 + rng.random() * spread
            # angle jitter only on big lots: small cuts stay perpendicular
            # so lots stay rectangular and pack tight
            angle_spread = math.pi / 6 * GRID_CHAOS * (1.0 if a > 400.0 else 0.0)
            b = (rng.random() - 0.5) * angle_spread
            res = split_poly(poly, ratio, b, 0.6)
            if res is not None:
                break
        if res is None:
            return [poly] if (fits and a <= MAX_LOT) else []
        out = []
        for half in res:
            out += create_alleys(half, depth + 1)
        if not out and fits and a <= MAX_LOT:
            return [poly]  # children were all slivers: keep the parent
        return out

    def lot_frame(lot):
        """Longest-edge frame -> (w, h, angle, P0, dir, inward normal)."""
        n = len(lot)
        best, blen = 0, -1.0
        for i in range(n):
            l = math.hypot(lot[(i + 1) % n][0] - lot[i][0],
                           lot[(i + 1) % n][1] - lot[i][1])
            if l > blen:
                blen, best = l, i
        P0, P1 = lot[best], lot[(best + 1) % n]
        ang = math.degrees(math.atan2(P1[1] - P0[1], P1[0] - P0[0]))
        r = math.radians(ang)
        c, s = math.cos(r), math.sin(r)
        xs = [p[0] * c + p[1] * s for p in lot]
        zs = [-p[0] * s + p[1] * c for p in lot]
        lw = max(xs) - min(xs)
        lh = max(zs) - min(zs)
        ex, ez = (P1[0] - P0[0]) / blen, (P1[1] - P0[1]) / blen
        nx, nz = -ez, ex
        # inward normal: toward lot centroid
        ccx = sum(p[0] for p in lot) / n
        ccz = sum(p[1] for p in lot) / n
        if (ccx - P0[0]) * nx + (ccz - P0[1]) * nz < 0:
            nx, nz = -nx, -nz
        return lw, lh, ang, P0, (ex, ez), (nx, nz)

    def district_pool(r_norm):
        pool = list(house_types)
        if r_norm > 0.7:
            pool = [t for t in pool if t['w'] * t['d'] <= 100] or pool
        if r_norm < 0.35:
            pool = [t for t in pool if t['w'] * t['d'] >= 48] or pool
        return pool

    def pack_bands(blk_inner, r_norm, district, b, budget):
        """Watabou ward fill: strips perpendicular to the street. Each band
        is a row of like houses along the street frontage, exact footprints,
        0.9 m side gaps, 1.6 m alley between bands. Stops at the remaining
        building budget (quantity control)."""
        lw, lh, ang, P0, (ex, ez), (nx, nz) = lot_frame(blk_inner)
        pool = district_pool(r_norm)
        rot = ang  # footprint w along the street, d inward
        placed_n = 0
        y = 0.8
        while placed_n < budget:
            room_y = lh - 0.8 - y
            cands = [t for t in pool if t['d'] <= room_y - 0.3]
            if not cands:
                break
            # band type: weighted; occasionally swap mid-band for variety
            spec = rng.choices(cands, weights=[t['weight'] for t in cands], k=1)[0]
            x = 0.8 + rng.uniform(0, 1.2)
            while x + spec['w'] <= lw - 0.8 and placed_n < budget:
                if rng.random() < 0.18:  # occasional in-row swap
                    alt = [t for t in cands
                           if t['w'] <= lw - 0.8 - x + spec['w'] - 0.3
                           and t['d'] <= room_y - 0.3]
                    if alt:
                        spec = rng.choices(alt, weights=[t['weight'] for t in alt], k=1)[0]
                if rng.random() >= EMPTY_PROB:  # few vacant lots
                    qx = P0[0] + ex * (x + spec['w'] / 2) + nx * (y + spec['d'] / 2)
                    qz = P0[1] + ez * (x + spec['w'] / 2) + nz * (y + spec['d'] / 2)
                    if try_place(qx, qz, rot, spec, district):
                        lots.append({'x': qx, 'z': qz, 'rot': rot,
                                     'frontage': spec['w'], 'block': b})
                        placed_n += 1
                x += spec['w'] + 0.9
            y += spec['d'] + 1.6
        return placed_n

    # temple: one large hall facing the plaza (the preset's temple=1),
    # mapped to the biggest common footprint so the CSV stays 7 types.
    # Placed before the wards so it wins its plot; counts toward the total.
    temple_spec = next((t for t in house_types
                        if t['name'] == 'house_D_hall'), None)
    temple_done = False
    if temple_spec is not None:
        for _ in range(600):
            a = rng.random() * 2 * math.pi
            rr = plaza_r + temple_spec['d'] / 2 + 1.5 + rng.uniform(0, 8)
            qx = market_c[0] + math.cos(a) * rr
            qz = market_c[1] + math.sin(a) * rr
            face = math.degrees(math.atan2(market_c[1] - qz, market_c[0] - qx))
            if try_place(qx, qz, face + 90, temple_spec, 'temple'):
                temple_done = True
                break

    # quantity control: houses = target - reserved landmarks
    budget = max(0, count - 2 - (1 if temple_done else 0))
    # fill wards from the center outward until the budget is spent
    block_order = sorted(
        range(len(blocks)),
        key=lambda b: math.hypot(
            sum(p[0] for p in blocks[b]) / len(blocks[b]) - market_c[0],
            sum(p[1] for p in blocks[b]) / len(blocks[b]) - market_c[1]))
    for b in block_order:
        if budget <= 0:
            break
        blk = blocks[b]
        if len(blk) < 3:
            continue
        cx = sum(p[0] for p in blk) / len(blk)
        cz = sum(p[1] for p in blk) / len(blk)
        r_norm = math.hypot(cx - market_c[0], cz - market_c[1]) / R
        district = 'center' if r_norm < 0.35 else ('mid' if r_norm < 0.7 else 'edge')
        inner = shrink_poly(blk, 1.5)  # inset from street centerlines (getCityBlock)
        if len(inner) < 3 or poly_area(inner) < 10:
            continue
        budget -= pack_bands(inner, r_norm, district, b, budget)

    # gates: where each radial arterial crosses the wall (drawn as thick
    # ticks along the road, like the original's gate marks)
    gates = []
    for a in radial_ang:
        ex, ez = math.cos(a), math.sin(a)
        prev_in = True
        for step in range(30, 130):
            t = step / 100.0 * R
            px, pz = market_c[0] + ex * t, market_c[1] + ez * t
            inside = point_in_poly(px, pz, wall)
            if prev_in and not inside:
                gates.append(((px, pz), (ex, ez)))
                break
            prev_in = inside

    # castle: wall-side keep (preset citadel=0), facing the town center
    ang_to_center = math.degrees(math.atan2(market_c[1] - cast_c[1],
                                            market_c[0] - cast_c[0]))
    placed.append({'x': cast_c[0], 'z': cast_c[1], 'rot': -ang_to_center,
                   'spec': castle_spec, 'district': 'castle'})
    market_spec = next(t for t in lib['types'] if t['name'] == 'market')
    placed.append({'x': market_c[0], 'z': market_c[1], 'rot': rng.uniform(0, 90),
                   'spec': market_spec, 'district': 'market'})

    # -- evaluate everything through the shared mesh (base -> final)
    wall_f = mesh.eval_poly(wall)
    roads_f = [mesh.eval_poly(l) for l in all_roads]
    alleys_f = [mesh.eval_poly(l) for l in alleys]
    river_f = mesh.eval_poly(river)
    blocks_f = [mesh.eval_poly(b) for b in blocks]
    final = []
    for p in placed:
        fx, fz = mesh.eval(p['x'], p['z'])
        final.append({'x': fx, 'z': fz,
                      'rot': (p['rot'] + mesh.rot_delta(p['x'], p['z'])) % 360,
                      'spec': p['spec'], 'district': p['district']})
    # post-warp cull: strong mesh distortion can intersect neighbors —
    # drop the smaller building of any overlapping pair (never market/castle)
    keep = []
    obbs = [obb_corners(p['x'], p['z'], p['spec']['w'], p['spec']['d'], p['rot'])
            for p in final]
    dropped = set()
    for i in range(len(final)):
        if i in dropped:
            continue
        for j in range(i + 1, len(final)):
            if j in dropped:
                continue
            if obb_overlap(obbs[i], obbs[j]):
                pi, pj = final[i], final[j]
                sacred = ('market', 'castle')
                if pi['spec']['name'] in sacred and pj['spec']['name'] not in sacred:
                    dropped.add(j)
                elif pj['spec']['name'] in sacred and pi['spec']['name'] not in sacred:
                    dropped.add(i)
                    break
                elif pi['spec']['w'] * pi['spec']['d'] <= pj['spec']['w'] * pj['spec']['d']:
                    dropped.add(i)
                    break
                else:
                    dropped.add(j)
    keep = [p for k, p in enumerate(final) if k not in dropped]
    final = keep
    final.sort(key=lambda p: (p['spec']['name'], p['x'], p['z']))

    meta = {
        'seed': seed, 'warp': warp, 'R': R,
        'citadel': {'x': cit_c[0], 'z': cit_c[1], 'r': cit_R},
        'market': {'x': market_c[0], 'z': market_c[1]},
        'plaza_r': plaza_r,
        'river_w': river_w,
        'mesh_spacing': R / 4.5,
        'counts': {},
    }
    for p in final:
        meta['counts'][p['spec']['name']] = meta['counts'].get(p['spec']['name'], 0) + 1
    meta['blocks'] = len(blocks)
    meta['lots'] = len(lots)
    meta['gates'] = [[round(g[0][0], 3), round(g[0][1], 3),
                      round(g[1][0], 3), round(g[1][1], 3)] for g in gates]
    base = {'wall': wall, 'roads': all_roads, 'alleys': alleys, 'river': river,
            'blocks': blocks,
            'lots': [(l['x'], l['z']) for l in lots],
            'citadel': {'x': cit_c[0], 'z': cit_c[1], 'r': cit_R},
            'market': {'x': market_c[0], 'z': market_c[1], 'plaza_r': plaza_r},
            'river_w': river_w}
    return final, wall_f, roads_f, alleys_f, river_f, river_w, blocks_f, mesh, meta, base


# ---------------------------------------------------------------- export

def write_all(outdir, final, wall, roads, alleys, river, river_w, blocks, mesh, meta, base, seed, warp):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, 'buildings.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['id', 'pos_x', 'pos_y', 'pos_z', 'rot_y_deg', 'type',
                    'width_m', 'depth_m', 'height_m', 'scale', 'district', 'seed', 'warp'])
        for i, p in enumerate(final):
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
        for rid, line in enumerate(roads + alleys):
            for j, (x, z) in enumerate(line):
                w.writerow([rid, j, round(x, 3), round(z, 3)])
    with open(os.path.join(outdir, 'river.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['pt_idx', 'x', 'z', 'width_m'])
        for j, (x, z) in enumerate(river):
            w.writerow([j, round(x, 3), round(z, 3), round(river_w, 2)])
    with open(os.path.join(outdir, 'blocks.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['block_id', 'pt_idx', 'x', 'z'])
        for bid, b in enumerate(blocks):
            for j, (x, z) in enumerate(b):
                w.writerow([bid, j, round(x, 3), round(z, 3)])
    with open(os.path.join(outdir, 'mesh.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['node', 'base_x', 'base_z', 'off_x', 'off_z'])
        for k, ((bx, bz), (ox, oz)) in enumerate(zip(mesh.base, mesh.off)):
            w.writerow([k, round(bx, 3), round(bz, 3), round(ox, 3), round(oz, 3)])
    with open(os.path.join(outdir, 'manifest.json'), 'w') as f:
        json.dump(meta, f, indent=2)
    # town.json: base + mesh + final -> the browser lab runs the SAME system
    with open(os.path.join(outdir, 'town.json'), 'w') as f:
        json.dump({
            'seed': seed, 'warp': warp, 'R': meta['R'], 'meta': meta,
            'mesh': {'x0': mesh.x0, 'z0': mesh.z0, 'nx': mesh.nx, 'nz': mesh.nz,
                     'spacing': mesh.s,
                     'base': [[round(x, 3), round(z, 3)] for x, z in mesh.base],
                     'off': [[round(x, 3), round(z, 3)] for x, z in mesh.off]},
            'base': {k: ([[[round(x, 3), round(z, 3)] for x, z in l] for l in v]
                         if k in ('roads', 'alleys', 'blocks') else v)
                     for k, v in base.items()
                     if k in ('roads', 'alleys', 'blocks')},
            'buildings': [
                {'x': round(p['x'], 3), 'z': round(p['z'], 3),
                 'rot': round(p['rot'] % 360, 2), 'type': p['spec']['name'],
                 'w': p['spec']['w'], 'd': p['spec']['d'],
                 'h': p['spec'].get('h', 5.0), 'district': p['district']}
                for p in final
            ],
        }, f)
    return os.path.join(outdir, 'buildings.csv')


def write_preview(outdir, final, wall, roads, alleys, river, river_w, blocks, meta, lib):
    # original DEFAULT palette: paper 0xccc5b8, light 0x99948a,
    # medium 0x67635c, dark 0x1a1917
    PAPER, LIGHT, MEDIUM, DARK = '#ccc5b8', '#99948a', '#67635c', '#1a1917'
    xs = [p[0] for p in wall] + [p['x'] for p in final]
    zs = [p[1] for p in wall] + [p['z'] for p in final]
    minx, maxx = min(xs) - 10, max(xs) + 10
    minz, maxz = min(zs) - 10, max(zs) + 10
    W, H = 800, 800
    sc = min(W / max(1e-6, (maxx - minx)), H / max(1e-6, (maxz - minz)))

    def X(x):
        return (x - minx) * sc
    def Z(z):
        return (z - minz) * sc

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    parts.append(f'<rect width="100%" height="100%" fill="{PAPER}"/>')
    # river: steel-blue band with darker edge (water on parchment)
    WATER, WATER_D = '#a9c1cf', '#7d99a8'
    rpts = ' '.join(f'{X(x):.1f},{Z(z):.1f}' for x, z in river)
    parts.append(f'<polyline points="{rpts}" stroke="{WATER_D}" stroke-width="{river_w*sc+2:.1f}" fill="none" stroke-linecap="round"/>')
    parts.append(f'<polyline points="{rpts}" stroke="{WATER}" stroke-width="{river_w*sc:.1f}" fill="none" stroke-linecap="round"/>')
    # plaza: open paper square around the market
    mc = meta['market']
    parts.append(f'<circle cx="{X(mc["x"]):.1f}" cy="{Z(mc["z"]):.1f}" r="{meta["plaza_r"]*sc:.1f}" fill="{PAPER}"/>')
    # walls: dark thick + towers + gate ticks
    wpts = ' '.join(f'{X(x):.1f},{Z(z):.1f}' for x, z in wall + [wall[0]])
    parts.append(f'<polygon points="{wpts}" fill="none" stroke="{DARK}" stroke-width="5"/>')
    for x, z in wall[::4]:
        parts.append(f'<circle cx="{X(x):.1f}" cy="{Z(z):.1f}" r="3.5" fill="{DARK}"/>')
    for (gx, gz), (ex, ez) in meta_gates(meta):
        parts.append(f'<line x1="{X(gx-ex*4):.1f}" y1="{Z(gz-ez*4):.1f}" '
                     f'x2="{X(gx+ex*4):.1f}" y2="{Z(gz+ez*4):.1f}" '
                     f'stroke="{DARK}" stroke-width="7"/>')
    # roads: medium casing + paper core; ward streets between blocks drawn
    # thin (the original's ward boundaries). Band alleys stay invisible —
    # they exist only as gaps between buildings.
    for line in roads:
        pts = ' '.join(f'{X(x):.1f},{Z(z):.1f}' for x, z in line)
        parts.append(f'<polyline points="{pts}" stroke="{MEDIUM}" stroke-width="3.2" fill="none" stroke-linecap="round"/>')
        parts.append(f'<polyline points="{pts}" stroke="{PAPER}" stroke-width="1.8" fill="none" stroke-linecap="round"/>')
    for line in alleys:
        pts = ' '.join(f'{X(x):.1f},{Z(z):.1f}' for x, z in line)
        parts.append(f'<polyline points="{pts}" stroke="{MEDIUM}" stroke-width="2.0" fill="none"/>')
        parts.append(f'<polyline points="{pts}" stroke="{PAPER}" stroke-width="0.9" fill="none"/>')
    for p in final:
        c = obb_corners(p['x'], p['z'], p['spec']['w'], p['spec']['d'], p['rot'])
        pts = ' '.join(f'{X(x):.1f},{Z(z):.1f}' for x, z in c + [c[0]])
        if p['spec']['name'] == 'castle':
            # castle keep: own curtain wall (offset rect) + heavy stroke
            wc = obb_corners(p['x'], p['z'], p['spec']['w'] + 6,
                             p['spec']['d'] + 6, p['rot'])
            wpts2 = ' '.join(f'{X(x):.1f},{Z(z):.1f}' for x, z in wc + [wc[0]])
            parts.append(f'<polygon points="{wpts2}" fill="none" stroke="{DARK}" stroke-width="3.5"/>')
            parts.append(f'<polygon points="{pts}" fill="{LIGHT}" stroke="{DARK}" stroke-width="2.2"/>')
        else:
            parts.append(f'<polygon points="{pts}" fill="{LIGHT}" stroke="{DARK}" stroke-width="0.8"/>')
    # compass rose (bottom-right), like the original's compass
    cxp, cyp, r = W - 46, H - 46, 26
    star = []
    for k in range(8):
        rr = r if k % 2 == 0 else r * 0.45
        a = -math.pi / 2 + k * math.pi / 4
        star.append(f'{cxp + rr * math.cos(a):.1f},{cyp + rr * math.sin(a):.1f}')
    parts.append(f'<polygon points="{" ".join(star)}" fill="{MEDIUM}" stroke="{DARK}" stroke-width="1"/>')
    parts.append(f'<text x="{cxp}" y="{cyp - r - 8}" text-anchor="middle" font-family="Georgia,serif" font-size="15" fill="{DARK}">N</text>')
    parts.append('</svg>')
    with open(os.path.join(outdir, 'preview.svg'), 'w') as f:
        f.write('\n'.join(parts))


def meta_gates(meta):
    for gx, gz, ex, ez in meta.get('gates', []):
        yield (gx, gz), (ex, ez)


def main():
    ap = argparse.ArgumentParser(description='Watabou-style city -> CSV for Houdini/PCG')
    ap.add_argument('--seed', type=int, default=None,
                    help='omit for a random town (seed is echoed in manifest)')
    ap.add_argument('--warp', type=float, default=0.35, help='0..1 mesh distortion')
    ap.add_argument('--count', type=int, default=55,
                    help='target number of buildings (town radius derives from it)')
    ap.add_argument('--size', type=float, default=None,
                    help='override radius = size*6.5m (default: derived from --count)')
    ap.add_argument('--radius', type=float, default=None)
    ap.add_argument('--buildings', default=os.path.join(os.path.dirname(__file__), 'buildings.json'))
    ap.add_argument('--outdir', required=True)
    ap.add_argument('--no-preview', action='store_true')
    a = ap.parse_args()

    seed = a.seed if a.seed is not None else random.randrange(2 ** 31)
    if a.size is not None:
        R = float(a.size) * 6.5
    elif a.radius is not None:
        R = float(a.radius)
    else:
        # radius from the building budget at measured band density
        R = max(50.0, min(220.0, 25.0 + 7.0 * math.sqrt(a.count)))
    warp = max(0.0, min(1.0, a.warp))
    with open(a.buildings) as f:
        lib = json.load(f)

    final, wall, roads, alleys, river, river_w, blocks, mesh, meta, base = \
        None, None, None, None, None, None, None, None, None, None
    for _ in range(4):
        # grow the radius until the wards can supply the target count
        final, wall, roads, alleys, river, river_w, blocks, mesh, meta, base = \
            generate(seed, warp, R, lib, count=a.count)
        if len(final) >= a.count * 0.92 or R >= 220.0:
            break
        R = min(220.0, R * 1.15)
    bp = write_all(outdir=a.outdir, final=final, wall=wall, roads=roads,
                   alleys=alleys, river=river, river_w=river_w, blocks=blocks,
                   mesh=mesh, meta=meta, base=base, seed=seed, warp=warp)
    if not a.no_preview:
        write_preview(a.outdir, final, wall, roads, alleys, river, river_w, blocks, meta, lib)
    print(f'seed={seed} warp={warp} R={R:.1f}m -> {len(final)} buildings, '
          f'{len(blocks)} blocks, {meta["lots"]} lots')
    print(f'  counts: {meta["counts"]}')
    print(f'  wrote: {bp} (+ walls/roads/river/blocks/mesh.csv, town.json, preview.svg)')


if __name__ == '__main__':
    main()
