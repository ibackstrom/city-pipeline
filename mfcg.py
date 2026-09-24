#!/usr/bin/env python3
"""
Faithful Python port of Oleg Dolya's Medieval Fantasy City Generator
(watabou/TownGeneratorOS, GPL-3.0). Ported from the Haxe sources:
Random, MathUtils, GeomUtils, ArrayExtender, Polygon, Voronoi, Graph,
Cutter, Topology, Patch, CurtainWall, Model and all wards.

The layout algorithm, constants and structure follow the original 1:1.
This file is licensed GPL-3.0 (see LICENSE-NOTE in README).
"""
import math
import random as _pyrandom

# ---------------------------------------------------------------- Random (LCG, as the original)

class Random:
    g = 48271.0
    n = 2147483647
    seed = 1

    @staticmethod
    def reset(seed=-1):
        Random.seed = seed if seed != -1 else int(_pyrandom.random() * Random.n) % Random.n

    @staticmethod
    def next():
        Random.seed = int((Random.seed * Random.g) % Random.n)
        return Random.seed

    @staticmethod
    def float():
        return Random.next() / Random.n

    @staticmethod
    def normal():
        return (Random.float() + Random.float() + Random.float()) / 3

    @staticmethod
    def int(min_, max_):
        return int(min_ + Random.next() / Random.n * (max_ - min_))

    @staticmethod
    def bool(chance=0.5):
        return Random.float() < chance

    @staticmethod
    def fuzzy(f=1.0):
        if f == 0:
            return 0.5
        return (1 - f) / 2 + f * Random.normal()


def sign(v):
    return 0 if v == 0 else (-1 if v < 0 else 1)


# ---------------------------------------------------------------- Point (mutable, identity-based)

class Pt:
    __slots__ = ('x', 'y')

    def __init__(self, x=0.0, y=0.0):
        self.x = x
        self.y = y

    def clone(self):
        return Pt(self.x, self.y)

    def set(self, p):
        self.x, self.y = p.x, p.y

    def add(self, p):
        return Pt(self.x + p.x, self.y + p.y)

    def addEq(self, p):
        self.x += p.x
        self.y += p.y
        return self

    def subtract(self, p):
        return Pt(self.x - p.x, self.y - p.y)

    def scale(self, k):
        return Pt(self.x * k, self.y * k)

    def scaleEq(self, k):
        self.x *= k
        self.y *= k
        return self

    def length(self):
        return math.hypot(self.x, self.y)

    def norm(self, k=1.0):
        l = self.length() or 1.0
        return Pt(self.x / l * k, self.y / l * k)

    def rotate90(self):
        return Pt(-self.y, self.x)

    def dot(self, p):
        return self.x * p.x + self.y * p.y

    def offset(self, dx, dy):
        self.x += dx
        self.y += dy


def distance(p1, p2):
    return math.hypot(p1.x - p2.x, p1.y - p2.y)


# ---------------------------------------------------------------- GeomUtils

def intersectLines(x1, y1, dx1, dy1, x2, y2, dx2, dy2):
    d = dx1 * dy2 - dy1 * dx2
    if d == 0:
        return None
    t2 = (dy1 * (x2 - x1) - dx1 * (y2 - y1)) / d
    if dx1 != 0:
        t1 = (x2 - x1 + dx2 * t2) / dx1
    else:
        t1 = (y2 - y1 + dy2 * t2) / dy1
    return Pt(t1, t2)


def interpolate(p1, p2, ratio=0.5):
    d = p2.subtract(p1)
    return Pt(p1.x + d.x * ratio, p1.y + d.y * ratio)


def sscalar(x1, y1, x2, y2):
    return x1 * x2 + y1 * y2


def cross(x1, y1, x2, y2):
    return x1 * y2 - y1 * x2


def distance2line(x1, y1, dx1, dy1, x0, y0):
    return (dx1 * y0 - dy1 * x0 + (y1 + dy1) * x1 - (x1 + dx1) * y1) / math.sqrt(dx1 * dx1 + dy1 * dy1)


# ---------------------------------------------------------------- Polygon helpers (poly = list[Pt])

DELTA = 0.000001


def p_square(poly):
    v1 = poly[-1]
    v2 = poly[0]
    s = v1.x * v2.y - v2.x * v1.y
    for i in range(1, len(poly)):
        v1, v2 = v2, poly[i]
        s += v1.x * v2.y - v2.x * v1.y
    return s * 0.5


