"""Experiment 5: validate nocsim against BookSim 2.

BookSim (Jiang et al., ISPASS 2013) is the standard cycle-accurate NoC
simulator. Here nocsim and BookSim run the same networks under the same
traffic, and we check that they agree. BookSim can't run nocsim's adaptive
escape routing, so this uses routing both support: dimension-order routing
on an 8x8 mesh and torus (BookSim's dim_order_mesh / dim_order_torus,
re-implemented in nocsim as routing="dor" with the same dateline VC rule).

Matched settings (see experiments/booksim_base.cfg): 4-packet buffers per
VC, single-flit packets, Bernoulli injection, 1-cycle links. BookSim's
router pipeline (VC alloc, switch alloc, switch traversal) plus the link
costs 4 cycles per hop, so nocsim runs with router_delay = 3.

Two known differences are corrected for explicitly rather than tuned away:
  * BookSim adds a constant 6 cycles per packet (injection channel, ejection
    channel and the destination router's pipeline), which nocsim does not
    model. We add it to nocsim's latency.
  * BookSim lets a node send packets to itself (1/64 of uniform traffic, and
    the 8 diagonal nodes under transpose). Those packets never enter the
    network and take exactly 6 cycles. We mix them into nocsim's average with
    the same weight.

One difference is *not* corrected by default, because it is a real
modelling gap: in BookSim a single VC cannot forward back-to-back packets
every cycle (each packet goes through VC and switch allocation in turn),
while nocsim allows one packet per VC per cycle. Where a traffic class has
only one VC (the 2-VC torus, whose two dateline classes get one VC each)
nocsim's saturation throughput is optimistic. With two VCs per class (mesh,
4-VC torus) the simulators agree. The 4-VC torus runs show exactly that, and
for the 2-VC torus we also run nocsim with ``vc_turnaround`` (a minimum gap
between packets leaving the same VC) to test whether that gap explains the
difference.

The 5-flit configurations (8-flit buffers) match the data packets of the
application traces. BookSim uses wormhole flow control, nocsim virtual
cut-through; with buffers that hold a whole packet the two behave alike.

Run experiments/booksim/run_booksim.py first (on Linux/WSL) to produce
results/booksim_raw.json.

Outputs: results/booksim_validation.json, results/figures/booksim_validation.png
"""

import json
import math
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import matplotlib.pyplot as plt
from common import FIGURES, RESULTS, WORKERS, save_json

from nocsim.simulator import SimConfig, simulate
from nocsim.topology import mesh, torus

N = 8
CONFIGS = [("mesh", 2, 1, 4), ("torus", 2, 1, 4), ("torus", 4, 1, 4), ("mesh", 2, 5, 8), ("torus", 4, 5, 8)]
TURNAROUNDS = (2, 3)  # extra nocsim series for the 1-VC-per-class torus
PATTERNS = ["uniform", "transpose", "bit_complement"]
SEEDS = (1, 2, 3)
BOOKSIM_FIXED = 6  # cycles BookSim adds per packet outside the network
# Fraction of packets BookSim sends to the source itself, per pattern.
SELF_FRACTION = {"uniform": 1 / (N * N), "transpose": N / (N * N), "bit_complement": 0.0}
LATENCY_FACTOR = 3.0  # saturation = latency exceeds 3x zero-load (same rule for both simulators)


def run_curve(args):
    topo_name, vcs, psize, buf, turnaround, pattern, seed, rates = args
    topo = mesh(N) if topo_name == "mesh" else torus(N)
    cfg = SimConfig(routing="dor", num_vcs=vcs, buffer_depth=buf, packet_size=psize, vc_turnaround=turnaround,
                    router_delay=3, link_latency="unit", warmup=3000, measure=5000, drain_limit=10000, seed=seed)
    out = []
    for r in rates:
        res = simulate(topo, pattern, r, cfg)
        out.append({"rate": r, "latency": res.avg_latency, "unstable": res.saturated})
        if res.saturated:
            break
    return (topo_name, vcs, psize, buf, turnaround, pattern), out


def mean_curve(runs: list[list[dict]]) -> dict[float, float]:
    """Average latency per rate over seeds, keeping only rates stable in every seed."""
    by_rate = defaultdict(list)
    for curve in runs:
        for p in curve:
            if not p["unstable"] and p["latency"] is not None and not math.isnan(p["latency"]):
                by_rate[p["rate"]].append(p["latency"])
    return {r: sum(v) / len(v) for r, v in sorted(by_rate.items()) if len(v) == len(runs)}


def saturation(curve: dict[float, float]) -> float:
    """Highest stable rate whose latency is within LATENCY_FACTOR x zero-load."""
    rates = sorted(curve)
    zero = curve[rates[0]]
    ok = [r for r in rates if curve[r] <= LATENCY_FACTOR * zero]
    return max(ok) if ok else 0.0


