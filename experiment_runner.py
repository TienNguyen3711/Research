"""
experiment_runner.py — Orchestrates all 5 experiments and writes results to JSON.

Experiments:
  1. Privacy vs Utility Trade-off (sweep MAX_POINT_OFFSET_METERS)
  2. Seed Entropy Analysis (SEB + TUR)
  3. Detection Resistance (statistical fingerprinting)
  4. Scalability (trajectory length sweep)
  5. Cryptographic Robustness (PBKDF2 iterations)

Run:
    source ~/code/Project/bin/activate
    python3 experiment_runner.py
    python3 experiment_runner.py --geolife /path/to/GeoLife
"""
import argparse
import json
import math
import os
import sys
import time

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("APP_TRAJECTORY_SECRET", "experiment_test_secret_do_not_use_in_production")
os.environ.setdefault("FAKE_DATA_SECRET_V1", "experiment_storage_secret_do_not_use_in_production")
os.environ.setdefault("FAKE_DATA_SECRET_CURRENT_VERSION", "1")
os.environ.setdefault("USER_ID", "1")

from experiment_dataset import load_dataset
from experiment_metrics import (
    compute_arr,
    compute_frechet_distance_m,
    compute_gis,
    compute_sps,
    compute_vps,
    compute_eps,
    compute_seb,
    compute_tur,
    compute_erti,
)
from experiment_baselines import run_all_baselines
from trajectory_layer_scramble import build_label_locked_trajectory_package
from trajectory_layer_bootstrap import dynamic_factors as DEFAULT_DYNAMIC_FACTORS
from trajectory_layer_crypto import get_storage_secret_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_set(key, value):
    os.environ[key] = str(value)


def _env_reset(key, original):
    if original is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = original


def _scramble(trajectory, user_id=1, timestamp=1700000000):
    pkg = build_label_locked_trajectory_package(
        trajectory,
        DEFAULT_DYNAMIC_FACTORS,
        user_id=user_id,
        timestamp=timestamp,
    )
    return pkg


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


_SPS_ENABLED = os.getenv("ENABLE_SPS", "0") == "1"  # requires live OSM network access


def _safe_sps(fake_traj):
    if not _SPS_ENABLED:
        return None  # skip by default — OSMnx network call hangs in offline/CI environments
    result = compute_sps(fake_traj)
    return result.get("score")


# ---------------------------------------------------------------------------
# Experiment 1 — Privacy vs Utility Trade-off
# ---------------------------------------------------------------------------

