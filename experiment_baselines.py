"""
experiment_baselines.py — Three baseline methods for comparison.

Baseline 1: Planar Laplace Noise (Andrés et al., 2013)
Baseline 2: k-Anonymity Dummy Generation
Baseline 3: Raw Storage (no protection — lower bound)
"""
import math
import os
import secrets as _secrets

METERS_PER_DEGREE = 111_000


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _haversine_m(p1, p2):
    R = 6_371_000
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _random_uniform():
    """Cryptographically seeded float in [0, 1)."""
    return _secrets.randbelow(2 ** 32) / 2 ** 32


def _sample_planar_laplace(epsilon_m):
    """
    Sample displacement (dr_lat_deg, dr_lng_deg) from the Planar Laplace
    distribution with privacy parameter epsilon (in 1/metres).

    The planar Laplace density is:
        f(r) = (epsilon² / 2π) * r * exp(-epsilon * r)

    Sampling via inverse CDF of the marginal radial distribution and a
    uniform angle, following Andrés et al. (2013).

    epsilon_m: sensitivity radius in metres. Smaller → more noise.
    """
    # Convert sensitivity radius to the epsilon parameter (1/m unit)
    epsilon = 1.0 / max(epsilon_m, 1.0)

    # Sample angle uniformly in [0, 2π)
    theta = _random_uniform() * 2 * math.pi

    # Sample radius from marginal: F(r) = 1 - (1 + epsilon*r) * exp(-epsilon*r)
    # Invert via the Lambert W function approximation or rejection sampling.
    # We use the closed-form inverse: r = -1/epsilon * (W(-u * e^{-1}) + 1)
    # where u is uniform in [0,1), approximated numerically.
    u = _random_uniform()
    # Newton iteration for: (1 + t) * exp(-t) = u  →  t = epsilon * r
    t = 1.0
    for _ in range(30):
        f_t = (1.0 + t) * math.exp(-t) - u
        df_t = -t * math.exp(-t)
        if abs(df_t) < 1e-15:
            break
        t -= f_t / df_t
        t = max(t, 1e-9)
    r_m = t / epsilon  # radius in metres

    # Convert polar offset to lat/lng degrees
    dr_lat = (r_m * math.cos(theta)) / METERS_PER_DEGREE
    dr_lng = (r_m * math.sin(theta)) / METERS_PER_DEGREE
    return dr_lat, dr_lng


# ---------------------------------------------------------------------------
# Baseline 1 — Planar Laplace Noise
# ---------------------------------------------------------------------------

def apply_planar_laplace(trajectory, epsilon_m):
    """
    Add independent Planar Laplace noise to each point.

    epsilon_m: sensitivity radius in metres. Higher → less noise (less privacy).
    Returns noisy trajectory as list of (lat, lng).
    """
    noisy = []
    for lat, lng in trajectory:
        dr_lat, dr_lng = _sample_planar_laplace(epsilon_m)
        noisy.append((lat + dr_lat, lng + dr_lng))
    return noisy


def planar_laplace_trajectory(real_trajectory, epsilon_m=500.0):
    """
    Full baseline: apply Planar Laplace to every point independently.
    Endpoints are NOT preserved (standard geo-indistinguishability model).
    """
    return apply_planar_laplace(real_trajectory, epsilon_m)


# ---------------------------------------------------------------------------
# Baseline 2 — k-Anonymity Dummy Generation
# ---------------------------------------------------------------------------

def _bearing_offset(lat, lng, bearing_deg, distance_m):
    """Move a point by distance_m in bearing_deg direction."""
    d = distance_m / METERS_PER_DEGREE
    bearing_rad = math.radians(bearing_deg)
    return (lat + d * math.cos(bearing_rad), lng + d * math.sin(bearing_rad))


def generate_k_anonymity_dummies(real_trajectory, k=5, separation_m=800.0):
    """
    Generate k-1 dummy trajectories alongside the real one.
    Each dummy follows the same shape as the real trajectory, shifted
    by a fixed lateral offset in a unique direction.

    Returns list of k trajectories (index 0 = real, indices 1..k-1 = dummies).
    """
    all_trajectories = [list(real_trajectory)]
    n = len(real_trajectory)

    for dummy_idx in range(1, k):
        angle = (dummy_idx * 360.0 / (k - 1))
        dummy = []
        for i, (lat, lng) in enumerate(real_trajectory):
            # Vary angle slightly per point to avoid perfectly parallel tracks
            point_angle = angle + (i / max(1, n - 1) - 0.5) * 15.0
            new_lat, new_lng = _bearing_offset(lat, lng, point_angle, separation_m)
            dummy.append((new_lat, new_lng))
        all_trajectories.append(dummy)

    return all_trajectories


def k_anonymity_trajectory(real_trajectory, k=5, separation_m=800.0):
    """
    Return the single "protected" output for k-anonymity baseline:
    the real trajectory with k-1 dummies generated alongside it.
    The caller receives all k trajectories; the "fake" for evaluation
    purposes is one of the dummies (index 1).
    """
    all_trajs = generate_k_anonymity_dummies(real_trajectory, k=k, separation_m=separation_m)
    return all_trajs[1]  # first dummy as the "published" fake


# ---------------------------------------------------------------------------
# Baseline 3 — Raw Storage (No Protection)
# ---------------------------------------------------------------------------

def raw_storage_trajectory(real_trajectory):
    """
    No transformation — return the real trajectory as-is.
    Represents the lower bound on privacy (ARR = 1.0 by definition).
    """
    return list(real_trajectory)


# ---------------------------------------------------------------------------
# Unified baseline runner
# ---------------------------------------------------------------------------

def run_all_baselines(real_trajectory, epsilon_m=500.0, k=5, separation_m=800.0):
    """
    Run all three baselines and return results dict.
    """
    return {
        "planar_laplace": planar_laplace_trajectory(real_trajectory, epsilon_m=epsilon_m),
        "k_anonymity": k_anonymity_trajectory(real_trajectory, k=k, separation_m=separation_m),
        "raw_storage": raw_storage_trajectory(real_trajectory),
    }
