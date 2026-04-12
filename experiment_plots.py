"""
experiment_plots.py — Generate all research charts from experiment_results.json.

Outputs (saved to experiment_figures/):
  fig1_privacy_utility_tradeoff.png  — ARR vs FD for all methods across offsets
  fig2_arr_by_offset.png             — ARR per method per offset (bar chart)
  fig3_fd_by_offset.png              — FD per method per offset (line chart)
  fig4_seed_entropy.png              — SEB entropy bar + TUR annotation
  fig5_scalability.png               — Generation time & VPS vs trajectory length
  fig6_crypto_robustness.png         — PBKDF2 derive time vs iterations
  fig7_metric_summary_table.png      — Full metric summary table (all 9 metrics)
  fig8_detection_resistance.png      — Detection rate comparison

Run:
    source ~/code/Project/bin/activate
    python3 experiment_plots.py
    python3 experiment_plots.py --results path/to/experiment_results.json
"""
import argparse
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

FIGURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiment_figures")
os.makedirs(FIGURE_DIR, exist_ok=True)

METHOD_LABELS = {
    "our_system": "Our System",
    "planar_laplace": "Planar Laplace",
    "k_anonymity": "k-Anonymity",
    "raw_storage": "Raw Storage",
}
METHOD_COLORS = {
    "our_system": "#1f77b4",
    "planar_laplace": "#ff7f0e",
    "k_anonymity": "#2ca02c",
    "raw_storage": "#d62728",
}
METHOD_MARKERS = {
    "our_system": "o",
    "planar_laplace": "s",
    "k_anonymity": "^",
    "raw_storage": "D",
}

METHODS = ["our_system", "planar_laplace", "k_anonymity", "raw_storage"]


def _savefig(fig, name):
    path = os.path.join(FIGURE_DIR, name)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")
    return path


# ---------------------------------------------------------------------------
# Fig 1 — Privacy-Utility Trade-off scatter (ARR vs FD)
# ---------------------------------------------------------------------------

