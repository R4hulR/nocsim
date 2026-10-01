"""Experiment 1: analytical comparison of mesh, torus and modified torus.

This is the comparison the thesis set out to make (hop count, latency), now
done for every grid size from 5x5 to 10x10, with two metrics the thesis
didn't have: physical wire length and the channel-load throughput estimate.

Outputs: results/static_metrics.csv, results/figures/static_metrics.png,
         results/figures/topologies.png
"""

import csv

import matplotlib.pyplot as plt
from common import FIGURES, RESULTS, baselines, draw_topology, style

from nocsim import metrics, traffic

SIZES = range(5, 11)


def main():
    rows = []
    for n in SIZES:
        for topo in baselines(n):
            s = metrics.summary(topo)
            # Throughput estimates for the adversarial permutations as well.
            for pat in ("transpose", "bit_complement", "tornado"):
                s[f"throughput_est_{pat}"] = metrics.throughput_estimate(topo, traffic.matrix(pat, n))
            rows.append(s)

    with open(RESULTS / "static_metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- figure: four metrics vs grid size
    panels = [
        ("avg_hops", "Average hop count", "lower is better"),
        ("total_wire", "Total wire length (tile pitches)", "lower is better"),
        ("throughput_est", "Throughput estimate, uniform\n(packets/node/cycle)", "higher is better"),
        ("wire_per_packet", "Wire crossed per packet\n(energy proxy, tiles)", "lower is better"),
    ]
    fig, axes = plt.subplots(1, 4, figsize=(16, 3.8))
    for ax, (key, label, note) in zip(axes, panels):
        for name in ("Mesh", "Torus", "Modified torus"):
            pts = [(r["n"], r[key]) for r in rows if r["topology"] == name]
            ax.plot(*zip(*pts), label=name, **style(name))
        ax.set_xlabel("grid side n  (n x n routers)")
        ax.set_title(f"{label}\n({note})", fontsize=10)
        ax.grid(alpha=0.3)
    axes[0].legend()
    fig.tight_layout()
    fig.savefig(FIGURES / "static_metrics.png", dpi=150, bbox_inches="tight")

    # ---- figure: what the three topologies look like at 8x8
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.6))
    for ax, topo in zip(axes, baselines(8)):
        draw_topology(topo, ax)
    fig.text(0.5, 0.01, "Straight grey: mesh links.  Edge stubs: full-length wraparound links.  "
             "Arcs: shorter links folded along one edge.  Colour = wire length.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(FIGURES / "topologies.png", dpi=150, bbox_inches="tight")

    # ---- console summary
    print(f"{'n':>3} {'topology':16} {'hops':>6} {'diam':>5} {'wire':>5} {'thr_uni':>8} {'thr_tr':>7} {'thr_bc':>7} {'thr_to':>7}")
    for r in rows:
        print(
            f"{r['n']:>3} {r['topology']:16} {r['avg_hops']:6.3f} {r['diameter']:5d} {r['total_wire']:5d} "
            f"{r['throughput_est']:8.3f} {r['throughput_est_transpose']:7.3f} {r['throughput_est_bit_complement']:7.3f} "
            f"{r['throughput_est_tornado']:7.3f}"
        )


if __name__ == "__main__":
    main()