def p_perimeter(poly):
    return sum(distance(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def p_compactness(poly):
    p = p_perimeter(poly)
    return 4 * math.pi * p_square(poly) / (p * p) if p else 0.0


def p_center(poly):
    c = Pt()
    for v in poly:
        c.addEq(v)
    c.scaleEq(1 / len(poly))
    return c


def p_centroid(poly):
    x = y = a = 0.0
    for i in range(len(poly)):
        v0, v1 = poly[i], poly[(i + 1) % len(poly)]
        f = cross(v0.x, v0.y, v1.x, v1.y)
        a += f
        x += (v0.x + v1.x) * f
        y += (v0.y + v1.y) * f
    s6 = 1 / (3 * a) if a else 0.0
    return Pt(s6 * x, s6 * y)


def in_poly(poly, v):
    for q in poly:
        if q is v:
            return True
    return False


def p_distance(poly, p):
    return min(distance(v, p) for v in poly)


def p_next(poly, v):
    return poly[(poly.index(v) + 1) % len(poly)]


def p_prev(poly, v):
    return poly[(poly.index(v) + len(poly) - 1) % len(poly)]


def p_findEdge(poly, a, b):
    try:
        index = poly.index(a)
    except ValueError:
        return -1
    return index if poly[(index + 1) % len(poly)] is b else -1


def p_vector(poly, v):
    return p_next(poly, v).subtract(v)


def p_vectori(poly, i):
    return poly[0 if i == len(poly) - 1 else i + 1].subtract(poly[i])


def p_isConvexVertexi(poly, i):
    n = len(poly)
    v0, v1, v2 = poly[(i + n - 1) % n], poly[i], poly[(i + 1) % n]
    return cross(v1.x - v0.x, v1.y - v0.y, v2.x - v1.x, v2.y - v1.y) > 0


def p_isConvex(poly):
    return all(p_isConvexVertexi(poly, i) for i in range(len(poly)))


def p_smoothVertex(poly, v, f=1.0):
    prev = p_prev(poly, v)
    nxt = p_next(poly, v)
    return Pt(prev.x + v.x * f + nxt.x, prev.y + v.y * f + nxt.y).scale(1 / (2 + f))


def p_smoothVertexEq(poly, f=1.0):
    n = len(poly)
    v1, v2 = poly[n - 1], poly[0]
    out = []
    for i in range(n):
        v0, v1, v2 = v1, v2, poly[(i + 1) % n]
        out.append(Pt((v0.x + v1.x * f + v2.x) / (2 + f),
                       (v0.y + v1.y * f + v2.y) / (2 + f)))
    return out


def p_filterShort(poly, threshold):
    i = 1
    v0 = poly[0]
    result = [v0]
    while i < len(poly):
        v1 = poly[i]
        i += 1
        while distance(v0, v1) < threshold and i < len(poly):
            v1 = poly[i]
            i += 1
        v0 = v1
        result.append(v0)
    return result


def p_inset(poly, p1, d):
    i1 = poly.index(p1)
    i0 = i1 - 1 if i1 > 0 else len(poly) - 1
    p0 = poly[i0]
    i2 = i1 + 1 if i1 < len(poly) - 1 else 0
    p2 = poly[i2]
    i3 = i2 + 1 if i2 < len(poly) - 1 else 0
    p3 = poly[i3]

    v0 = p1.subtract(p0)
    v1 = p2.subtract(p1)
    v2 = p3.subtract(p2)

    cosv = v0.dot(v1) / v0.length() / v1.length()
    z = v0.x * v1.y - v0.y * v1.x
    t = d / math.sqrt(1 - cosv * cosv)
    if z > 0:
        t = min(t, v0.length() * 0.99)
    else:
        t = min(t, v1.length() * 0.5)
    t *= sign(z)
    poly[i1] = p1.subtract(v0.norm(t))

    cosv = v1.dot(v2) / v1.length() / v2.length()
    z = v1.x * v2.y - v1.y * v2.x
    t = d / math.sqrt(1 - cosv * cosv)
    if z > 0:
        t = min(t, v2.length() * 0.99)
    else:
        t = min(t, v1.length() * 0.5)
    poly[i2] = p2.add(v2.norm(t))


def p_insetEq(poly, d):
    # operate on a copy, inset modifies by index
    q = list(poly)
    for i in range(len(q)):
        if d != 0:
            p_inset(q, q[i], d)
    return q


def p_insetAll(poly, ds):
    q = list(poly)
    for i in range(len(q)):
        if ds[i] != 0:
            p_inset(q, q[i], ds[i])
    return q


def p_buffer(poly, ds):
    q = []
    i = 0
    n = len(poly)
    for k in range(n):
        v0, v1 = poly[k], poly[(k + 1) % n]
        dd = ds[i]
        i += 1
        if dd == 0:
            q.append(v0)
            q.append(v1)
        else:
            v = v1.subtract(v0)
            nv = v.rotate90().norm(dd)
            q.append(v0.add(nv))
            q.append(v1.add(nv))

    # resolve self-intersections (as the original)
    wasCut = True
    lastEdge = 0
    while wasCut:
        wasCut = False
        n = len(q)
        for i in range(lastEdge, n - 2):
            lastEdge = i
            p11, p12 = q[i], q[i + 1]
            x1, y1 = p11.x, p11.y
            dx1, dy1 = p12.x - x1, p12.y - y1
            jmax = n if i > 0 else n - 1
            for j in range(i + 2, jmax):
                p21 = q[j]
                p22 = q[j + 1] if j < n - 1 else q[0]
                x2, y2 = p21.x, p21.y
                dx2, dy2 = p22.x - x2, p22.y - y2
                t = intersectLines(x1, y1, dx1, dy1, x2, y2, dx2, dy2)
                if t and DELTA < t.x < 1 - DELTA and DELTA < t.y < 1 - DELTA:
                    pn = Pt(x1 + dx1 * t.x, y1 + dy1 * t.x)
                    q.insert(j + 1, pn)
                    q.insert(i + 1, pn)
                    wasCut = True
                    break
            if wasCut:
                break

    # pick the biggest part
    regular = list(range(len(q)))
    bestPart = None
    bestPartSq = -1e18
    while regular:
        indices = []
        start = regular[0]
        i = start
        while True:
            indices.append(i)
            if i in regular:
                regular.remove(i)
            nxt = (i + 1) % len(q)
            v = q[nxt]
            # first index of v after nxt (identity)
            next1 = -1
            for idx in range(len(q)):
                if q[idx] is v:
                    next1 = idx
                    if idx == nxt:
                        # look for a duplicate elsewhere
                        for idx2 in range(len(q) - 1, -1, -1):
                            if q[idx2] is v:
                                next1 = idx2
                                break
                    break
            i = nxt if next1 == -1 else next1
            if i == start:
                break
        part = [q[i] for i in indices]
        s = p_square(part)
        if s > bestPartSq:
            bestPart, bestPartSq = part, s
    return bestPart if bestPart is not None else list(poly)


def p_bufferEq(poly, d):
    return p_buffer(poly, [d] * len(poly))


def p_shrink(poly, ds):
    q = list(poly)
    n = len(poly)
    for i in range(n):
        dd = ds[i]
        if dd > 0:
            v1, v2 = poly[i], poly[(i + 1) % n]
            v = v2.subtract(v1)
            nv = v.rotate90().norm(dd)
            q = p_cut(q, v1.add(nv), v2.add(nv), 0)[0]
    return q


def p_shrinkEq(poly, d):
    return p_shrink(poly, [d] * len(poly))


def p_peel(poly, v1, d):
    i1 = poly.index(v1)
    i2 = 0 if i1 == len(poly) - 1 else i1 + 1
    v2 = poly[i2]
    v = v2.subtract(v1)
    n = v.rotate90().norm(d)
    return p_cut(poly, v1.add(n), v2.add(n), 0)[0]


def p_simplyfy(poly, n):
    poly = list(poly)
    while len(poly) > n:
        ln = len(poly)
        result = 0
        mn = float('inf')
        b, c = poly[ln - 1], poly[0]
        for i in range(ln):
            a, b, c = b, c, poly[(i + 1) % ln]
            measure = abs(a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
            if measure < mn:
                result = i
                mn = measure
        poly.pop(result)
    return poly


def p_borders(poly, another):
    len1, len2 = len(poly), len(another)
    for i in range(len1):
        # identity index
        j = -1
        for k in range(len2):
            if another[k] is poly[i]:
                j = k
                break
        if j != -1:
            nxt = poly[(i + 1) % len1]
            if nxt is another[(j + 1) % len2] or nxt is another[(j + len2 - 1) % len2]:
                return True
    return False


def p_spliti(poly, i1, i2):
    if i1 > i2:
        i1, i2 = i2, i1
    return [list(poly[i1:i2 + 1]), list(poly[i2:]) + list(poly[:i1 + 1])]


def p_split(poly, p1, p2):
    return p_spliti(poly, poly.index(p1), poly.index(p2))


def p_cut(poly, p1, p2, gap=0.0):
    x1, y1 = p1.x, p1.y
    dx1, dy1 = p2.x - x1, p2.y - y1
    ln = len(poly)
    edge1, ratio1 = 0, 0.0
    edge2, ratio2 = 0, 0.0
    count = 0
    for i in range(ln):
        v0 = poly[i]
        v1 = poly[(i + 1) % ln]
        x2, y2 = v0.x, v0.y
        dx2, dy2 = v1.x - x2, v1.y - y2
        t = intersectLines(x1, y1, dx1, dy1, x2, y2, dx2, dy2)
        if t is not None and 0 <= t.y <= 1:
            if count == 0:
                edge1, ratio1 = i, t.x
            elif count == 1:
                edge2, ratio2 = i, t.x
            count += 1
    if count == 2:
        point1 = p1.add(p2.subtract(p1).scale(ratio1))
        point2 = p1.add(p2.subtract(p1).scale(ratio2))
        half1 = list(poly[edge1 + 1:edge2 + 1])
        half1.insert(0, point1)
        half1.append(point2)
        half2 = list(poly[edge2 + 1:]) + list(poly[:edge1 + 1])
        half2.insert(0, point2)
        half2.append(point1)
        if gap > 0:
            half1 = p_peel(half1, point2, gap / 2)
            half2 = p_peel(half2, point1, gap / 2)
        v = p_vectori(poly, edge1)
        if cross(dx1, dy1, v.x, v.y) > 0:
            return [half1, half2]
        return [half2, half1]
    return [list(poly)]


def p_interpolate(poly, p):
    total = 0.0
    dd = []
    for v in poly:
        d = 1 / max(distance(v, p), 1e-9)
        dd.append(d)
        total += d
    return [d / total for d in dd]


def p_rect(w=1.0, h=1.0):
    return [Pt(-w / 2, -h / 2), Pt(w / 2, -h / 2), Pt(w / 2, h / 2), Pt(-w / 2, h / 2)]


def p_regular(n=8, r=1.0):
    return [Pt(r * math.cos(i / n * math.pi * 2), r * math.sin(i / n * math.pi * 2))
            for i in range(n)]


# ---------------------------------------------------------------- Voronoi

class Triangle:
    __slots__ = ('p1', 'p2', 'p3', 'c', 'r')

    def __init__(self, p1, p2, p3):
        s = (p2.x - p1.x) * (p2.y + p1.y) + \
            (p3.x - p2.x) * (p3.y + p2.y) + \
            (p1.x - p3.x) * (p1.y + p3.y)
        self.p1 = p1
        self.p2 = p2 if s > 0 else p3   # CCW
        self.p3 = p3 if s > 0 else p2

        x1, y1 = (p1.x + p2.x) / 2, (p1.y + p2.y) / 2
        x2, y2 = (p2.x + p3.x) / 2, (p2.y + p3.y) / 2
        dx1, dy1 = p1.y - p2.y, p2.x - p1.x
        dx2, dy2 = p2.y - p3.y, p3.x - p2.x
        tg1 = dy1 / dx1 if dx1 != 0 else float('inf')
        den = (dy2 - dx2 * tg1)
        t2 = ((y1 - y2) - (x1 - x2) * tg1) / den if den != 0 else 0.0
        self.c = Pt(x2 + dx2 * t2, y2 + dy2 * t2)
        self.r = distance(self.c, p1)

    def hasEdge(self, a, b):
        return (self.p1 is a and self.p2 is b) or \
               (self.p2 is a and self.p3 is b) or \
               (self.p3 is a and self.p1 is b)


class Region:
    def __init__(self, seed):
        self.seed = seed
        self.vertices = []  # triangles

    def sortVertices(self):
        self.vertices.sort(key=_cmp_to_key(self._compareAngles))
        return self

    def _compareAngles(self, v1, v2):
        x1, y1 = v1.c.x - self.seed.x, v1.c.y - self.seed.y
        x2, y2 = v2.c.x - self.seed.x, v2.c.y - self.seed.y
        if x1 >= 0 and x2 < 0:
            return 1
        if x2 >= 0 and x1 < 0:
            return -1
        if x1 == 0 and x2 == 0:
            return 1 if y2 > y1 else -1
        return sign(x2 * y1 - x1 * y2)

    def center(self):
        c = Pt()
        for v in self.vertices:
            c.addEq(v.c)
        c.scaleEq(1 / len(self.vertices))
        return c

    def borders(self, r):
        len1, len2 = len(self.vertices), len(r.vertices)
        for i in range(len1):
            j = -1
            for k in range(len2):
                if r.vertices[k] is self.vertices[i]:
                    j = k
                    break
            if j != -1:
                return self.vertices[(i + 1) % len1] is r.vertices[(j + len2 - 1) % len2]
        return False


def _cmp_to_key(cmp):
    class K:
        def __init__(self, obj):
            self.obj = obj
        def __lt__(self, other):
            return cmp(self.obj, other.obj) < 0
    return K


class Voronoi:
    def __init__(self, minx, miny, maxx, maxy):
        self.triangles = []
        c1, c2 = Pt(minx, miny), Pt(minx, maxy)
        c3, c4 = Pt(maxx, miny), Pt(maxx, maxy)
        self.frame = [c1, c2, c3, c4]
        self.points = [c1, c2, c3, c4]
        self.triangles.append(Triangle(c1, c2, c3))
        self.triangles.append(Triangle(c2, c3, c4))
        self._regions = {id(p): self._buildRegion(p) for p in self.points}
        self._regionsDirty = False

    def _buildRegion(self, p):
        r = Region(p)
        for tr in self.triangles:
            if tr.p1 is p or tr.p2 is p or tr.p3 is p:
                r.vertices.append(tr)
        return r.sortVertices()

    def addPoint(self, p):
        toSplit = [tr for tr in self.triangles if distance(p, tr.c) < tr.r]
        if toSplit:
            self.points.append(p)
            a, b = [], []
            for t1 in toSplit:
                e1 = e2 = e3 = True
                for t2 in toSplit:
                    if t2 is t1:
                        continue
                    if e1 and t2.hasEdge(t1.p2, t1.p1):
                        e1 = False
                    if e2 and t2.hasEdge(t1.p3, t1.p2):
                        e2 = False
                    if e3 and t2.hasEdge(t1.p1, t1.p3):
                        e3 = False
                    if not (e1 or e2 or e3):
                        break
                if e1:
                    a.append(t1.p1); b.append(t1.p2)
                if e2:
                    a.append(t1.p2); b.append(t1.p3)
                if e3:
                    a.append(t1.p3); b.append(t1.p1)
            index = 0
            while True:
                self.triangles.append(Triangle(p, a[index], b[index]))
                # index of a-element identical to b[index]
                nxt = -1
                for k in range(len(a)):
                    if a[k] is b[index]:
                        nxt = k
                        break
                index = nxt
                if index == 0:
                    break
            for tr in toSplit:
                self.triangles.remove(tr)
            self._regionsDirty = True

    def regions_map(self):
        if self._regionsDirty:
            self._regions = {}
            self._regionsDirty = False
            for p in self.points:
                self._regions[id(p)] = self._buildRegion(p)
        return self._regions

    def _isReal(self, tr):
        return not (tr.p1 in self.frame or tr.p2 in self.frame or tr.p3 in self.frame)

    def triangulation(self):
        return [tr for tr in self.triangles if self._isReal(tr)]

    def partioning(self):
        result = []
        for p in self.points:
            r = self._regions.get(id(p)) or self._buildRegion(p)
            is_real = True
            for v in r.vertices:
                if not self._isReal(v):
                    is_real = False
                    break
            if is_real:
                result.append(r)
        return result

    @staticmethod
    def relax(voronoi, toRelax=None):
        regions = voronoi.partioning()
        points = [p for p in voronoi.points if p not in voronoi.frame]
        if toRelax is None:
            toRelax = voronoi.points
        for r in regions:
            if any(p is r.seed for p in toRelax):
                points = [p for p in points if p is not r.seed]
                points.append(r.center())
        return Voronoi.build(points)

    @staticmethod
    def build(vertices):
        minx = miny = 1e10
        maxx = maxy = -1e9
        for v in vertices:
            minx = min(minx, v.x); miny = min(miny, v.y)
            maxx = max(maxx, v.x); maxy = max(maxy, v.y)
        dx, dy = (maxx - minx) * 0.5, (maxy - miny) * 0.5
        vor = Voronoi(minx - dx / 2, miny - dy / 2, maxx + dx / 2, maxy + dy / 2)
        for v in vertices:
            vor.addPoint(v)
        return vor


# ---------------------------------------------------------------- Graph / Node

class Node:
    def __init__(self):
        self.links = {}

    def link(self, node, price=1.0, symmetrical=True):
        self.links[node] = price
        if symmetrical:
            node.links[self] = price


class Graph:
    def __init__(self):
        self.nodes = []

    def add(self):
        n = Node()
        self.nodes.append(n)
        return n

    def aStar(self, start, goal, exclude=None):
        closedSet = list(exclude) if exclude else []
        openSet = [start]
        cameFrom = {}
        gScore = {id(start): 0}
        while openSet:
            current = openSet.pop(0)
            if current is goal:
                return self._buildPath(cameFrom, current)
            closedSet.append(current)
            curScore = gScore[id(current)]
            for neighbour, price in current.links.items():
                if any(n is neighbour for n in closedSet):
                    continue
                score = curScore + price
                if not any(n is neighbour for n in openSet):
                    openSet.append(neighbour)
                elif score >= gScore.get(id(neighbour), float('inf')):
                    continue
                cameFrom[id(neighbour)] = current
                gScore[id(neighbour)] = score
        return None

    def _buildPath(self, cameFrom, current):
        path = [current]
        while id(current) in cameFrom:
            current = cameFrom[id(current)]
            path.append(current)
        return path


# ---------------------------------------------------------------- Cutter

class Cutter:
    @staticmethod
    def bisect(poly, vertex, ratio=0.5, angle=0.0, gap=0.0):
        nxt = p_next(poly, vertex)
        p1 = interpolate(vertex, nxt, ratio)
        d = nxt.subtract(vertex)
        cosB, sinB = math.cos(angle), math.sin(angle)
        vx = d.x * cosB - d.y * sinB
        vy = d.y * cosB + d.x * sinB
        p2 = Pt(p1.x - vy, p1.y + vx)
        return p_cut(poly, p1, p2, gap)

    @staticmethod
    def radial(poly, center=None, gap=0.0):
        if center is None:
            center = p_centroid(poly)
        sectors = []
        n = len(poly)
        for i in range(n):
            v0, v1 = poly[i], poly[(i + 1) % n]
            sector = [center, v0, v1]
            if gap > 0:
                sector = p_shrink(sector, [gap / 2, 0, gap / 2])
            sectors.append(sector)
        return sectors

    @staticmethod
    def semiRadial(poly, center=None, gap=0.0):
        if center is None:
            centroid = p_centroid(poly)
            center = min(poly, key=lambda v: distance(v, centroid))
        gap /= 2
        sectors = []
        n = len(poly)
        for i in range(n):
            v0, v1 = poly[i], poly[(i + 1) % n]
            if v0 is not center and v1 is not center:
                sector = [center, v0, v1]
                if gap > 0:
                    d = [gap if p_findEdge(poly, center, v0) == -1 else 0,
                         0,
                         gap if p_findEdge(poly, v1, center) == -1 else 0]
                    sector = p_shrink(sector, d)
                sectors.append(sector)
        return sectors

    @staticmethod
    def ring(poly, thickness):
        slices = []
        n = len(poly)
        for i in range(n):
            v1, v2 = poly[i], poly[(i + 1) % n]
            v = v2.subtract(v1)
            nv = v.rotate90().norm(thickness)
            slices.append((v1.add(nv), v2.add(nv), v.length()))
        slices.sort(key=lambda s: s[2])
        peel = []
        p = list(poly)
        for i in range(len(slices)):
            halves = p_cut(p, slices[i][0], slices[i][1])
            p = halves[0]
            if len(halves) == 2:
                peel.append(halves[1])
        return peel


# ---------------------------------------------------------------- Patch / CurtainWall / Model

class Patch:
    def __init__(self, vertices):
        self.shape = list(vertices)
        self.ward = None
        self.withinCity = False
        self.withinWalls = False

    @staticmethod
    def fromRegion(r):
        return Patch([tr.c for tr in r.vertices])


class GenError(Exception):
    pass


class CurtainWall:
    def __init__(self, real, model, patches, reserved):
        self.real = True
        self.patches = patches
        self.model = model
        if len(patches) == 1:
            self.shape = list(patches[0].shape)
        else:
            self.shape = Model.findCircumference(patches)
            if real:
                smoothFactor = min(1, 40 / len(patches))
                smoothed = []
                for v in self.shape:
                    if any(r is v for r in reserved):
                        smoothed.append(v)
                    else:
                        smoothed.append(p_smoothVertex(self.shape, v, smoothFactor))
                for i, v in enumerate(smoothed):
                    self.shape[i].set(v)
        self.segments = [True] * len(self.shape)
        self.buildGates(real, model, reserved)
        self.towers = []

    def buildGates(self, real, model, reserved):
        self.gates = []
        if len(self.patches) > 1:
            entrances = [v for v in self.shape
                         if not any(r is v for r in reserved) and
                         sum(1 for p in self.patches if in_poly(p.shape, v)) > 1]
        else:
            entrances = [v for v in self.shape if not any(r is v for r in reserved)]
        if not entrances:
            raise GenError('Bad walled area shape!')

        while True:
            index = Random.int(0, len(entrances))
            gate = entrances[index]
            self.gates.append(gate)

            if real:
                outerWards = [w for w in model.patchByVertex(gate)
                              if not any(p is w for p in self.patches)]
                if len(outerWards) == 1:
                    outer = outerWards[0]
                    if len(outer.shape) > 3:
                        wallv = p_next(self.shape, gate).subtract(p_prev(self.shape, gate))
                        out = Pt(wallv.y, -wallv.x)

                        def key(v):
                            if in_poly(self.shape, v) or any(r is v for r in reserved):
                                return -1e18
                            d = v.subtract(gate)
                            return d.dot(out) / d.length()
                        farthest = max(outer.shape, key=key)
                        newPatches = [Patch(half) for half in p_split(outer.shape, gate, farthest)]
                        Model.replace_patch(model.patches, outer, newPatches)

            if index == 0:
                del entrances[0:2]
                if entrances:
                    entrances.pop()
            elif index == len(entrances) - 1:
                del entrances[index - 1:index + 1]
                if entrances:
                    entrances.pop(0)
            else:
                del entrances[index - 1:index + 2]

            if len(entrances) < 3:
                break

        if not self.gates:
            raise GenError('Bad walled area shape!')
        if self.real:
            for gate in self.gates:
                gate.set(p_smoothVertex(self.shape, gate))

    def buildTowers(self):
        self.towers = []
        ln = len(self.shape)
        for i in range(ln):
            t = self.shape[i]
            if not any(g is t for g in self.gates) and \
               (self.segments[(i + ln - 1) % ln] or self.segments[i]):
                self.towers.append(t)

    def getRadius(self):
        return max(v.length() for v in self.shape)

    def bordersBy(self, p, v0, v1):
        index = p_findEdge(self.shape, v0, v1) if any(q is p for q in self.patches) \
            else p_findEdge(self.shape, v1, v0)
        return index != -1 and self.segments[index]

    def borders(self, p):
        withinWalls = any(q is p for q in self.patches)
        ln = len(self.shape)
        for i in range(ln):
            if self.segments[i]:
                v0, v1 = self.shape[i], self.shape[(i + 1) % ln]
                index = p_findEdge(p.shape, v0, v1) if withinWalls \
                    else p_findEdge(p.shape, v1, v0)
                if index != -1:
                    return True
        return False


# ---------------------------------------------------------------- Wards

MAIN_STREET = 2.0
REGULAR_STREET = 1.0
ALLEY = 0.6


class Ward:
    def __init__(self, model, patch):
        self.model = model
        self.patch = patch
        self.geometry = []

    def createGeometry(self):
        self.geometry = []

    def getLabel(self):
        return None

    def getCityBlock(self):
        insetDist = []
        innerPatch = self.model.wall is None or self.patch.withinWalls
        n = len(self.patch.shape)
        for i in range(n):
            v0, v1 = self.patch.shape[i], self.patch.shape[(i + 1) % n]
            if self.model.wall is not None and self.model.wall.bordersBy(self.patch, v0, v1):
                insetDist.append(MAIN_STREET / 2)
            else:
                onStreet = innerPatch and (
                    self.model.plaza is not None and
                    p_findEdge(self.model.plaza.shape, v1, v0) != -1)
                if not onStreet:
                    for street in self.model.arteries:
                        if in_poly(street, v0) and in_poly(street, v1):
                            onStreet = True
                            break
                insetDist.append((MAIN_STREET if onStreet else
                                  (REGULAR_STREET if innerPatch else ALLEY)) / 2)
        return p_shrink(self.patch.shape, insetDist) if p_isConvex(self.patch.shape) \
            else p_buffer(self.patch.shape, insetDist)

    def filterOutskirts(self):
        populatedEdges = []

        def addEdge(v1, v2, factor=1.0):
            dx, dy = v2.x - v1.x, v2.y - v1.y
            distances = {}
            def d(v):
                if v is not v1 and v is not v2:
                    distances[id(v)] = distance2line(v1.x, v1.y, dx, dy, v.x, v.y)
                else:
                    distances[id(v)] = 0.0
                return distances[id(v)]
            best = max(self.patch.shape, key=d)
            populatedEdges.append((v1.x, v1.y, dx, dy, distances[id(best)]))

        n = len(self.patch.shape)
        for i in range(n):
            v1, v2 = self.patch.shape[i], self.patch.shape[(i + 1) % n]
            onRoad = False
            for street in self.model.arteries:
                if in_poly(street, v1) and in_poly(street, v2):
                    onRoad = True
                    break
            if onRoad:
                addEdge(v1, v2, 1)
            else:
                nb = self.model.getNeighbour(self.patch, v1)
                if nb is not None and nb.withinCity:
                    addEdge(v1, v2, 1 if self.model.isEnclosed(nb) else 0.4)

        density = []
        for v in self.patch.shape:
            if any(g is v for g in self.model.gates):
                density.append(1.0)
            elif all(p.withinCity for p in self.model.patchByVertex(v)):
                density.append(2 * Random.float())
            else:
                density.append(0.0)

        kept = []
        for building in self.geometry:
            minDist = 1.0
            for (ex, ey, dx, dy, dmax) in populatedEdges:
                for v in building:
                    d = distance2line(ex, ey, dx, dy, v.x, v.y)
                    dist = d / dmax if dmax else 99
                    if dist < minDist:
                        minDist = dist
            c = p_center(building)
            interp = p_interpolate(self.patch.shape, c)
            p = sum(density[j] * interp[j] for j in range(len(interp)))
            minDist /= p if p else 1.0
            if Random.fuzzy(1) > minDist:
                kept.append(building)
        self.geometry = kept

    @staticmethod
    def createAlleys(p, minSq, gridChaos, sizeChaos, emptyProb=0.04, split=True):
        # longest edge -> its first vertex
        v = None
        length = -1.0
        n = len(p)
        for i in range(n):
            ln = distance(p[i], p[(i + 1) % n])
            if ln > length:
                length = ln
                v = p[i]

        spread = 0.8 * gridChaos
        ratio = (1 - spread) / 2 + Random.float() * spread

        angleSpread = math.pi / 6 * gridChaos * (0.0 if p_square(p) < minSq * 4 else 1.0)
        b = (Random.float() - 0.5) * angleSpread

        halves = Cutter.bisect(p, v, ratio, b, ALLEY if split else 0.0)

        buildings = []
        for half in halves:
            if p_square(half) < minSq * math.pow(2, 4 * sizeChaos * (Random.float() - 0.5)):
                if not Random.bool(emptyProb):
                    buildings.append(half)
            else:
                buildings += Ward.createAlleys(
                    half, minSq, gridChaos, sizeChaos, emptyProb,
                    p_square(half) > minSq / (Random.float() * Random.float()))
        return buildings

    @staticmethod
    def createOrthoBuilding(poly, minBlockSq, fill):
        def findLongestEdge(p):
            return p.index(min(p, key=lambda v: -p_vector(p, v).length()))

        def slice_poly(p, c1, c2):
            v0i = findLongestEdge(p)
            v0 = p[v0i]
            v1 = p[v0i + 1] if v0i < len(p) - 1 else p[0]
            v = v1.subtract(v0)
            ratio = 0.4 + Random.float() * 0.2
            p1 = interpolate(v0, v1, ratio)
            if abs(sscalar(v.x, v.y, c1.x, c1.y)) < abs(sscalar(v.x, v.y, c2.x, c2.y)):
                c = c1
            else:
                c = c2
            halves = p_cut(p, p1, p1.add(c))
            buildings = []
            for half in halves:
                if p_square(half) < minBlockSq * math.pow(2, Random.normal() * 2 - 1):
                    if Random.bool(fill):
                        buildings.append(half)
                else:
                    buildings += slice_poly(half, c1, c2)
            return buildings

        if p_square(poly) < minBlockSq:
            return [list(poly)]
        c1 = p_vector(poly, poly[findLongestEdge(poly)])
        c2 = c1.rotate90()
        while True:
            blocks = slice_poly(poly, c1, c2)
            if blocks:
                return blocks


class CommonWard(Ward):
    def __init__(self, model, patch, minSq, gridChaos, sizeChaos, emptyProb=0.04):
        super().__init__(model, patch)
        self.minSq = minSq
        self.gridChaos = gridChaos
        self.sizeChaos = sizeChaos
        self.emptyProb = emptyProb

    def createGeometry(self):
        block = self.getCityBlock()
        self.geometry = Ward.createAlleys(block, self.minSq, self.gridChaos,
                                          self.sizeChaos, self.emptyProb)
        if not self.model.isEnclosed(self.patch):
            self.filterOutskirts()


class CraftsmenWard(CommonWard):
    def __init__(self, model, patch):
        super().__init__(model, patch,
                         10 + 80 * Random.float() * Random.float(),
                         0.5 + Random.float() * 0.2, 0.6)

    def getLabel(self):
        return 'Craftsmen'


class MerchantWard(CommonWard):
    def __init__(self, model, patch):
        super().__init__(model, patch,
                         50 + 60 * Random.float() * Random.float(),
                         0.5 + Random.float() * 0.3, 0.7, 0.15)

    @staticmethod
    def rateLocation(model, patch):
        return p_distance(patch.shape, p_center(model.plaza.shape) if model.plaza else model.center)

    def getLabel(self):
        return 'Merchant'


class PatriciateWard(CommonWard):
    def __init__(self, model, patch):
        super().__init__(model, patch,
                         80 + 30 * Random.float() * Random.float(),
                         0.5 + Random.float() * 0.3, 0.8, 0.2)

    @staticmethod
    def rateLocation(model, patch):
        rate = 0
        for p in model.patches:
            if p.ward is not None and p_borders(p.shape, patch.shape):
                if isinstance(p.ward, Park):
                    rate -= 1
                elif isinstance(p.ward, Slum):
                    rate += 1
        return rate

    def getLabel(self):
        return 'Patriciate'


class Slum(CommonWard):
    def __init__(self, model, patch):
        super().__init__(model, patch,
                         10 + 30 * Random.float() * Random.float(),
                         0.6 + Random.float() * 0.4, 0.8, 0.03)

    @staticmethod
    def rateLocation(model, patch):
        return -p_distance(patch.shape, p_center(model.plaza.shape) if model.plaza else model.center)

    def getLabel(self):
        return 'Slum'


class Market(Ward):
    def createGeometry(self):
        statue = Random.bool(0.6)
        offset = statue or Random.bool(0.3)
        v0 = v1 = None
        if statue or offset:
            length = -1.0
            n = len(self.patch.shape)
            for i in range(n):
                p0, p1 = self.patch.shape[i], self.patch.shape[(i + 1) % n]
                ln = distance(p0, p1)
                if ln > length:
                    length, v0, v1 = ln, p0, p1
        if statue:
            obj = p_rect(1 + Random.float(), 1 + Random.float())
            ang = math.atan2(v1.y - v0.y, v1.x - v0.x)
            cosA, sinA = math.cos(ang), math.sin(ang)
            for v in obj:
                vx = v.x * cosA - v.y * sinA
                vy = v.y * cosA + v.x * sinA
                v.x, v.y = vx, vy
        else:
            obj = p_regular(16, 1 + Random.float())
        if offset:
            gravity = interpolate(v0, v1)
            obj_pt = interpolate(p_centroid(self.patch.shape), gravity,
                                 0.2 + Random.float() * 0.4)
        else:
            obj_pt = p_centroid(self.patch.shape)
        for v in obj:
            v.offset(obj_pt.x, obj_pt.y)
        self.geometry = [obj]

    @staticmethod
    def rateLocation(model, patch):
        for p in model.inner:
            if isinstance(p.ward, Market) and p_borders(p.shape, patch.shape):
                return float('inf')
        return p_square(patch.shape) / p_square(model.plaza.shape) if model.plaza \
            else p_distance(patch.shape, model.center)

    def getLabel(self):
        return 'Market'


class Castle(Ward):
    def __init__(self, model, patch):
        super().__init__(model, patch)
        reserved = [v for v in patch.shape
                    if any(not p.withinCity for p in model.patchByVertex(v))]
        self.wall = CurtainWall(True, model, [patch], reserved)

    def createGeometry(self):
        block = p_shrinkEq(self.patch.shape, MAIN_STREET * 2)
        self.geometry = Ward.createOrthoBuilding(block, math.sqrt(abs(p_square(block))) * 4, 0.6)

    def getLabel(self):
        return 'Castle'


class Cathedral(Ward):
    def createGeometry(self):
        if Random.bool(0.4):
            self.geometry = Cutter.ring(self.getCityBlock(), 2 + Random.float() * 4)
        else:
            self.geometry = Ward.createOrthoBuilding(self.getCityBlock(), 50, 0.8)

    @staticmethod
    def rateLocation(model, patch):
        if model.plaza is not None and p_borders(patch.shape, model.plaza.shape):
            return -1 / p_square(patch.shape)
        return p_distance(patch.shape, p_center(model.plaza.shape) if model.plaza else model.center) \
            * p_square(patch.shape)

    def getLabel(self):
        return 'Temple'


class GateWard(CommonWard):
    def __init__(self, model, patch):
        super().__init__(model, patch,
                         10 + 50 * Random.float() * Random.float(),
                         0.5 + Random.float() * 0.3, 0.7)

    def getLabel(self):
        return 'Gate'


class AdministrationWard(CommonWard):
    def __init__(self, model, patch):
        super().__init__(model, patch,
                         80 + 30 * Random.float() * Random.float(),
                         0.1 + Random.float() * 0.3, 0.3)

    @staticmethod
    def rateLocation(model, patch):
        if model.plaza is not None:
            return 0 if p_borders(patch.shape, model.plaza.shape) \
                else p_distance(patch.shape, p_center(model.plaza.shape))
        return p_distance(patch.shape, model.center)

    def getLabel(self):
        return 'Administration'


class MilitaryWard(Ward):
    def createGeometry(self):
        block = self.getCityBlock()
        self.geometry = Ward.createAlleys(
            block, math.sqrt(abs(p_square(block))) * (1 + Random.float()),
            0.1 + Random.float() * 0.3, 0.3, 0.25)

    @staticmethod
    def rateLocation(model, patch):
        if model.citadel is not None and p_borders(model.citadel.shape, patch.shape):
            return 0
        if model.wall is not None and model.wall.borders(patch):
            return 1
        if model.citadel is None and model.wall is None:
            return 0
        return float('inf')

    def getLabel(self):
        return 'Military'


class Park(Ward):
    def createGeometry(self):
        block = self.getCityBlock()
        self.geometry = Cutter.radial(block, None, ALLEY) if p_compactness(block) >= 0.7 \
            else Cutter.semiRadial(block, None, ALLEY)

    def getLabel(self):
        return 'Park'


class Farm(Ward):
    def createGeometry(self):
        housing = p_rect(4, 4)
        rand_v = self.patch.shape[Random.int(0, len(self.patch.shape))]
        pos = interpolate(rand_v, p_centroid(self.patch.shape), 0.3 + Random.float() * 0.4)
        ang = Random.float() * math.pi
        cosA, sinA = math.cos(ang), math.sin(ang)
        for v in housing:
            vx = v.x * cosA - v.y * sinA
            vy = v.y * cosA + v.x * sinA
            v.x, v.y = vx, vy
        for v in housing:
            v.offset(pos.x, pos.y)
        self.geometry = Ward.createOrthoBuilding(housing, 8, 0.5)

    def getLabel(self):
        return 'Farm'


# ---------------------------------------------------------------- Model

WARD_CLASSES = [CraftsmenWard, CraftsmenWard, MerchantWard, CraftsmenWard, CraftsmenWard, Cathedral,
                CraftsmenWard, CraftsmenWard, CraftsmenWard, CraftsmenWard, CraftsmenWard,
                CraftsmenWard, CraftsmenWard, CraftsmenWard, AdministrationWard, CraftsmenWard,
                Slum, CraftsmenWard, Slum, PatriciateWard, Market,
                Slum, CraftsmenWard, CraftsmenWard, CraftsmenWard, Slum,
                CraftsmenWard, CraftsmenWard, CraftsmenWard, MilitaryWard, Slum,
                CraftsmenWard, Park, PatriciateWard, Market, MerchantWard]

RATE_FUNCS = {
    MerchantWard: MerchantWard.rateLocation,
    PatriciateWard: PatriciateWard.rateLocation,
    Slum: Slum.rateLocation,
    Market: Market.rateLocation,
    Cathedral: Cathedral.rateLocation,
    AdministrationWard: AdministrationWard.rateLocation,
    MilitaryWard: MilitaryWard.rateLocation,
}


class Model:
    def __init__(self, nPatches=-1, seed=-1, templeNeeded=False):
        if seed > 0:
            Random.reset(seed)
        self.seed = Random.seed
        self.nPatches = nPatches if nPatches != -1 else 15
        self.templeNeeded = templeNeeded

        self.plazaNeeded = True
        self.citadelNeeded = True
        self.wallsNeeded = True

        self.patches = []
        self.inner = []
        self.citadel = None
        self.plaza = None
        self.center = None
        self.border = None
        self.wall = None
        self.gates = []
        self.arteries = []
        self.streets = []
        self.roads = []
        self.cityRadius = 0.0

        attempts = 0
        while True:
            attempts += 1
            try:
                self.build()
                break
            except GenError:
                if attempts > 400:
                    raise

    # -- pipeline ------------------------------------------------
    def build(self):
        self.streets = []
        self.roads = []
        self.buildPatches()
        self.optimizeJunctions()
        self.buildWalls()
        self.buildStreets()
        self.createWards()
        self.buildGeometry()

    def buildPatches(self):
        sa = Random.float() * 2 * math.pi
        points = []
        for i in range(self.nPatches * 8):
            a = sa + math.sqrt(i) * 5
            r = 0 if i == 0 else 10 + i * (2 + Random.float())
            points.append(Pt(math.cos(a) * r, math.sin(a) * r))
        voronoi = Voronoi.build(points)

        # Relaxing central wards
        for _ in range(3):
            toRelax = [voronoi.points[j] for j in range(3)]
            toRelax.append(voronoi.points[self.nPatches])
            voronoi = Voronoi.relax(voronoi, toRelax)

        voronoi.points.sort(key=lambda p: 0 if p.length() == 0 else (1 if p.length() > 0 else -1))
        regions = voronoi.partioning()

        self.patches = []
        self.inner = []
        count = 0
        for r in regions:
            patch = Patch.fromRegion(r)
            self.patches.append(patch)
            if count == 0:
                self.center = min(patch.shape, key=lambda p: p.length())
                if self.plazaNeeded:
                    self.plaza = patch
            elif count == self.nPatches and self.citadelNeeded:
                self.citadel = patch
                self.citadel.withinCity = True
            if count < self.nPatches:
                patch.withinCity = True
                patch.withinWalls = self.wallsNeeded
                self.inner.append(patch)
            count += 1

    def optimizeJunctions(self):
        patchesToOptimize = self.inner if self.citadel is None else self.inner + [self.citadel]
        wards2clean = []
        for w in patchesToOptimize:
            index = 0
            while index < len(w.shape):
                v0 = w.shape[index]
                v1 = w.shape[(index + 1) % len(w.shape)]
                if v0 is not v1 and distance(v0, v1) < 8:
                    for w1 in self.patchByVertex(v1):
                        if w1 is not w:
                            w1.shape[w1.shape.index(v1)] = v0
                            wards2clean.append(w1)
                    v0.addEq(v1)
                    v0.scaleEq(0.5)
                    w.shape.remove(v1)
                    continue
                index += 1
        for w in wards2clean:
            i = 0
            while i < len(w.shape):
                v = w.shape[i]
                j = i + 1
                while j < len(w.shape):
                    if w.shape[j] is v:
                        w.shape.pop(j)
                    else:
                        j += 1
                i += 1

    def buildWalls(self):
        reserved = list(self.citadel.shape) if self.citadel is not None else []
        self.border = CurtainWall(self.wallsNeeded, self, self.inner, reserved)
        if self.wallsNeeded:
            self.wall = self.border
            self.wall.buildTowers()
        radius = self.border.getRadius()
        self.patches = [p for p in self.patches
                        if p_distance(p.shape, self.center) < radius * 3]
        self.gates = list(self.border.gates)
        if self.citadel is not None:
            try:
                castle = Castle(self, self.citadel)
                castle.wall.buildTowers()
                if p_compactness(self.citadel.shape) < 0.75:
                    raise GenError('Bad citadel shape!')
                self.citadel.ward = castle
                self.gates += castle.wall.gates
            except GenError:
                # tiny towns: the citadel borders no city ward, so its keep
                # can't have a gate — demote it to countryside for this attempt
                self.citadel.withinCity = False
                self.citadel.withinWalls = False
                self.citadel = None

    def buildStreets(self):
        self.topology = Topology(self)
        for gate in self.gates:
            if self.plaza is not None:
                end = min(self.plaza.shape, key=lambda v: distance(v, gate))
            else:
                end = self.center
            street = self.topology.buildPath(gate, end, self.topology.outer)
            if street is not None:
                self.streets.append(street)
                if any(g is gate for g in self.border.gates):
                    direction = gate.norm(1000)
                    start = None
                    dist = float('inf')
                    for p in self.topology.node2pt.values():
                        d = distance(p, direction)
                        if d < dist:
                            dist, start = d, p
                    road = self.topology.buildPath(start, gate, self.topology.inner)
                    if road is not None:
                        self.roads.append(road)
            else:
                raise GenError('Unable to build a street!')
        self.tidyUpRoads()
        # smooth
        for a in self.arteries:
            smoothed = p_smoothVertexEq(a, 3)
            for i in range(1, len(a) - 1):
                a[i].set(smoothed[i])

    def tidyUpRoads(self):
        segments = []

        def cut2segments(street):
            v1 = street[0]
            for i in range(1, len(street)):
                v0, v1 = v1, street[i]
                if self.plaza is not None and in_poly(self.plaza.shape, v0) \
                        and in_poly(self.plaza.shape, v1):
                    continue
                exists = False
                for seg in segments:
                    if seg[0] is v0 and seg[1] is v1:
                        exists = True
                        break
                if not exists:
                    segments.append((v0, v1))

        for street in self.streets:
            cut2segments(street)
        for road in self.roads:
            cut2segments(road)

        self.arteries = []
        while segments:
            seg = segments.pop()
            attached = False
            for a in self.arteries:
                if a[0] is seg[1]:
                    a.insert(0, seg[0])
                    attached = True
                    break
                if a[-1] is seg[0]:
                    a.append(seg[1])
                    attached = True
                    break
            if not attached:
                self.arteries.append([seg[0], seg[1]])

    def createWards(self):
        unassigned = list(self.inner)
        if self.plaza is not None:
            self.plaza.ward = Market(self, self.plaza)
            unassigned.remove(self.plaza)

        # inner gate wards
        for gate in self.border.gates:
            for patch in self.patchByVertex(gate):
                if patch.withinCity and patch.ward is None and \
                        Random.bool(0.2 if self.wall is None else 0.5):
                    patch.ward = GateWard(self, patch)
                    unassigned.remove(patch)

        wards = list(WARD_CLASSES)
        # the preset forces a temple: Cathedral first in line
        if self.templeNeeded:
            wards.remove(Cathedral)
            wards.insert(0, Cathedral)
        # some shuffling (as the original)
        for _ in range(len(wards) // 10):
            index = Random.int(0, len(wards) - 1)
            wards[index], wards[index + 1] = wards[index + 1], wards[index]

        # inner wards
        while unassigned:
            wardClass = wards.pop(0) if wards else Slum
            rateFunc = RATE_FUNCS.get(wardClass)
            if rateFunc is None:
                bestPatch = None
                while True:
                    bestPatch = unassigned[Random.int(0, len(unassigned) - 1)]
                    if bestPatch.ward is None:
                        break
            else:
                bestPatch = min(unassigned, key=lambda patch: float('inf') if patch.ward is not None
                                else rateFunc(self, patch))
            bestPatch.ward = wardClass(self, bestPatch)
            unassigned.remove(bestPatch)

        # outskirts
        if self.wall is not None:
            for gate in self.wall.gates:
                if not Random.bool(1 / (self.nPatches - 5)):
                    for patch in self.patchByVertex(gate):
                        if patch.ward is None:
                            patch.withinCity = True
                            patch.ward = GateWard(self, patch)

        self.cityRadius = 0
        for patch in self.patches:
            if patch.withinCity:
                for v in patch.shape:
                    self.cityRadius = max(self.cityRadius, v.length())
            elif patch.ward is None:
                patch.ward = Farm(self, patch) if (
                    Random.bool(0.2) and p_compactness(patch.shape) >= 0.7) \
                    else Ward(self, patch)

    def buildGeometry(self):
        for patch in self.patches:
            patch.ward.createGeometry()

    # -- helpers ------------------------------------------------
    def patchByVertex(self, v):
        return [p for p in self.patches if in_poly(p.shape, v)]

    def getNeighbour(self, patch, v):
        nxt = p_next(patch.shape, v)
        for p in self.patches:
            if p_findEdge(p.shape, nxt, v) != -1:
                return p
        return None

    def getNeighbours(self, patch):
        return [p for p in self.patches
                if p is not patch and p_borders(p.shape, patch.shape)]

    def isEnclosed(self, patch):
        return patch.withinCity and (
            patch.withinWalls or
            all(p.withinCity for p in self.getNeighbours(patch)))

    @staticmethod
    def findCircumference(wards):
        if len(wards) == 0:
            return []
        if len(wards) == 1:
            return list(wards[0].shape)
        A, B = [], []
        for w1 in wards:
            n = len(w1.shape)
            for i in range(n):
                a, b = w1.shape[i], w1.shape[(i + 1) % n]
                outerEdge = True
                for w2 in wards:
                    if w2 is not w1 and p_findEdge(w2.shape, b, a) != -1:
                        outerEdge = False
                        break
                if outerEdge:
                    A.append(a)
                    B.append(b)
        result = []
        index = 0
        while True:
            result.append(A[index])
            # index of element in A identical to B[index]
            nxt = -1
            for k in range(len(A)):
                if A[k] is B[index]:
                    nxt = k
                    break
            index = nxt
            if index == 0:
                break
        return result

    @staticmethod
    def replace_patch(lst, el, newEls):
        index = next(i for i, e in enumerate(lst) if e is el)
        lst[index:index + 1] = newEls


class Topology:
    def __init__(self, model):
        self.model = model
        self.graph = Graph()
        self.pt2node = {}
        self.node2pt = {}
        self.inner = []
        self.outer = []

        # blocked points: citadel + walls, excluding gates
        blocked = []
        if model.citadel is not None:
            blocked += list(model.citadel.shape)
        if model.wall is not None:
            blocked += list(model.wall.shape)
        blocked = [b for b in blocked if not any(g is b for g in model.gates)]
        self.blocked = blocked

        border_shape = model.border.shape

        for p in model.patches:
            withinCity = p.withinCity
            v1 = p.shape[-1]
            n1 = self.processPoint(v1)
            for i in range(len(p.shape)):
                v0, v1 = v1, p.shape[i]
                n0, n1 = n1, self.processPoint(v1)
                if n0 is not None and not in_poly(border_shape, v0):
                    (self.inner if withinCity else self.outer).append(n0)
                if n1 is not None and not in_poly(border_shape, v1):
                    (self.inner if withinCity else self.outer).append(n1)
                if n0 is not None and n1 is not None:
                    n0.link(n1, distance(v0, v1))

    def processPoint(self, v):
        if id(v) in self.pt2node:
            n = self.pt2node[id(v)]
        else:
            n = self.graph.add()
            self.pt2node[id(v)] = n
            self.node2pt[id(n)] = v
        return None if any(b is v for b in self.blocked) else n

    def buildPath(self, frm, to, exclude=None):
        start = self.pt2node.get(id(frm))
        goal = self.pt2node.get(id(to))
        if start is None or goal is None:
            return None
        path = self.graph.aStar(start, goal, exclude)
        if path is None:
            return None
        return [self.node2pt[id(n)] for n in path]
