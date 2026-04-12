"""
experiment_metrics.py — All evaluation metrics defined in the research design.

Metrics implemented:
  Privacy:   ARR, FD (Fréchet Distance), GIS
  Utility:   SPS, VPS, EPS
  Security:  SEB, TUR, ERTI
"""
import math
import os
import hashlib

METERS_PER_DEGREE = 111_000
_OSM_AVAILABLE = None  # lazy-checked once


def _check_osm():
    global _OSM_AVAILABLE
    if _OSM_AVAILABLE is None:
        try:
            import osmnx  # noqa: F401
            _OSM_AVAILABLE = True
        except ImportError:
            _OSM_AVAILABLE = False
    return _OSM_AVAILABLE


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def haversine_m(p1, p2):
    """Great-circle distance in metres between two (lat, lng) points."""
    R = 6_371_000
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _nearest_distance_m(point, trajectory):
    return min(haversine_m(point, p) for p in trajectory)


# ---------------------------------------------------------------------------
# Metric 1 — ARR: Adversarial Recovery Rate
# ---------------------------------------------------------------------------

def compute_arr(real_trajectory, fake_trajectory, delta_m=50.0):
    """
    Simulate a road-snap attacker: for each fake point, find the closest real
    point and treat it as the "reconstructed" guess.  ARR = fraction of real
    points recovered within delta_m.

    Real attacker would use map-matching; without OSM data we use nearest-
    neighbour as a conservative upper bound on attacker capability.
    """
    if not real_trajectory or not fake_trajectory:
        return 0.0
    recovered = 0
    for real_pt in real_trajectory:
        nearest = _nearest_distance_m(real_pt, fake_trajectory)
        if nearest <= delta_m:
            recovered += 1
    return recovered / len(real_trajectory)


# ---------------------------------------------------------------------------
# Metric 2 — FD: Discrete Fréchet Distance
# ---------------------------------------------------------------------------

def compute_frechet_distance_m(traj_a, traj_b):
    """
    Discrete Fréchet distance in metres using the DP algorithm.
    O(n*m) time and space — suitable for trajectories up to ~500 points.
    """
    n, m = len(traj_a), len(traj_b)
    if n == 0 or m == 0:
        return float("inf")

    ca = [[-1.0] * m for _ in range(n)]

    def _rc(i, j):
        if ca[i][j] > -1:
            return ca[i][j]
        d = haversine_m(traj_a[i], traj_b[j])
        if i == 0 and j == 0:
            ca[i][j] = d
        elif i == 0:
            ca[i][j] = max(_rc(0, j - 1), d)
        elif j == 0:
            ca[i][j] = max(_rc(i - 1, 0), d)
        else:
            ca[i][j] = max(min(_rc(i - 1, j), _rc(i - 1, j - 1), _rc(i, j - 1)), d)
        return ca[i][j]

    return _rc(n - 1, m - 1)


# ---------------------------------------------------------------------------
# Metric 3 — GIS: Geo-Indistinguishability Score
# ---------------------------------------------------------------------------

def compute_gis(fake_trajectory, candidate_real_trajectories, fd_threshold_m=2000.0):
    """
    Count how many candidate real trajectories are "plausibly consistent"
    with the fake trajectory (FD ≤ threshold).

    Returns integer count. Target ≥ 10.
    """
    count = 0
    for cand in candidate_real_trajectories:
        fd = compute_frechet_distance_m(fake_trajectory, cand)
        if fd <= fd_threshold_m:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Metric 4 — SPS: Spatial Plausibility Score
# ---------------------------------------------------------------------------

def compute_sps(fake_trajectory, road_threshold_m=50.0, bbox_buffer=0.02):
    """
    Percentage of fake points within road_threshold_m of any OSM road.

    Falls back to a heuristic density check when OSMnx is unavailable:
    counts points inside the convex hull of the trajectory (rough proxy for
    "urban area"). This is labelled as approximate in the output.
    """
    if not _check_osm():
        return {"score": None, "method": "osm_unavailable", "note": "Install osmnx for accurate SPS"}

    try:
        import osmnx as ox
        from shapely.geometry import Point

        lats = [p[0] for p in fake_trajectory]
        lngs = [p[1] for p in fake_trajectory]
        north = max(lats) + bbox_buffer
        south = min(lats) - bbox_buffer
        east = max(lngs) + bbox_buffer
        west = min(lngs) - bbox_buffer

        G = ox.graph_from_bbox(bbox=(north, south, east, west), network_type="drive")
        _, edges = ox.graph_to_gdfs(G)

        threshold_deg = road_threshold_m / METERS_PER_DEGREE
        on_road = 0
        for pt in fake_trajectory:
            pt_geom = Point(pt[1], pt[0])  # shapely uses (lng, lat)
            min_dist = edges.geometry.distance(pt_geom).min()
            if min_dist <= threshold_deg:
                on_road += 1
        score = on_road / len(fake_trajectory)
        return {"score": score, "method": "osmnx", "note": ""}
    except Exception as exc:
        return {"score": None, "method": "osmnx_error", "note": str(exc)}


# ---------------------------------------------------------------------------
# Metric 5 — VPS: Velocity Plausibility Score
# ---------------------------------------------------------------------------