def plot_privacy_utility_tradeoff(exp1_data):
    fig, ax = plt.subplots(figsize=(8, 6))
    for method in METHODS:
        arr_vals = [row[method]["ARR"] for row in exp1_data if row[method].get("ARR") is not None]
        fd_vals = [row[method]["FD_m"] for row in exp1_data if row[method].get("FD_m") is not None]
        if not arr_vals:
            continue
        ax.plot(arr_vals, fd_vals,
                marker=METHOD_MARKERS[method],
                color=METHOD_COLORS[method],
                label=METHOD_LABELS[method],
                linewidth=1.8, markersize=8)
        # Annotate offset values
        for i, row in enumerate(exp1_data):
            if row[method].get("ARR") is not None and row[method].get("FD_m") is not None:
                ax.annotate(f"{row['offset_m']}m",
                            (row[method]["ARR"], row[method]["FD_m"]),
                            textcoords="offset points", xytext=(4, 4), fontsize=7, color="grey")

    # Target zone shading
    ax.axvspan(0, 0.05, alpha=0.08, color="green", label="ARR target (≤0.05)")
    ax.axhspan(300, 2000, alpha=0.08, color="blue", label="FD target zone (300–2000m)")

    ax.set_xlabel("Adversarial Recovery Rate (ARR) ↓ better", fontsize=12)
    ax.set_ylabel("Fréchet Distance (m) ↑ better", fontsize=12)
    ax.set_title("Privacy-Utility Trade-off Curve", fontsize=14, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    return _savefig(fig, "fig1_privacy_utility_tradeoff.png")


# ---------------------------------------------------------------------------
# Fig 2 — ARR by offset (grouped bar)
# ---------------------------------------------------------------------------

def plot_arr_by_offset(exp1_data):
    offsets = [row["offset_m"] for row in exp1_data]
    x = np.arange(len(offsets))
    width = 0.2
    fig, ax = plt.subplots(figsize=(9, 5))

    for i, method in enumerate(METHODS):
        arr_vals = [row[method].get("ARR", 0) or 0 for row in exp1_data]
        ax.bar(x + i * width, arr_vals, width, label=METHOD_LABELS[method],
               color=METHOD_COLORS[method], alpha=0.85)

    ax.axhline(0.05, color="red", linestyle="--", linewidth=1.2, label="Target ARR = 0.05")
    ax.set_xticks(x + width * 1.5)
    ax.set_xticklabels([f"{o}m" for o in offsets])
    ax.set_xlabel("Max Point Offset (meters)", fontsize=12)
    ax.set_ylabel("ARR (lower = more private)", fontsize=12)
    ax.set_title("Adversarial Recovery Rate by Offset Level", fontsize=14, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    return _savefig(fig, "fig2_arr_by_offset.png")


# ---------------------------------------------------------------------------
# Fig 3 — FD by offset (line chart)
# ---------------------------------------------------------------------------

def plot_fd_by_offset(exp1_data):
    offsets = [row["offset_m"] for row in exp1_data]
    fig, ax = plt.subplots(figsize=(9, 5))

    for method in METHODS:
        fd_vals = [row[method].get("FD_m") or 0 for row in exp1_data]
        ax.plot(offsets, fd_vals, marker=METHOD_MARKERS[method],
                color=METHOD_COLORS[method], label=METHOD_LABELS[method],
                linewidth=2, markersize=8)

    ax.axhspan(300, 2000, alpha=0.08, color="blue")
    ax.text(offsets[0], 1100, "Target zone\n300–2000 m", color="blue", fontsize=9, alpha=0.7)
    ax.set_xlabel("Max Point Offset (meters)", fontsize=12)
    ax.set_ylabel("Fréchet Distance (m)", fontsize=12)
    ax.set_title("Fréchet Distance vs Offset Level", fontsize=14, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    return _savefig(fig, "fig3_fd_by_offset.png")


# ---------------------------------------------------------------------------
# Fig 4 — Seed Entropy (SEB + TUR)
# ---------------------------------------------------------------------------

def plot_seed_entropy(exp2_data):
    seb = exp2_data.get("SEB", {})
    tur = exp2_data.get("TUR", {})

    entropy_bits = seb.get("entropy_bits", 0)
    max_bits = seb.get("max_possible_bits", 256)
    tur_val = tur.get("tur", 0)

    fig, axes = plt.subplots(1, 2, figsize=(10, 5))

    # SEB bar
    ax = axes[0]
    bars = ax.bar(["Actual Entropy", "Max Possible"], [entropy_bits, max_bits],
                  color=["#1f77b4", "#aec7e8"], edgecolor="black", linewidth=0.8)
    ax.axhline(128, color="red", linestyle="--", linewidth=1.5, label="Min target (128 bits)")
    ax.set_ylabel("Entropy (bits)", fontsize=12)
    ax.set_title("Seed Entropy Bits (SEB)", fontsize=13, fontweight="bold")
    ax.legend(fontsize=10)
    for bar, val in zip(bars, [entropy_bits, max_bits]):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 1, f"{val:.1f}", ha="center", fontsize=10)

    # TUR gauge
    ax = axes[1]
    theta = np.linspace(0, 2 * np.pi, 100)
    ax.plot(np.cos(theta), np.sin(theta), color="lightgrey", linewidth=1)
    angle = (1 - tur_val) * 2 * np.pi
    ax.fill_between(
        np.linspace(0, angle, 100),
        0,
        0,
    )
    wedge = plt.matplotlib.patches.Wedge((0, 0), 0.9, 0, tur_val * 360,
                                          facecolor="#2ca02c", alpha=0.7)
    ax.add_patch(wedge)
    ax.set_xlim(-1.1, 1.1)
    ax.set_ylim(-1.1, 1.1)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.text(0, 0, f"{tur_val:.4f}", ha="center", va="center", fontsize=20, fontweight="bold")
    ax.text(0, -1.05, f"TUR  (target = 1.0)\nn_seeds = {tur.get('total', 0)}",
            ha="center", fontsize=11)
    ax.set_title("Temporal Uniqueness Rate (TUR)", fontsize=13, fontweight="bold")

    fig.tight_layout()
    return _savefig(fig, "fig4_seed_entropy.png")


# ---------------------------------------------------------------------------
# Fig 5 — Scalability
# ---------------------------------------------------------------------------

def plot_scalability(exp4_data):
    lengths = [row["n_points"] for row in exp4_data if "error" not in row]
    times = [row["generation_ms"] for row in exp4_data if "error" not in row]
    vps_vals = [row.get("VPS") or 0 for row in exp4_data if "error" not in row]

    fig, ax1 = plt.subplots(figsize=(9, 5))
    color_time = "#1f77b4"
    color_vps = "#ff7f0e"

    ax1.plot(lengths, times, marker="o", color=color_time, linewidth=2, markersize=8, label="Generation Time (ms)")
    ax1.set_xlabel("Trajectory Length (points)", fontsize=12)
    ax1.set_ylabel("Generation Time (ms)", color=color_time, fontsize=12)
    ax1.tick_params(axis="y", labelcolor=color_time)

    ax2 = ax1.twinx()
    ax2.plot(lengths, vps_vals, marker="s", color=color_vps, linewidth=2, markersize=8,
             linestyle="--", label="VPS")
    ax2.axhline(0.95, color=color_vps, linestyle=":", linewidth=1, alpha=0.7)
    ax2.set_ylabel("Velocity Plausibility Score (VPS)", color=color_vps, fontsize=12)
    ax2.tick_params(axis="y", labelcolor=color_vps)
    ax2.set_ylim(0, 1.1)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, fontsize=10)
    ax1.set_title("Scalability: Generation Time & VPS vs Trajectory Length", fontsize=13, fontweight="bold")
    ax1.grid(True, alpha=0.3)
    return _savefig(fig, "fig5_scalability.png")


# ---------------------------------------------------------------------------
# Fig 6 — Crypto Robustness
# ---------------------------------------------------------------------------

def plot_crypto_robustness(exp5_data):
    iters = [row["iterations"] for row in exp5_data]
    times = [row["derive_ms"] for row in exp5_data]

    fig, ax = plt.subplots(figsize=(7, 5))
    bars = ax.bar([f"{i:,}" for i in iters], times,
                  color=["#aec7e8", "#ffbb78", "#1f77b4"],
                  edgecolor="black", linewidth=0.8)
    ax.set_xlabel("PBKDF2 Iterations", fontsize=12)
    ax.set_ylabel("Key Derivation Time (ms)", fontsize=12)
    ax.set_title("PBKDF2 Key Derivation Time vs Iteration Count", fontsize=13, fontweight="bold")
    for bar, val in zip(bars, times):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.5, f"{val:.1f}ms",
                ha="center", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    # Annotate OWASP recommendation
    ax.annotate("OWASP 2024\nrecommendation",
                xy=(2, times[-1]), xytext=(1.5, times[-1] * 0.6),
                arrowprops=dict(arrowstyle="->", color="red"),
                color="red", fontsize=9)
    return _savefig(fig, "fig6_crypto_robustness.png")


# ---------------------------------------------------------------------------
# Fig 7 — Full Metric Summary Table
# ---------------------------------------------------------------------------

def plot_metric_summary_table(summary_data):
    metrics = ["ARR", "FD_m", "GIS", "VPS", "EPS_start", "EPS_end", "ERTI"]
    methods_shown = ["our_system", "planar_laplace", "k_anonymity", "raw_storage"]
    col_labels = [METHOD_LABELS[m] for m in methods_shown]
    row_labels = ["ARR ↓", "FD (m) ↑", "GIS ↑", "VPS ↑", "EPS start (m)", "EPS end (m)", "ERTI ↑"]
    targets = ["≤0.05", "300–2000", "≥10", "≥0.95", "N/A*", "N/A*", "=1.0"]

    cell_data = []
    for metric in metrics:
        row = []
        for method in methods_shown:
            val = summary_data.get(method, {}).get(metric)
            if val is None:
                row.append("—")
            elif metric == "FD_m":
                row.append(f"{val:.0f}")
            elif metric in ("EPS_start", "EPS_end"):
                row.append(f"{val:.0f}")
            elif isinstance(val, float):
                row.append(f"{val:.3f}")
            else:
                row.append(str(val))
        cell_data.append(row)

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.axis("off")

    all_cols = ["Metric", "Target"] + col_labels
    all_data = [[row_labels[i], targets[i]] + cell_data[i] for i in range(len(metrics))]

    table = ax.table(
        cellText=all_data,
        colLabels=all_cols,
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 2.0)

    # Header styling
    for j in range(len(all_cols)):
        table[0, j].set_facecolor("#2c3e50")
        table[0, j].set_text_props(color="white", fontweight="bold")

    # Highlight "Our System" column
    our_col = all_cols.index("Our System")
    for i in range(1, len(metrics) + 1):
        table[i, our_col].set_facecolor("#dbeafe")

    ax.set_title("Full Metric Summary — All Methods", fontsize=14, fontweight="bold", pad=20)
    fig.text(0.5, 0.01, "* EPS measures endpoint offset distance; our system intentionally offsets endpoints (target: offset is applied).",
             ha="center", fontsize=8, color="grey")
    return _savefig(fig, "fig7_metric_summary_table.png")


# ---------------------------------------------------------------------------
# Fig 8 — Detection Resistance
# ---------------------------------------------------------------------------

def plot_detection_resistance(exp3_data):
    methods_shown = ["our_system", "planar_laplace", "k_anonymity"]
    detection_rates = [exp3_data.get(m, {}).get("detection_rate") or 0 for m in methods_shown]
    labels = [METHOD_LABELS[m] for m in methods_shown]
    colors = [METHOD_COLORS[m] for m in methods_shown]

    fig, ax = plt.subplots(figsize=(7, 5))
    bars = ax.bar(labels, detection_rates, color=colors, edgecolor="black", linewidth=0.8, alpha=0.85)
    ax.axhline(0.5, color="red", linestyle="--", linewidth=1.2, label="50% chance (random guess)")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Statistical Detection Rate (lower = harder to detect)", fontsize=11)
    ax.set_title("Detection Resistance — Statistical Fingerprinting Attack", fontsize=13, fontweight="bold")
    for bar, val in zip(bars, detection_rates):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.01, f"{val:.3f}",
                ha="center", fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    return _savefig(fig, "fig8_detection_resistance.png")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate experiment plots")
    parser.add_argument("--results", default="experiment_results.json")
    args = parser.parse_args()

    results_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.results)
    if not os.path.exists(results_path):
        print(f"Results file not found: {results_path}")
        print("Run experiment_runner.py first.")
        sys.exit(1)

    with open(results_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    print(f"[Plots] Loading results: {len(data.get('experiment_1', []))} offset levels, "
          f"source={data.get('dataset_source')}")

    saved = []
    saved.append(plot_privacy_utility_tradeoff(data["experiment_1"]))
    saved.append(plot_arr_by_offset(data["experiment_1"]))
    saved.append(plot_fd_by_offset(data["experiment_1"]))
    saved.append(plot_seed_entropy(data["experiment_2"]))
    saved.append(plot_scalability(data["experiment_4"]))
    saved.append(plot_crypto_robustness(data["experiment_5"]))
    saved.append(plot_metric_summary_table(data["full_metric_summary"]))
    saved.append(plot_detection_resistance(data["experiment_3"]))

    print(f"\n[Done] {len(saved)} figures saved to {FIGURE_DIR}/")


if __name__ == "__main__":
    main()
