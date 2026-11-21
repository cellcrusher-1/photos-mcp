"""Distances and route matching. Pure Python, no Photos access, so it is testable anywhere."""
from __future__ import annotations

import math

EARTH_MI = 3958.8


def miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_MI * math.asin(min(1.0, math.sqrt(a)))


def _xy(lat: float, lon: float, lat0: float) -> tuple[float, float]:
    """Local flat projection in miles around latitude lat0 (fine for a few hundred miles)."""
    k = math.pi / 180 * EARTH_MI
    return lon * k * math.cos(math.radians(lat0)), lat * k


def miles_to_segment(lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance in miles from a point to the straight segment a-b (each (lat, lon))."""
    lat0 = (a[0] + b[0] + lat) / 3
    px, py = _xy(lat, lon, lat0)
    ax, ay = _xy(a[0], a[1], lat0)
    bx, by = _xy(b[0], b[1], lat0)
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def nearest(lat: float, lon: float, points: list[dict], mode: str) -> tuple[int, float]:
    """Index of the nearest point (mode "points") or segment (mode "segments": segment i
    runs from points[i] to points[i+1]), and its distance in miles."""
    best, dist = -1, float("inf")
    if mode == "segments":
        for i in range(len(points) - 1):
            a, b = points[i], points[i + 1]
            d = miles_to_segment(lat, lon, (a["lat"], a["lon"]), (b["lat"], b["lon"]))
            if d < dist:
                best, dist = i, d
    else:
        for i, p in enumerate(points):
            d = miles(lat, lon, p["lat"], p["lon"])
            if d < dist:
                best, dist = i, d
    return best, dist


def clean_points(points: list) -> list[dict]:
    """Validate route points: [{name, lat, lon}], 1–500 of them."""
    if not isinstance(points, list) or not (1 <= len(points) <= 500):
        raise ValueError("points must be a list of 1 to 500 {name, lat, lon}")
    out = []
    for i, p in enumerate(points):
        try:
            lat, lon = float(p["lat"]), float(p["lon"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"point {i} needs numeric lat and lon")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"point {i} is out of range")
        out.append({"name": str(p.get("name") or f"#{i}")[:80], "lat": lat, "lon": lon})
    return out
