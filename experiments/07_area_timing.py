"""Experiment 7: area and timing of each design from DSENT (22 nm).

For every design, at three tile pitches (1.0 / 1.5 / 2.0 mm):
* area: routers, global-wire tracks and repeaters;
* global-clock regime (Kite / NetSmith): the highest clock at which the
  longest link fits in one cycle, and the resulting zero-load latency in ns;
* pipelined-link regime at a fixed 2 GHz clock: long links take
  ceil(length / reach) cycles; zero-load latency measured in the simulator.

Energy needs activity counts, so it is computed with the application traces
in the next experiment.

Requires results/dsent_characterization.json (tools/dsent_main/characterize.py).
Outputs: results/area_timing.csv, results/figures/area_timing.png
"""

import csv
import json

import matplotlib.pyplot as plt
from common import DESIGNS, FIGURES, RESULTS, style

from nocsim.metrics import summary
from nocsim.power import TechModel
from nocsim.simulator import SimConfig, simulate
from nocsim.topology import Topology, folded_torus, mesh, modified_torus, torus

N = 8
PITCHES = (1.0, 1.5, 2.0)
FIXED_CLOCK = 2e9


def designs():
    out = [mesh(N), torus(N), folded_torus(N), modified_torus(N)]
    for f, name in (("maxlink_8x8_L5.json", "Searched (links <= 5)"), ("frontier_8x8_w208.json", "Searched (wire <= 208)")):
        d = Topology.from_dict(json.loads((DESIGNS / f).read_text()))
        out.append(Topology(name, d.n, d.edges, d.info))
    return out


def zero_load_cycles(topo, reach_tiles):
    """Measured zero-load latency (cycles) with pipelined long links."""
    cfg = SimConfig(link_latency="wire", wire_reach=reach_tiles, warmup=300, measure=6000, seed=3)
    return simulate(topo, "uniform", 0.003, cfg).avg_latency


def main():
    rows = []
    for pitch in PITCHES:
        tm = TechModel.from_dsent(tile_mm=pitch, freq_hz=FIXED_CLOCK)
        for t in designs():
            s, a = summary(t), tm.area(t)
            f_max = tm.max_frequency(t)
            # Global clock: every hop is router (1 cycle) + link (1 cycle) at f_max.
            t0_global_ns = 2 * s["avg_hops"] / f_max * 1e9
            t0_pipe_cyc = zero_load_cycles(t, tm.reach_tiles())
            rows.append({"design": t.name, "tile_mm": pitch, "max_link_tiles": t.max_link_length,
                         "total_wire_tiles": t.total_wire_length, "area_mm2": a["total_mm2"],
                         "router_mm2": a["router_mm2"], "wire_track_mm2": a["wire_track_mm2"],
                         "repeater_mm2": a["repeater_mm2"], "f_max_GHz": f_max / 1e9,
                         "zero_load_global_ns": t0_global_ns,
                         "zero_load_pipelined_ns": t0_pipe_cyc / FIXED_CLOCK * 1e9,
                         "avg_hops": s["avg_hops"]})
    with open(RESULTS / "area_timing.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"{'tile':>4} {'design':24} {'area':>6} {'f_max':>6} {'T0 global':>9} {'T0 pipe@2GHz':>12}")
    for r in rows:
        print(f"{r['tile_mm']:4.1f} {r['design']:24} {r['area_mm2']:6.2f} {r['f_max_GHz']:6.2f} "
              f"{r['zero_load_global_ns']:9.2f} {r['zero_load_pipelined_ns']:12.2f}")

    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    names = [t.name for t in designs()]
    for ax, key, label in ((axes[0], "area_mm2", "network area (mm$^2$)"),
                           (axes[1], "zero_load_global_ns", "zero-load latency, global clock (ns)"),
                           (axes[2], "zero_load_pipelined_ns", "zero-load latency, pipelined links @2 GHz (ns)")):
        for name in names:
            pts = [(r["tile_mm"], r[key]) for r in rows if r["design"] == name]
            st = style(name) if name != "Folded torus" else {"color": "#9467bd", "marker": "P"}
            ax.plot(*zip(*pts), label=name, **st)
        ax.set_xlabel("tile pitch (mm)")
        ax.set_title(label, fontsize=10)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIGURES / "area_timing.png", dpi=150, bbox_inches="tight")


if __name__ == "__main__":
    main()