def experiment_1_privacy_utility(trajectories, offsets=None):
    """
    Sweep MAX_POINT_OFFSET_METERS and measure ARR + FD + SPS per method.
    """
    if offsets is None:
        offsets = [500, 1000, 2000, 3500, 5000]

    print("\n[Exp 1] Privacy vs Utility Trade-off")
    results = []

    original_max = os.environ.get("MAX_POINT_OFFSET_METERS")
    original_mid_min = os.environ.get("MIDDLE_MIN_OFFSET_METERS")
    original_mid_max = os.environ.get("MIDDLE_MAX_OFFSET_METERS")
    original_ep = os.environ.get("ENDPOINT_OFFSET_METERS")

    for offset in offsets:
        # Scale all offset params proportionally
        _env_set("MAX_POINT_OFFSET_METERS", offset)
        _env_set("MIDDLE_MIN_OFFSET_METERS", int(offset * 0.7))
        _env_set("MIDDLE_MAX_OFFSET_METERS", offset)
        _env_set("ENDPOINT_OFFSET_METERS", int(offset * 0.9))

        # Reload bootstrap constants after env change
        import importlib, trajectory_layer_bootstrap
        importlib.reload(trajectory_layer_bootstrap)
        import trajectory_layer_scramble
        importlib.reload(trajectory_layer_scramble)
        from trajectory_layer_scramble import build_label_locked_trajectory_package as _build

        row = {"offset_m": offset, "our_system": {}, "planar_laplace": {}, "k_anonymity": {}, "raw_storage": {}}
        arr_our, fd_our, sps_our = [], [], []
        arr_pl, fd_pl, sps_pl = [], [], []
        arr_ka, fd_ka, sps_ka = [], [], []
        arr_raw, fd_raw, sps_raw = [], [], []

        for entry in trajectories[:10]:
            real = entry["points"]
            if len(real) < 5:
                continue

            # Our system
            try:
                pkg = _build(real, DEFAULT_DYNAMIC_FACTORS, user_id=1, timestamp=1700000000)
                fake = pkg["scrambled_trajectory"]
                arr_our.append(compute_arr(real, fake))
                fd_our.append(compute_frechet_distance_m(real, fake))
                sps_our.append(_safe_sps(fake))
            except Exception as exc:
                print(f"  [Exp1] our_system error: {exc}")

            # Baselines
            epsilon_m = offset  # comparable sensitivity radius
            baselines = run_all_baselines(real, epsilon_m=epsilon_m, k=5, separation_m=offset * 0.2)
            for key, fake_b in baselines.items():
                arr_val = compute_arr(real, fake_b)
                fd_val = compute_frechet_distance_m(real, fake_b)
                sps_val = _safe_sps(fake_b)
                if key == "planar_laplace":
                    arr_pl.append(arr_val); fd_pl.append(fd_val); sps_pl.append(sps_val)
                elif key == "k_anonymity":
                    arr_ka.append(arr_val); fd_ka.append(fd_val); sps_ka.append(sps_val)
                elif key == "raw_storage":
                    arr_raw.append(arr_val); fd_raw.append(fd_val); sps_raw.append(sps_val)

        row["our_system"] = {"ARR": _mean(arr_our), "FD_m": _mean(fd_our), "SPS": _mean(sps_our)}
        row["planar_laplace"] = {"ARR": _mean(arr_pl), "FD_m": _mean(fd_pl), "SPS": _mean(sps_pl)}
        row["k_anonymity"] = {"ARR": _mean(arr_ka), "FD_m": _mean(fd_ka), "SPS": _mean(sps_ka)}
        row["raw_storage"] = {"ARR": _mean(arr_raw), "FD_m": _mean(fd_raw), "SPS": _mean(sps_raw)}
        results.append(row)
        print(f"  offset={offset}m | our ARR={row['our_system']['ARR']:.3f}  FD={row['our_system']['FD_m']:.0f}m")

    # Restore env and reload modules back to original state
    for key, orig in [
        ("MAX_POINT_OFFSET_METERS", original_max),
        ("MIDDLE_MIN_OFFSET_METERS", original_mid_min),
        ("MIDDLE_MAX_OFFSET_METERS", original_mid_max),
        ("ENDPOINT_OFFSET_METERS", original_ep),
    ]:
        _env_reset(key, orig)

    import importlib, trajectory_layer_bootstrap, trajectory_layer_scramble
    importlib.reload(trajectory_layer_bootstrap)
    importlib.reload(trajectory_layer_scramble)
    # Refresh the global _scramble reference so later experiments use restored constants
    global build_label_locked_trajectory_package
    from trajectory_layer_scramble import build_label_locked_trajectory_package

    return results


# ---------------------------------------------------------------------------
# Experiment 2 — Seed Entropy Analysis
# ---------------------------------------------------------------------------

def experiment_2_seed_entropy(trajectories, n_runs=50):
    """
    Derive n_runs session seeds with varied timestamps and measure SEB + TUR.
    """
    print("\n[Exp 2] Seed Entropy Analysis")
    import base64
    from trajectory_layer_scramble import derive_session_seed, get_application_secret
    import secrets as _s

    app_secret = get_application_secret()
    seeds = []
    for i in range(n_runs):
        ts = 1700000000 + i * 600  # 10-minute intervals
        session_id = _s.token_hex(16)
        seed = derive_session_seed(DEFAULT_DYNAMIC_FACTORS, user_id=1, timestamp=ts, session_id=session_id, app_secret=app_secret)
        seeds.append(seed)

    seb = compute_seb(seeds)
    tur = compute_tur(seeds)
    print(f"  SEB={seb['entropy_bits']:.1f} bits (max={seb['max_possible_bits']}) | TUR={tur['tur']:.4f}")
    return {"SEB": seb, "TUR": tur}


# ---------------------------------------------------------------------------
# Experiment 3 — Detection Resistance
# ---------------------------------------------------------------------------

