"""
experiment_dataset.py — GeoLife .plt loader with synthetic fallback.

GeoLife format (.plt):
  Line 1–6: header (ignored)
  Line 7+:  lat,lng,0,altitude,days_since_1899,date,time
"""
import math
import os
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# GeoLife loader
# ---------------------------------------------------------------------------

_GEOLIFE_HEADER_LINES = 6


def _parse_plt_file(file_path):
    """Return list of (lat, lng, timestamp_unix) from a single .plt file."""
    points = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
        for line_no, raw in enumerate(fh):
            if line_no < _GEOLIFE_HEADER_LINES:
                continue
            parts = raw.strip().split(",")
            if len(parts) < 7:
                continue
            try:
                lat = float(parts[0])
                lng = float(parts[1])
                date_str = parts[5].strip()   # e.g. 2008-05-04
                time_str = parts[6].strip()   # e.g. 08:30:52
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
                ts = dt.timestamp()
                points.append((lat, lng, ts))
            except (ValueError, IndexError):
                continue
    return points


def _haversine_m(p1, p2):
    R = 6_371_000
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _trajectory_length_m(traj):
    total = 0.0
    for i in range(len(traj) - 1):
        total += _haversine_m(traj[i], traj[i + 1])
    return total


def load_geolife_trajectories(
    geolife_root,
    max_users=10,
    min_points=10,
    max_points=200,
    min_length_m=500,
    max_length_m=50_000,
):
    """
    Load trajectories from the GeoLife dataset directory structure:
        <geolife_root>/Data/<user_id>/Trajectory/*.plt

    Returns a list of dicts:
        {"user_id": str, "points": [(lat,lng), ...], "timestamps": [unix_float, ...]}
    """
    data_root = os.path.join(geolife_root, "Data")
    if not os.path.isdir(data_root):
        raise FileNotFoundError(f"GeoLife Data directory not found at: {data_root}")

    trajectories = []
    user_dirs = sorted(os.listdir(data_root))
    for user_dir in user_dirs[:max_users]:
        traj_dir = os.path.join(data_root, user_dir, "Trajectory")
        if not os.path.isdir(traj_dir):
            continue
        for plt_file in sorted(os.listdir(traj_dir)):
            if not plt_file.endswith(".plt"):
                continue
            full_path = os.path.join(traj_dir, plt_file)
            raw = _parse_plt_file(full_path)
            if len(raw) < min_points:
                continue
            # Subsample to max_points evenly
            if len(raw) > max_points:
                step = len(raw) / max_points
                raw = [raw[int(i * step)] for i in range(max_points)]
            points = [(lat, lng) for lat, lng, _ in raw]
            timestamps = [ts for _, _, ts in raw]
            length = _trajectory_length_m(points)
            if length < min_length_m or length > max_length_m:
                continue
            trajectories.append({
                "user_id": user_dir,
                "points": points,
                "timestamps": timestamps,
                "source": "geolife",
            })
    return trajectories


# ---------------------------------------------------------------------------
# Synthetic fallback — used when GeoLife is unavailable
# ---------------------------------------------------------------------------

def _make_synthetic_trajectory(start_lat, start_lng, num_points, seed_val, timestamps=True):
    """Generate a plausible urban trajectory using deterministic noise."""
    import random
    rng = random.Random(seed_val)
    lat, lng = start_lat, start_lng
    points = []
    ts_list = []
    base_time = 1_700_000_000.0 + seed_val * 3600
    for i in range(num_points):
        # Move roughly north-east with small random variation
        lat += rng.uniform(0.0003, 0.0008)
        lng += rng.uniform(0.0002, 0.0007)
        # Add small local noise
        lat += rng.gauss(0, 0.00005)
        lng += rng.gauss(0, 0.00005)
        points.append((round(lat, 7), round(lng, 7)))
        ts_list.append(base_time + i * rng.uniform(3, 8))
    return points, ts_list


def load_synthetic_trajectories(num_trajectories=20, num_points=40):
    """
    Generate synthetic GPS trajectories centered around Melbourne.
    Used as a fallback when GeoLife is not available.
    """
    trajectories = []
    # Spread origins across Melbourne region
    origins = [
        (-37.808, 144.962),  # Melbourne CBD
        (-37.820, 144.970),
        (-37.795, 144.955),
        (-37.830, 144.980),
        (-37.810, 144.940),
        (-37.840, 144.990),
        (-37.800, 144.975),
        (-37.815, 144.950),
        (-37.825, 144.965),
        (-37.790, 144.985),
    ]
    for i in range(num_trajectories):
        origin = origins[i % len(origins)]
        points, timestamps = _make_synthetic_trajectory(
            origin[0], origin[1], num_points, seed_val=i * 17 + 3
        )
        trajectories.append({
            "user_id": f"synthetic_{i:03d}",
            "points": points,
            "timestamps": timestamps,
            "source": "synthetic",
        })
    return trajectories


def load_dataset(geolife_root=None, max_users=10, num_synthetic=20, num_points=40):
    """
    Try to load GeoLife; fall back to synthetic data.

    Returns (trajectories, source_label).
    """
    if geolife_root and os.path.isdir(geolife_root):
        try:
            trajs = load_geolife_trajectories(geolife_root, max_users=max_users)
            if trajs:
                print(f"[dataset] Loaded {len(trajs)} trajectories from GeoLife.")
                return trajs, "geolife"
        except Exception as exc:
            print(f"[dataset] GeoLife load failed ({exc}), falling back to synthetic.")

    trajs = load_synthetic_trajectories(num_trajectories=num_synthetic, num_points=num_points)
    print(f"[dataset] Using {len(trajs)} synthetic trajectories.")
    return trajs, "synthetic"