def main():
    booksim = json.loads((RESULTS / "booksim_raw.json").read_text())
    bs_runs = defaultdict(lambda: defaultdict(list))
    for r in booksim:
        bs_runs[(r["topology"], r["vcs"], r.get("packet_size", 1), r.get("buffer", 4), r["pattern"])][r["seed"]].append(r)
    bs_runs = {k: [sorted(v, key=lambda r: r["rate"]) for v in seeds.values()] for k, seeds in bs_runs.items()}
    rates = {cfg: sorted({r["rate"] for r in booksim if (r["topology"], r["vcs"], r.get("packet_size", 1), r.get("buffer", 4)) == cfg})
             for cfg in CONFIGS}

    jobs = [(t, v, ps, b, 1, p, s, rates[(t, v, ps, b)]) for t, v, ps, b in CONFIGS for p in PATTERNS for s in SEEDS]
    jobs += [("torus", 2, 1, 4, k, p, s, rates[("torus", 2, 1, 4)]) for k in TURNAROUNDS for p in PATTERNS for s in SEEDS]
    with ProcessPoolExecutor(WORKERS) as ex:
        ns_results = list(ex.map(run_curve, jobs))
    ns_runs = defaultdict(list)
    for key, curve in ns_results:
        f = SELF_FRACTION[key[5]]
        self_lat = BOOKSIM_FIXED + key[2] - 1  # a self-addressed packet still serializes its flits
        ns_runs[key].append([dict(pt, latency=(1 - f) * (pt["latency"] + BOOKSIM_FIXED) + f * self_lat)
                             for pt in curve])

    rows = []
    fig, axes = plt.subplots(len(CONFIGS), len(PATTERNS), figsize=(13, 3.2 * len(CONFIGS)))
    print(f"{'config':16} {'pattern':15} {'T0 BookSim':>10} {'T0 nocsim':>10} {'sat BookSim':>11} {'sat nocsim':>10}  latency error")
    for i, (t, v, ps, b) in enumerate(CONFIGS):
        for j, p in enumerate(PATTERNS):
            bs, ns = mean_curve(bs_runs[(t, v, ps, b, p)]), mean_curve(ns_runs[(t, v, ps, b, 1, p)])
            row = {"topology": t, "vcs": v, "packet_size": ps, "buffer": b, "pattern": p,
                   "zero_load_booksim": bs[min(bs)], "zero_load_nocsim": ns[min(ns)],
                   "saturation_booksim": saturation(bs), "saturation_nocsim": saturation(ns)}
            # Mean relative latency gap over loads below both saturation points.
            limit = min(row["saturation_booksim"], row["saturation_nocsim"])
            common = [r for r in bs if r in ns and r <= limit]
            row["mean_abs_latency_error_pct"] = 100 * sum(abs(ns[r] - bs[r]) / bs[r] for r in common) / len(common)
            rows.append(row)
            if (t, v, ps) == ("torus", 2, 1):
                for k in TURNAROUNDS:
                    nk = mean_curve(ns_runs[(t, v, ps, b, k, p)])
                    row[f"saturation_nocsim_turnaround{k}"] = saturation(nk)
            print(f"{t + '/' + str(v) + 'VC/' + str(ps) + 'f':16} {p:15} {row['zero_load_booksim']:10.2f} {row['zero_load_nocsim']:10.2f} "
                  f"{row['saturation_booksim']:11.2f} {row['saturation_nocsim']:10.2f}  "
                  f"{row['mean_abs_latency_error_pct']:5.1f}%" +
                  "".join(f"   turnaround {k}: sat {row[f'saturation_nocsim_turnaround{k}']:.2f}" for k in TURNAROUNDS
                          if f"saturation_nocsim_turnaround{k}" in row))

            ax = axes[i][j]
            ax.plot(list(bs), list(bs.values()), "o--", ms=3, color="#444444", label="BookSim 2")
            ax.plot(list(ns), list(ns.values()), "s-", ms=3, color="#d62728", label="nocsim")
            ax.set_ylim(0, 4 * row["zero_load_booksim"])
            ax.set_xlim(0, max(max(bs), max(ns)) + 0.04)
            if (t, v, ps) == ("torus", 2, 1):
                for k, ls in zip(TURNAROUNDS, (":", "-.")):
                    nk = mean_curve(ns_runs[(t, v, ps, b, k, p)])
                    ax.plot(list(nk), list(nk.values()), ls, color="#1f77b4", lw=1.2, label=f"nocsim, VC turnaround {k}")
                ax.legend(fontsize=6)
            ax.set_title(f"{t}, {v} VCs, {ps}-flit packets  |  {p}", fontsize=9)
            ax.set_xlabel("injection rate (packets/node/cycle)", fontsize=8)
            if j == 0:
                ax.set_ylabel("avg latency (cycles)")
            ax.grid(alpha=0.3)
    axes[0][0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES / "booksim_validation.png", dpi=150, bbox_inches="tight")
    save_json(rows, RESULTS / "booksim_validation.json")


if __name__ == "__main__":
    main()