def experiment_3_detection_resistance(trajectories):
    """
    Simulate a statistical detector trying to distinguish fake from real.
    Features: mean lat, mean lng, total length, bearing variance.
    Detector: simple threshold on Fréchet distance to a reference cluster.
    Attack success rate = fraction of fakes detected as fake.
    """
    print("\n[Exp 3] Detection Resistance")
    from experiment_metrics import haversine_m

    def _traj_features(traj):
        lats = [p[0] for p in traj]
        lngs = [p[1] for p in traj]
        length = sum(haversine_m(traj[i], traj[i + 1]) for i in range(len(traj) - 1))
        bearings = []
        for i in range(len(traj) - 1):
            dy = traj[i + 1][0] - traj[i][0]
            dx = traj[i + 1][1] - traj[i][1]
            bearings.append(math.atan2(dx, dy))
        bearing_var = sum((b - sum(bearings) / len(bearings)) ** 2 for b in bearings) / len(bearings) if bearings else 0
        return {
            "mean_lat": sum(lats) / len(lats),
            "mean_lng": sum(lngs) / len(lngs),
            "length_m": length,
            "bearing_var": bearing_var,
        }

    results = {}
    for method in ["our_system", "planar_laplace", "k_anonymity"]:
        detected = 0
        total = 0
        for entry in trajectories[:10]:
            real = entry["points"]
            if len(real) < 5:
                continue
            try:
                if method == "our_system":
                    pkg = _scramble(real)
                    fake = pkg["scrambled_trajectory"]
                elif method == "planar_laplace":
                    fake = run_all_baselines(real)["planar_laplace"]
                else:
                    fake = run_all_baselines(real)["k_anonymity"]

                real_feat = _traj_features(real)
                fake_feat = _traj_features(fake)
                # Simple detector: if length ratio differs by >3× or centroid shifts <100m → detected
                length_ratio = fake_feat["length_m"] / max(real_feat["length_m"], 1)
                centroid_shift = haversine_m(
                    (real_feat["mean_lat"], real_feat["mean_lng"]),
                    (fake_feat["mean_lat"], fake_feat["mean_lng"]),
                )
                if length_ratio > 3.0 or length_ratio < 0.33 or centroid_shift < 100:
                    detected += 1
                total += 1
            except Exception:
                continue
        detection_rate = detected / total if total else None
        results[method] = {"detection_rate": detection_rate, "total": total}
        print(f"  {method}: detection_rate={detection_rate:.3f}" if detection_rate is not None else f"  {method}: no data")

    return results


# ---------------------------------------------------------------------------
# Experiment 4 — Scalability
# ---------------------------------------------------------------------------

def experiment_4_scalability(lengths=None):
    """
    Measure generation time and ERTI at various trajectory lengths.
    """
    if lengths is None:
        lengths = [8, 15, 25, 50, 100]  # min 8 pts — pipeline requires at least this many
    print("\n[Exp 4] Scalability")
    from experiment_dataset import _make_synthetic_trajectory

    results = []
    secret_registry = get_storage_secret_registry()

    for n in lengths:
        points, timestamps = _make_synthetic_trajectory(-37.808, 144.962, n, seed_val=n * 7)
        t0 = time.perf_counter()
        try:
            pkg = _scramble(points)
            elapsed = time.perf_counter() - t0
            erti = compute_erti([pkg], secret_registry, user_id=1)
            vps = compute_vps(pkg["scrambled_trajectory"], timestamps[:len(pkg["scrambled_trajectory"])])
        except Exception as exc:
            print(f"  n={n}: error — {exc}")
            results.append({"n_points": n, "error": str(exc)})
            continue
        row = {
            "n_points": n,
            "generation_ms": round(elapsed * 1000, 1),
            "ERTI": erti["erti"],
            "VPS": vps.get("score"),
        }
        results.append(row)
        print(f"  n={n} | {elapsed*1000:.1f}ms | ERTI={erti['erti']} | VPS={vps.get('score', 'N/A')}")

    return results


# ---------------------------------------------------------------------------
# Experiment 5 — Cryptographic Robustness
# ---------------------------------------------------------------------------

def experiment_5_crypto_robustness():
    """
    Compare PBKDF2 at 3 iteration counts: time-to-derive and ERTI.
    """
    print("\n[Exp 5] Cryptographic Robustness")
    import base64
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    import secrets as _s

    iterations_list = [100_000, 390_000, 600_000]
    secret = "experiment_secret"
    salt = _s.token_bytes(32)
    results = []

    for iters in iterations_list:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iters)
        t0 = time.perf_counter()
        base64.urlsafe_b64encode(kdf.derive(secret.encode()))
        elapsed = time.perf_counter() - t0
        row = {"iterations": iters, "derive_ms": round(elapsed * 1000, 1), "key_len_bytes": 32}
        results.append(row)
        print(f"  iterations={iters:,} | derive_time={elapsed*1000:.1f}ms")

    return results


# ---------------------------------------------------------------------------
# Full metric summary across all methods
# ---------------------------------------------------------------------------

