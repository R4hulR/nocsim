"""Experiment 4: cycle-level latency vs. offered load (8x8).

Compares mesh, torus, the modified torus and one design from the search
frontier of experiment 3 (the best found with <= 208 tiles of wire: 18% more
than the modified torus, 7% less than the torus) under four traffic patterns, using deadlock-free
adaptive routing (minimal adaptive + up*/down* escape, 3 VCs, 4-packet
buffers). Two wire models:

* ``unit``: every link takes 1 cycle (the usual textbook assumption);
* ``wire``: a link of length L takes ceil(L / 2) cycles, so long wraparound
  links cost latency. This is the regime where shortening wires, which is
  the point of the modified torus, should pay off.

Outputs: results/simulation.csv, results/simulation_summary.json,
         results/figures/latency_vs_load.png, results/figures/searched_topology.png
"""

import csv
import math

import matplotlib.pyplot as plt
from common import (DESIGNS, FIGURES, RESULTS, baselines, draw_topology, load_design, rate_grid, save_json,
                    saturation_throughput, style, sweep)

from nocsim import metrics, traffic
from nocsim.simulator import SimConfig
from nocsim.topology import Topology

N = 8
# Hotspot traffic is left out on purpose: the hot router can eject only one
# packet per cycle, which caps every topology at the same ~0.08 load, so it
# says nothing about topology.
PATTERNS = ["uniform", "transpose", "bit_complement", "tornado"]
WIRE_MODELS = ["unit", "wire"]
SEARCHED_BUDGET = 208


def main():
    topos = baselines(N)
    searched_path = DESIGNS / f"frontier_8x8_w{SEARCHED_BUDGET}.json"
    if searched_path.exists():
        d = load_design(searched_path)
        searched = Topology(f"Searched (wire {d.total_wire_length})", d.n, d.edges, d.info)
        topos.append(searched)
        fig, axes = plt.subplots(1, 3, figsize=(13, 4.6))
        for ax, t in zip(axes, (topos[2], searched, topos[1])):
            draw_topology(t, ax)
        fig.tight_layout()
        fig.savefig(FIGURES / "searched_topology.png", dpi=150, bbox_inches="tight")

    jobs, keys = [], []
    for wm in WIRE_MODELS:
        cfg = SimConfig(routing="adaptive", link_latency=wm, seed=7)
        for pat in PATTERNS:
            for t in topos:
                jobs.append((t, pat, cfg, rate_grid(0.025, 1.0)))
                keys.append((wm, pat, t.name))
    curves = dict(zip(keys, sweep(jobs)))

    with open(RESULTS / "simulation.csv", "w", newline="") as f:
        fields = ["wire_model"] + list(next(iter(curves.values()))[0].keys())
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for (wm, _, _), pts in curves.items():
            for p in pts:
                w.writerow({"wire_model": wm, **p})

    # ---- summary: zero-load latency, measured saturation, analytical estimate
    summary = []
    print(f"{'wire':5} {'pattern':15} {'topology':22} {'T0(cyc)':>8} {'sat(sim)':>9} {'estimate':>8}")
    for (wm, pat, name), pts in curves.items():
        topo = next(t for t in topos if t.name == name)
        est = metrics.throughput_estimate(topo, traffic.matrix(pat, N))
        row = {"wire_model": wm, "pattern": pat, "topology": name,
               "zero_load_latency": pts[0]["avg_latency"], "saturation": saturation_throughput(pts),
               "estimate": est, "zero_load_wire": pts[0]["avg_wire"]}
        summary.append(row)
        print(f"{wm:5} {pat:15} {name:22} {row['zero_load_latency']:8.2f} {row['saturation']:9.3f} {est:8.3f}")
    save_json(summary, RESULTS / "simulation_summary.json")

    # ---- figure: rows = wire model, columns = traffic pattern
    fig, axes = plt.subplots(len(WIRE_MODELS), len(PATTERNS), figsize=(4 * len(PATTERNS), 3.4 * len(WIRE_MODELS)))
    for i, wm in enumerate(WIRE_MODELS):
        for j, pat in enumerate(PATTERNS):
            ax = axes[i][j]
            for t in topos:
                pts = [p for p in curves[(wm, pat, t.name)] if not math.isnan(p["avg_latency"])]
                if pts:
                    st = style(t.name)
                    ax.plot([p["offered"] for p in pts], [p["avg_latency"] for p in pts], label=t.name,
                            color=st["color"], marker=st["marker"], ms=3, lw=1.3)
            zero = min(r["zero_load_latency"] for r in summary if r["wire_model"] == wm and r["pattern"] == pat)
            ax.set_ylim(0, 6 * zero)
            ax.set_title(f"{pat}  |  {'1-cycle links' if wm == 'unit' else 'wire-length delays'}", fontsize=9)
            ax.set_xlabel("offered load (packets/node/cycle)", fontsize=8)
            if j == 0:
                ax.set_ylabel("avg latency (cycles)")
            ax.grid(alpha=0.3)
    axes[0][0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIGURES / "latency_vs_load.png", dpi=150, bbox_inches="tight")


if __name__ == "__main__":
    main()
