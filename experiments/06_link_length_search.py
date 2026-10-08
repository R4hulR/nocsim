"""Experiment 6: best boundary-link layouts under a maximum link length (8x8).

Kite and NetSmith both observe that a network's clock is limited by its
longest wire, and classify topologies by their longest link. This experiment
asks the on-chip, radix-4 version of that question: if no extra link may be
longer than L tiles, how good can a boundary-port layout get?

For each L, simulated annealing (several cold starts, plus a warm start from
any reference design that satisfies the limit) maximises the same
multi-pattern objective as experiment 3, with no total-wire budget.
Reference designs: mesh, torus (flat), folded torus (same graph as the
torus, every link <= 2 tiles), and the modified torus.

Outputs: results/link_length_search.json, results/designs/maxlink_8x8_L*.json,
         results/figures/link_length_search.png
"""

import json
from concurrent.futures import ProcessPoolExecutor

import matplotlib.pyplot as plt
from common import DESIGNS, FIGURES, RESULTS, WORKERS, save_json, style

from nocsim.search import DesignSpace, Objective, simulated_annealing
from nocsim.topology import folded_torus, from_boundary_links, mesh, modified_torus, torus

N = 8
EVALS = 2500
SEEDS = range(4)
LIMITS = [2, 3, 4, 5, 7, None]  # None = no limit
NO_BUDGET = 10**6  # wire is not constrained here, only the longest link


def run(args):
    limit, seed, warm = args
    obj = Objective(N, NO_BUDGET, max_link=limit)
    init = None
    if warm:
        space = DesignSpace(N, limit)
        fits = [t for t in (torus(N), modified_torus(N))
                if all(space.can_link(u, v, set()) for u, v in t.extra_links())]
        if not fits:
            return None
        init = max(fits, key=lambda t: obj(t.extra_links())).extra_links()
    r = simulated_annealing(obj, EVALS, seed, init=init)
    return {"limit": limit, "seed": seed, "warm": warm, "score": r.best_score,
            "links": r.best_links, "stats": obj.stats(r.best_links)}


def describe(topo, obj):
    """Objective-style metrics for a reference topology, which may lie outside the boundary design space."""
    s = obj.evaluate(topo)
    s["throughput_rel_torus"] = sum(s[f"throughput_est_{p}"] / obj.ref_thr[p] for p in obj.patterns) / len(obj.patterns)
    return s


def main():
    jobs = [(L, s, False) for L in LIMITS for s in SEEDS] + [(L, 100, True) for L in LIMITS]
    with ProcessPoolExecutor(WORKERS) as ex:
        runs = [r for r in ex.map(run, jobs) if r is not None]

    ref_obj = Objective(N, NO_BUDGET)
    refs = []
    for t in (mesh(N), torus(N), folded_torus(N), modified_torus(N)):
        s = describe(t, ref_obj)
        refs.append({"topology": t.name, "max_link": t.max_link_length, "total_wire": t.total_wire_length,
                     "avg_hops": s["avg_hops"], "throughput_rel_torus": s["throughput_rel_torus"]})

    best = []
    print(f"{'max link':>8} {'actual':>6} {'wire':>5} {'hops':>6} {'thr/torus':>9}  found by")
    for L in LIMITS:
        b = max((r for r in runs if r["limit"] == L), key=lambda r: r["score"])
        t = from_boundary_links(N, b["links"], f"Searched (max link {L or 'any'})",
                                {"max_link": L, "warm_start": b["warm"]})
        (DESIGNS / f"maxlink_8x8_L{L or 'any'}.json").write_text(json.dumps(t.to_dict(), indent=1))
        st = b["stats"]
        row = {"limit": L, "max_link": t.max_link_length, "total_wire": t.total_wire_length,
               "avg_hops": st["avg_hops"], "throughput_rel_torus": st["throughput_rel_torus"],
               "found_by": "warm start" if b["warm"] else "cold start"}
        best.append(row)
        print(f"{str(L or 'any'):>8} {row['max_link']:6d} {row['total_wire']:5d} {row['avg_hops']:6.3f} "
              f"{row['throughput_rel_torus']:9.3f}  {row['found_by']}")
    print("references:")
    for r in refs:
        print(f"   {r['topology']:15} max link {r['max_link']}, wire {r['total_wire']}, hops {r['avg_hops']:.3f}, "
              f"thr/torus {r['throughput_rel_torus']:.3f}")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    xs = [r["max_link"] for r in best]
    for ax, key, label in ((axes[0], "throughput_rel_torus", "throughput estimate relative to torus\n(mean of 4 patterns)"),
                           (axes[1], "avg_hops", "average hop count")):
        ax.plot(xs, [r[key] for r in best], "-D", color="#2ca02c", label="best boundary-port layout found")
        for r in refs:
            st_ = style(r["topology"]) if r["topology"] != "Folded torus" else {"color": "#9467bd", "marker": "P"}
            ax.scatter(r["max_link"], r[key], s=90, zorder=3, label=r["topology"], **st_)
        ax.set_xlabel("longest link (tiles)")
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8)
    axes[0].set_title("Throughput vs. longest link", fontsize=10)
    axes[1].set_title("Hop count vs. longest link", fontsize=10)
    fig.tight_layout()
    fig.savefig(FIGURES / "link_length_search.png", dpi=150, bbox_inches="tight")
    save_json({"best_per_limit": best, "references": refs}, RESULTS / "link_length_search.json")


if __name__ == "__main__":
    main()
