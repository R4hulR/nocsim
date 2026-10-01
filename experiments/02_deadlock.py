"""Experiment 2: is the thesis' BFS routing safe? (No, and here's the fix.)

Part A (static): build the channel dependency graph (CDG) for each topology
and routing function. A cycle means deadlock is possible (Dally & Seitz).

Part B (dynamic): simulate the thesis' BFS routing with a single VC at
increasing load, several seeds each, and record how often the network
freezes. Then hammer the adaptive + up*/down* escape scheme at loads far
beyond saturation to show that it never does.

Output: results/deadlock.json
"""

from concurrent.futures import ProcessPoolExecutor

from common import RESULTS, WORKERS, baselines, save_json

from nocsim.routing import RoutingTables, is_deadlock_free
from nocsim.simulator import SimConfig, simulate

N = 8
RATES = [0.05, 0.1, 0.15, 0.2, 0.3, 0.5]
SEEDS = range(5)


def run(args):
    topo, routing, vcs, rate, seed = args
    cfg = SimConfig(routing=routing, num_vcs=vcs, warmup=500, measure=2000, drain_limit=3000, seed=seed)
    r = simulate(topo, "uniform", rate, cfg)
    return topo.name, routing, rate, seed, r.deadlocked


def main():
    out = {"cdg_acyclic": {}, "bfs_deadlock_rate": {}, "adaptive_stress": {}}

    print("Part A: is the channel dependency graph acyclic?  (True = deadlock-free)")
    for topo in baselines(N):
        tables = RoutingTables(topo)
        row = {algo: is_deadlock_free(tables, algo) for algo in ("bfs", "updown", "adaptive")}
        out["cdg_acyclic"][topo.name] = row
        print(f"  {topo.name:16} {row}")

    jobs = [(t, "bfs", 1, r, s) for t in baselines(N) for r in RATES for s in SEEDS]
    stress = [(t, "adaptive", SimConfig().num_vcs, r, s) for t in baselines(N) for r in (0.6, 1.0) for s in SEEDS]
    with ProcessPoolExecutor(WORKERS) as ex:
        results = list(ex.map(run, jobs + stress))

    print("\nPart B: fraction of runs that deadlocked (uniform traffic, 8x8)")
    print(f"  {'topology':16} {'routing':9} " + " ".join(f"{r:>5}" for r in RATES + [0.6, 1.0]))
    for t in baselines(N):
        for routing, rates, key in (("bfs", RATES, "bfs_deadlock_rate"), ("adaptive", [0.6, 1.0], "adaptive_stress")):
            fr = {}
            for r in rates:
                runs = [d for name, ro, rr, _, d in results if name == t.name and ro == routing and rr == r]
                fr[r] = sum(runs) / len(runs)
            out[key][t.name] = fr
            cells = " ".join(f"{fr[r]:5.1f}" if r in fr else "    -" for r in RATES + [0.6, 1.0])
            print(f"  {t.name:16} {routing:9} {cells}")

    save_json(out, RESULTS / "deadlock.json")


if __name__ == "__main__":
    main()
