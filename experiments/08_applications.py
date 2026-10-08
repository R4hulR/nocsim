"""Experiment 8: PARSEC application traces on every design (8x8, 22 nm).

Each Netrace trace (converted with tools/netrace_dump; first 250k packets of
the region of interest) is replayed with its dependencies on six designs:
mesh, torus, folded torus, modified torus, and the two searched designs.

Two timing regimes (see nocsim.power):
* pipelined: every network runs at 2 GHz; a link of length L takes
  ceil(L / reach) cycles;
* global:    each network runs at the highest clock its longest link allows.

The traces were captured with 2 GHz cores, so compute gaps are converted to
real time with that clock and then to cycles of each network's own clock,
which keeps networks at different frequencies comparable. ``speedup``
additionally compresses all compute gaps (1x = as recorded; 10x and 50x
emulate faster cores and heavier network load).

Router model: 3 VCs x 8-flit buffers, virtual cut-through, and a 2-cycle VC
turnaround so that throughput matches BookSim (experiment 5). Packets are
1 flit (8 B control) or 5 flits (72 B data) at 16 B/flit.

Usage: python experiments/08_applications.py --traces <dir with *_64c_*.txt>
Outputs: results/applications.csv, results/applications_summary.json,
         results/figures/applications.png
"""

import argparse
import csv
import json
import math
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from common import DESIGNS, FIGURES, RESULTS, WORKERS, save_json, style

from nocsim.power import TechModel
from nocsim.simulator import SimConfig, simulate_trace
from nocsim.topology import Topology, folded_torus, mesh, modified_torus, torus
from nocsim.traces import TraceSource, load_netrace_dump

N = 8
CORE_HZ = 2e9  # clock of the cores the traces were captured on
FIXED_HZ = 2e9  # network clock in the pipelined regime
TILE_MM = 1.5
SPEEDUPS = (1, 10, 50)
REGIMES = ("pipelined", "global")


def designs():
    out = [mesh(N), torus(N), folded_torus(N), modified_torus(N)]
    for f, name in (("maxlink_8x8_L5.json", "Searched (links <= 5)"), ("frontier_8x8_w208.json", "Searched (wire <= 208)")):
        d = Topology.from_dict(json.loads((DESIGNS / f).read_text()))
        out.append(Topology(name, d.n, d.edges, d.info))
    return out


def run(args):
    trace_path, design_idx, regime, speedup, tile_mm, esc_return = args
    topo = designs()[design_idx]
    tm = TechModel.from_dsent(tile_mm=tile_mm, freq_hz=FIXED_HZ)
    _, pkts = load_netrace_dump(trace_path)
    if regime == "pipelined":
        f_net = FIXED_HZ
        cfg = SimConfig(buffer_depth=8, vc_turnaround=2, link_latency="wire", wire_reach=tm.reach_tiles(FIXED_HZ),
                        escape_return=esc_return)
    else:
        f_net = tm.max_frequency(topo)
        cfg = SimConfig(buffer_depth=8, vc_turnaround=2, link_latency="unit", escape_return=esc_return)
    # Gaps are recorded in core cycles; express them in network cycles.
    src = TraceSource(pkts, speedup=speedup * CORE_HZ / f_net)
    s = simulate_trace(topo, src, cfg)
    e = tm.energy(topo, s["router_flits"], s["link_flit_tiles"], s["completion"], freq_hz=f_net,
                  per_router_flits=s["per_router_flits"])
    bench = Path(trace_path).stem.split("_64c")[0]
    return {"benchmark": bench, "design": topo.name, "regime": regime, "speedup": speedup, "tile_mm": tile_mm,
            "f_net_GHz": f_net / 1e9,
            "runtime_us": s["completion"] / f_net * 1e6, "avg_latency_ns": s["avg_latency"] / f_net * 1e9,
            "avg_hops": s["avg_hops"], "energy_uJ": e["total_J"] * 1e6,
            "dynamic_uJ": (e["router_dynamic_J"] + e["link_dynamic_J"]) * 1e6,
            "static_uJ": (e["clock_J"] + e["leakage_J"]) * 1e6,
            "deadlocked": s["deadlocked"], "delivered": s["delivered"], "packets": s["packets"]}


