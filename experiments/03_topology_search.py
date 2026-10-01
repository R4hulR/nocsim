"""Experiment 3: searching for better boundary-link layouts (8x8).

A. Method comparison. Random search, simulated annealing and REINFORCE,
   each with the same evaluation budget, at the modified torus' own wire
   budget (176 tiles). Question: can an automated search beat the hand
   design without spending more wire?
B. Wire/throughput frontier. For a range of wire budgets, take the best
   design annealing finds (multi-start, plus a warm start from any baseline
   that fits the budget). This shows where mesh, torus and modified torus
   sit relative to what is achievable.

Outputs: results/search.json, results/designs/*.json,
         results/figures/search_convergence.png, results/figures/frontier.png
"""

from concurrent.futures import ProcessPoolExecutor

import matplotlib.pyplot as plt
import numpy as np
from common import DESIGNS, FIGURES, RESULTS, WORKERS, baselines, save_json, style

from nocsim.search import Objective, random_search, reinforce, simulated_annealing
from nocsim.topology import from_boundary_links, modified_torus, torus

N = 8
EVALS = 2500
SEEDS = range(3)
BUDGETS = [152, 160, 176, 192, 208, 224, 256]
FRONTIER_SEEDS = range(4)


def run_method(args):
    method, budget, seed = args
    obj = Objective(N, budget)
    if method == "random":
        r = random_search(obj, EVALS, seed)
    elif method == "annealing":
        r = simulated_annealing(obj, EVALS, seed)
    elif method == "reinforce":
        r = reinforce(obj, EVALS, seed, batch=64, lr=8.0)
    elif method == "annealing_warm":
        # Warm start from the best baseline that fits within the budget.
        fits = [t for t in (torus(N), modified_torus(N)) if t.total_wire_length <= budget]
        if not fits:
            return None
        start = max(fits, key=lambda t: obj(t.extra_links()))
        r = simulated_annealing(obj, EVALS, seed, init=start.extra_links())
    stats = obj.stats(r.best_links)
    return {"method": method, "budget": budget, "seed": seed, "score": r.best_score, "links": r.best_links,
            "history": r.history, "stats": stats}


def main():
    jobs = [(m, 176, s) for m in ("random", "annealing", "reinforce") for s in SEEDS]
    jobs += [("annealing", b, 100 + s) for b in BUDGETS for s in FRONTIER_SEEDS]
    jobs += [("annealing_warm", b, 200) for b in BUDGETS]
    with ProcessPoolExecutor(WORKERS) as ex:
        runs = [r for r in ex.map(run_method, jobs) if r is not None]

    # ---------------- A. method comparison at the modified torus' budget
    comp = [r for r in runs if r["budget"] == 176 and r["seed"] in SEEDS]
    ref = Objective(N, 176)
    mod_score = ref(modified_torus(N).extra_links())
    print(f"A. Same wire as the modified torus (176 tiles), {EVALS} evaluations, {len(SEEDS)} seeds")
    print(f"   modified torus (hand design): J = {mod_score:.3f}")
    summary_a = {}
    fig, ax = plt.subplots(figsize=(6, 4))
    for method, color in (("random", "#7f7f7f"), ("annealing", "#2ca02c"), ("reinforce", "#9467bd")):
        rs = [r for r in comp if r["method"] == method]
        scores = [r["score"] for r in rs]
        thr = [r["stats"]["throughput_rel_torus"] for r in rs]
        hops = [r["stats"]["avg_hops"] for r in rs]
        summary_a[method] = {"score_mean": float(np.mean(scores)), "score_std": float(np.std(scores)),
                             "thr_mean": float(np.mean(thr)), "hops_mean": float(np.mean(hops))}
        print(f"   {method:10}  J = {np.mean(scores):.3f} +/- {np.std(scores):.3f}   "
              f"throughput vs torus = {np.mean(thr):.3f}   hops = {np.mean(hops):.3f}")
        L = min(len(r["history"]) for r in rs)
        curves = np.array([r["history"][:L] for r in rs])
        x = np.arange(1, L + 1)
        ax.plot(x, curves.mean(0), color=color, label=method)
        ax.fill_between(x, curves.min(0), curves.max(0), color=color, alpha=0.15)
    ax.axhline(mod_score, color="#d62728", ls="--", label="modified torus (hand design)")
    ax.set_xlabel("objective evaluations")
    ax.set_ylabel("best J found (higher is better)")
    ax.set_title("Search at a fixed wire budget (8x8, 176 tiles)", fontsize=10)
    ax.set_ylim(bottom=max(-1.0, ax.get_ylim()[0]))
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES / "search_convergence.png", dpi=150, bbox_inches="tight")

    # ---------------- B. frontier
    frontier = []
    for b in BUDGETS:
        rs = [r for r in runs if r["budget"] == b and r["method"] in ("annealing", "annealing_warm")]
        best = max(rs, key=lambda r: r["score"])
        # note: stats already has a "links" field (the link count), so the design goes under "extra_links"
        frontier.append({**best["stats"], "budget": b, "method": best["method"], "extra_links": best["links"]})
    base_stats = []
    for t in baselines(N):
        s = dict(ref.stats(t.extra_links()))
        s["topology"] = t.name
        base_stats.append(s)

    print("\nB. Best design found per wire budget")
    print(f"   {'budget':>6} {'wire':>5} {'thr/torus':>9} {'hops':>6}  found by")
    for f in frontier:
        print(f"   {f['budget']:6d} {f['total_wire']:5d} {f['throughput_rel_torus']:9.3f} {f['avg_hops']:6.3f}  {f['method']}")
    for s in base_stats:
        print(f"   {s['topology']:>14}: wire {s['total_wire']}, thr/torus {s['throughput_rel_torus']:.3f}, hops {s['avg_hops']:.3f}")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for ax, key, label in ((axes[0], "throughput_rel_torus", "throughput estimate relative to torus\n(mean of 4 traffic patterns)"),
                           (axes[1], "avg_hops", "average hop count")):
        ax.plot([f["total_wire"] for f in frontier], [f[key] for f in frontier], "-D", color="#2ca02c",
                label="best found by search")
        for s in base_stats:
            ax.scatter(s["total_wire"], s[key], s=90, zorder=3, label=s["topology"],
                       color=style(s["topology"])["color"], marker=style(s["topology"])["marker"])
        ax.set_xlabel("total wire length (tile pitches)")
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8)
    axes[0].set_title("Throughput vs. wire (higher-left is better)", fontsize=10)
    axes[1].set_title("Hop count vs. wire (lower-left is better)", fontsize=10)
    fig.tight_layout()
    fig.savefig(FIGURES / "frontier.png", dpi=150, bbox_inches="tight")

    # ---------------- save every frontier design for the simulation experiment
    same_wire = next(f for f in frontier if f["budget"] == 176)
    same = set(from_boundary_links(N, same_wire["extra_links"]).edges) == set(modified_torus(N).edges)
    print(f"
   At 176 tiles the best design found {'IS' if same else 'is NOT'} the modified torus itself.")
    for f in frontier:
        t = from_boundary_links(N, f["extra_links"], f"Searched (wire<={f['budget']})", {"wire_budget": f["budget"]})
        (DESIGNS / f"frontier_8x8_w{f['budget']}.json").write_text(__import__("json").dumps(t.to_dict(), indent=1))

    save_json({"method_comparison": summary_a, "modified_score": mod_score,
               "frontier": [{k: v for k, v in f.items()} for f in frontier],
               "baselines": base_stats}, RESULTS / "search.json")


if __name__ == "__main__":
    main()
