"""Run BookSim 2 sweeps for the simulator-validation experiment.

Standard library only, so it runs on any Linux box (or WSL) with a BookSim
binary. Usage:

    python3 run_booksim.py --booksim ~/booksim2/src/booksim [--out booksim_raw.json]

Every run uses experiments/booksim_base.cfg, which is set up to match
nocsim's validation configuration: 8x8, dimension-order routing,
4-packet buffers, single-flit packets, Bernoulli injection. Topology,
VC count, traffic and injection rate are overridden on the command line. Results go to
results/booksim_raw.json, which experiments/05_booksim_validation.py reads.
"""

import argparse
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BASE_CFG = HERE.parent / "booksim_base.cfg"

# (topology, VCs). The 4-VC torus gives each dateline class two VCs; see
# experiments/05_booksim_validation.py for why that matters.
CONFIGS = (("mesh", 2), ("torus", 2), ("torus", 4))
# BookSim name -> nocsim name for the same pattern.
PATTERNS = {"uniform": "uniform", "transpose": "transpose", "bitcomp": "bit_complement"}
RATES = [0.005] + [round(0.02 * i, 3) for i in range(1, 50)]
SEEDS = (1, 2, 3)

NUM = r"([-+0-9.eE]+|nan|-?inf)"


def run_one(booksim: str, topo: str, vcs: int, pattern: str, rate: float, seed: int) -> dict:
    cmd = [booksim, str(BASE_CFG), f"topology={topo}", f"num_vcs={vcs}", f"traffic={pattern}",
           f"injection_rate={rate}", f"seed={seed}"]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    res = {"topology": topo, "vcs": vcs, "pattern": PATTERNS[pattern], "rate": rate, "seed": seed}
    # BookSim prints "Simulation unstable" (or never converges) past saturation.
    res["unstable"] = "unstable" in out.lower() or "Overall Traffic Statistics" not in out
    for key, label in (("latency", "Packet latency average"), ("accepted", "Accepted packet rate average"),
                       ("hops", "Hops average")):
        # The "Overall" block at the end is the average across all samples.
        tail = out.split("Overall Traffic Statistics")[-1]
        m = re.search(label + r"\s*=\s*" + NUM, tail)
        res[key] = float(m.group(1)) if m else None
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--booksim", required=True, help="path to the booksim binary")
    ap.add_argument("--out", default=str(ROOT / "results" / "booksim_raw.json"))
    ap.add_argument("--jobs", type=int, default=16)
    args = ap.parse_args()

    jobs = [(t, v, p, r, s) for t, v in CONFIGS for p in PATTERNS for s in SEEDS for r in RATES]
    with ThreadPoolExecutor(args.jobs) as ex:
        results = list(ex.map(lambda j: run_one(args.booksim, *j), jobs))
    Path(args.out).write_text(json.dumps(results, indent=1))
    print(f"wrote {len(results)} runs to {args.out}")


if __name__ == "__main__":
    main()