def geomean(xs):
    return float(math.exp(sum(math.log(x) for x in xs) / len(xs)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", required=True, help="directory of netrace_dump outputs (*_64c_*.txt)")
    ap.add_argument("--tile-mm", type=float, default=TILE_MM, help="router pitch (sensitivity study)")
    ap.add_argument("--speedups", default=",".join(map(str, SPEEDUPS)))
    ap.add_argument("--no-escape-return", action="store_true",
                    help="packets that enter the escape VC stay there (robustness check)")
    args = ap.parse_args()
    speedups = tuple(int(x) for x in args.speedups.split(","))
    suffix = "" if args.tile_mm == TILE_MM else f"_tile{args.tile_mm:g}mm"
    if args.no_escape_return:
        suffix += "_noescapereturn"
    traces = sorted(str(p) for p in Path(args.traces).glob("*_64c_*.txt"))
    names = [t.name for t in designs()]
    jobs = [(t, d, r, sp, args.tile_mm, not args.no_escape_return) for t in traces for d in range(len(names)) for r in REGIMES for sp in speedups]
    with ProcessPoolExecutor(WORKERS) as ex:
        rows = list(ex.map(run, jobs))
    with open(RESULTS / f"applications{suffix}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    bad = [r for r in rows if r["deadlocked"] or r["delivered"] != r["packets"]]
    print(f"{len(rows)} runs, {len(bad)} incomplete/deadlocked")

    # Normalise to the mesh per (benchmark, regime, speedup); geometric mean over benchmarks.
    summary = {}
    benches = sorted({r["benchmark"] for r in rows})
    for regime in REGIMES:
        for sp in speedups:
            key = f"{regime}_x{sp}"
            summary[key] = {}
            for name in names:
                ratios = {m: [] for m in ("runtime_us", "avg_latency_ns", "energy_uJ", "dynamic_uJ")}
                for b in benches:
                    base = next(r for r in rows if (r["benchmark"], r["design"], r["regime"], r["speedup"]) == (b, "Mesh", regime, sp))
                    cur = next(r for r in rows if (r["benchmark"], r["design"], r["regime"], r["speedup"]) == (b, name, regime, sp))
                    for m in ratios:
                        ratios[m].append(cur[m] / base[m])
                g = {m: geomean(v) for m, v in ratios.items()}
                g["edp"] = g["runtime_us"] * g["energy_uJ"]
                summary[key][name] = g
    save_json({"benchmarks": benches, "tile_mm": args.tile_mm, "normalised_to_mesh_geomean": summary},
              RESULTS / f"applications_summary{suffix}.json")

    for key, vals in summary.items():
        print(f"\n{key}  (geomean over {len(benches)} benchmarks, relative to mesh; lower is better)")
        print(f"  {'design':24} {'runtime':>8} {'latency':>8} {'energy':>8} {'dyn.E':>8} {'EDP':>8}")
        for name, g in vals.items():
            print(f"  {name:24} {g['runtime_us']:8.3f} {g['avg_latency_ns']:8.3f} {g['energy_uJ']:8.3f} "
                  f"{g['dynamic_uJ']:8.3f} {g['edp']:8.3f}")

    if suffix or 10 not in speedups:
        return  # the figure is drawn for the main configuration only
    # Figure: runtime / latency / energy relative to mesh at 10x, both regimes.
    fig, axes = plt.subplots(2, 3, figsize=(15, 7))
    for i, regime in enumerate(REGIMES):
        vals = summary[f"{regime}_x10"]
        for j, (m, label) in enumerate((("runtime_us", "runtime"), ("avg_latency_ns", "packet latency"), ("energy_uJ", "network energy"))):
            ax = axes[i][j]
            ys = [vals[n][m] for n in names]
            colors = [style(n)["color"] if n != "Folded torus" else "#9467bd" for n in names]
            ax.bar(range(len(names)), ys, color=colors)
            ax.axhline(1.0, color="black", lw=0.8)
            ax.set_xticks(range(len(names)))
            ax.set_xticklabels([n.replace("Searched ", "S. ") for n in names], rotation=30, ha="right", fontsize=8)
            ax.set_title(f"{label}, {regime} regime (10x), rel. to mesh", fontsize=9)
            ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIGURES / "applications.png", dpi=150, bbox_inches="tight")


if __name__ == "__main__":
    main()