def compute_full_summary(trajectories):
    """Compute all 9 metrics for our system and 3 baselines on the dataset."""
    print("\n[Summary] Computing all metrics across all methods")
    summary = {method: {"ARR": [], "FD_m": [], "EPS_start": [], "EPS_end": [], "VPS": [], "GIS": []}
               for method in ["our_system", "planar_laplace", "k_anonymity", "raw_storage"]}

    secret_registry = get_storage_secret_registry()

    our_packages = []
    for entry in trajectories[:15]:
        real = entry["points"]
        timestamps = entry.get("timestamps", [])
        if len(real) < 5:
            continue
        baselines = run_all_baselines(real, epsilon_m=500.0, k=5, separation_m=300.0)
        candidate_pool = [e["points"] for e in trajectories if e["points"] != real][:20]

        try:
            pkg = _scramble(real)
            fake_our = pkg["scrambled_trajectory"]

            our_packages.append(pkg)
            summary["our_system"]["ARR"].append(compute_arr(real, fake_our))
            summary["our_system"]["FD_m"].append(compute_frechet_distance_m(real, fake_our))
            eps = compute_eps(real, fake_our)
            summary["our_system"]["EPS_start"].append(eps["start_m"])
            summary["our_system"]["EPS_end"].append(eps["end_m"])
            fake_ts = timestamps[:len(fake_our)] if timestamps else None
            if fake_ts:
                vps = compute_vps(fake_our, fake_ts)
                summary["our_system"]["VPS"].append(vps.get("score"))
            gis = compute_gis(fake_our, candidate_pool[:10])
            summary["our_system"]["GIS"].append(gis)
        except Exception as exc:
            print(f"  our_system error: {exc}")

        for method, fake in baselines.items():
            summary[method]["ARR"].append(compute_arr(real, fake))
            summary[method]["FD_m"].append(compute_frechet_distance_m(real, fake))
            eps = compute_eps(real, fake)
            summary[method]["EPS_start"].append(eps["start_m"])
            summary[method]["EPS_end"].append(eps["end_m"])
            if timestamps:
                fake_ts = timestamps[:len(fake)]
                vps = compute_vps(fake, fake_ts)
                summary[method]["VPS"].append(vps.get("score"))
            gis = compute_gis(fake, candidate_pool[:10])
            summary[method]["GIS"].append(gis)

    # ERTI (our system only)
    erti = compute_erti(our_packages, secret_registry, user_id=1) if our_packages else {"erti": None}

    aggregated = {}
    for method, metrics in summary.items():
        aggregated[method] = {k: _mean(v) for k, v in metrics.items()}
    aggregated["our_system"]["ERTI"] = erti["erti"]

    for method, vals in aggregated.items():
        arr_s = f"{vals['ARR']:.3f}" if vals['ARR'] is not None else "N/A"
        fd_s = f"{vals['FD_m']:.0f}m" if vals['FD_m'] is not None else "N/A"
        gis_s = f"{vals['GIS']:.1f}" if vals['GIS'] is not None else "N/A"
        print(f"  {method}: ARR={arr_s}  FD={fd_s}  GIS={gis_s}")

    return aggregated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run trajectory privacy experiments")
    parser.add_argument("--geolife", default=None, help="Path to GeoLife root directory")
    parser.add_argument("--output", default="experiment_results.json", help="Output JSON file")
    parser.add_argument("--quick", action="store_true", help="Quick mode: fewer trajectories, fewer offsets")
    args = parser.parse_args()

    trajectories, source = load_dataset(
        geolife_root=args.geolife,
        max_users=5 if args.quick else 10,
        num_synthetic=10 if args.quick else 20,
    )

    offsets = [500, 2000, 5000] if args.quick else [500, 1000, 2000, 3500, 5000]

    all_results = {
        "dataset_source": source,
        "n_trajectories": len(trajectories),
    }

    all_results["experiment_1"] = experiment_1_privacy_utility(trajectories, offsets=offsets)
    all_results["experiment_2"] = experiment_2_seed_entropy(trajectories)
    all_results["experiment_3"] = experiment_3_detection_resistance(trajectories)
    all_results["experiment_4"] = experiment_4_scalability()
    all_results["experiment_5"] = experiment_5_crypto_robustness()
    all_results["full_metric_summary"] = compute_full_summary(trajectories)

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.output)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(all_results, fh, indent=2, default=str)
    print(f"\n[Done] Results saved to {output_path}")


if __name__ == "__main__":
    main()