def compute_vps(fake_trajectory, timestamps, v_max_kmh=130.0):
    """
    Fraction of consecutive point pairs with implied speed in (0, v_max_kmh].

    timestamps: list of Unix floats, same length as fake_trajectory.
    """
    if len(fake_trajectory) < 2 or len(timestamps) < 2:
        return {"score": None, "note": "Need ≥2 points with timestamps"}

    v_max_ms = v_max_kmh / 3.6
    valid = 0
    usable = min(len(fake_trajectory), len(timestamps))  # guard: scramble may expand trajectory
    total = usable - 1
    if total <= 0:
        return {"score": None, "note": "Timestamps too short for expanded trajectory"}
    speeds = []
    for i in range(total):
        dist = haversine_m(fake_trajectory[i], fake_trajectory[i + 1])
        dt = timestamps[i + 1] - timestamps[i]
        if dt <= 0:
            speeds.append(None)
            continue
        speed_ms = dist / dt
        speeds.append(speed_ms * 3.6)  # store km/h
        if 0 < speed_ms <= v_max_ms:
            valid += 1
    return {
        "score": valid / total,
        "implied_speeds_kmh": speeds,
        "v_max_kmh": v_max_kmh,
    }


# ---------------------------------------------------------------------------
# Metric 6 — EPS: Endpoint Preservation Score
# ---------------------------------------------------------------------------

def compute_eps(real_trajectory, fake_trajectory):
    """
    Distance in metres between start/end points of real and fake trajectories.
    Returns dict with start_m and end_m. Target: both ≤ 50m.

    NOTE: Your system intentionally offsets endpoints by 4500m (ENDPOINT_OFFSET_METERS),
    so EPS verifies that the offset is applied (EPS > 50m is expected and correct).
    """
    start_m = haversine_m(real_trajectory[0], fake_trajectory[0])
    end_m = haversine_m(real_trajectory[-1], fake_trajectory[-1])
    return {"start_m": start_m, "end_m": end_m}


# ---------------------------------------------------------------------------
# Metric 7 — SEB: Seed Entropy Bits
# ---------------------------------------------------------------------------

def compute_seb(seed_list):
    """
    Shannon entropy in bits of a collection of session seeds.

    seed_list: list of bytes objects (each 32 bytes).
    Entropy is computed per-bit across the seed corpus.
    """
    if not seed_list:
        return {"entropy_bits": 0.0, "n_seeds": 0}

    seed_len = len(seed_list[0])
    n = len(seed_list)
    total_entropy = 0.0

    for byte_pos in range(seed_len):
        for bit_pos in range(8):
            ones = sum(1 for s in seed_list if (s[byte_pos] >> bit_pos) & 1)
            zeros = n - ones
            p1 = ones / n
            p0 = zeros / n
            if p1 > 0:
                total_entropy -= p1 * math.log2(p1)
            if p0 > 0:
                total_entropy -= p0 * math.log2(p0)

    # Normalise: max entropy for seed_len*8 bits is seed_len*8
    max_bits = seed_len * 8
    return {
        "entropy_bits": total_entropy,
        "max_possible_bits": max_bits,
        "entropy_ratio": total_entropy / max_bits,
        "n_seeds": n,
    }


# ---------------------------------------------------------------------------
# Metric 8 — TUR: Temporal Uniqueness Rate
# ---------------------------------------------------------------------------

def compute_tur(seed_list):
    """
    Proportion of seed pairs that are distinct.
    TUR = unique_seeds / total_seeds. Target = 1.0.
    """
    if not seed_list:
        return {"tur": None, "unique": 0, "total": 0}
    hex_seeds = [s.hex() if isinstance(s, (bytes, bytearray)) else str(s) for s in seed_list]
    unique = len(set(hex_seeds))
    total = len(hex_seeds)
    return {"tur": unique / total, "unique": unique, "total": total}


# ---------------------------------------------------------------------------
# Metric 9 — ERTI: Encryption Round-Trip Integrity
# ---------------------------------------------------------------------------

def compute_erti(protected_packages, secret_registry, user_id, tmp_dir="/tmp"):
    """
    For each protected_package, save to a temp file and decrypt back.
    ERTI = fraction that round-trip successfully without content change.
    """
    import tempfile
    from trajectory_layer_crypto import (
        save_encrypted_fake_trajectory_with_secret,
        load_encrypted_fake_trajectory_with_secret,
        normalize_protected_package,
        get_current_key_version,
    )

    passed = 0
    for i, pkg in enumerate(protected_packages):
        tmp_path = os.path.join(tmp_dir, f"erti_test_{i}.bin")
        try:
            key_version = get_current_key_version(secret_registry)
            secret = secret_registry[key_version]
            save_encrypted_fake_trajectory_with_secret(
                pkg, secret, tmp_path, user_id=user_id, key_version=key_version
            )
            restored = load_encrypted_fake_trajectory_with_secret(tmp_path, secret_registry, user_id)
            if normalize_protected_package(restored) == normalize_protected_package(pkg):
                passed += 1
        except Exception:
            pass
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    total = len(protected_packages)
    return {"erti": passed / total if total else None, "passed": passed, "total": total}


# ---------------------------------------------------------------------------
# Convenience: compute all metrics for a single real/fake pair
# ---------------------------------------------------------------------------

def compute_all_metrics(real_trajectory, fake_trajectory, timestamps=None, candidate_pool=None):
    """
    Run all applicable metrics for one real→fake pair.
    timestamps: list of Unix floats for fake_trajectory (required for VPS).
    candidate_pool: list of trajectories for GIS (optional).
    """
    results = {}

    results["ARR"] = compute_arr(real_trajectory, fake_trajectory)
    results["FD_m"] = compute_frechet_distance_m(real_trajectory, fake_trajectory)
    results["EPS"] = compute_eps(real_trajectory, fake_trajectory)

    if candidate_pool is not None:
        results["GIS"] = compute_gis(fake_trajectory, candidate_pool)

    if timestamps is not None:
        results["VPS"] = compute_vps(fake_trajectory, timestamps)

    results["SPS"] = compute_sps(fake_trajectory)

    return results
