"""Experiment 9: per-benchmark breakdown of the application results.

Reads results/applications.csv (experiment 8) and writes, for 10x
compression and both clocking regimes, each design's runtime relative to the
mesh on every benchmark, so it is visible whether a few benchmarks drive the
geometric means reported in the paper.

Outputs: results/per_benchmark.csv, paper/per_benchmark_table.tex
"""

import csv
import math
from collections import defaultdict

from common import RESULTS, ROOT

SPEEDUP = 10
REGIMES = (("pipelined", "Fixed 2~GHz clock"), ("global", "Wire-limited clock"))
DESIGNS = ["Torus", "Folded torus", "Modified torus", "Searched (links <= 5)", "Searched (wire <= 208)"]
SHORT = {"Torus": "Torus", "Folded torus": "Folded", "Modified torus": "Modified",
         "Searched (links <= 5)": "S.\\,$\\le$5", "Searched (wire <= 208)": "S.\\,$\\le$208"}


def main():
    rows = list(csv.DictReader(open(RESULTS / "applications.csv")))
    tab = defaultdict(dict)  # (regime, benchmark) -> design -> runtime
    for r in rows:
        if int(r["speedup"]) == SPEEDUP and float(r.get("tile_mm", 1.5)) == 1.5:
            tab[(r["regime"], r["benchmark"])][r["design"]] = float(r["runtime_us"])
    benches = sorted({b for _, b in tab})

    out_rows, lines = [], []
    lines += [r"\begin{table}[t]", r"\centering",
              r"\caption{Runtime relative to the mesh for each PARSEC benchmark ($10\times$ compression, "
              r"1.5~mm tiles); lower is better. Best evaluated design per row in bold.}",
              r"\label{tab:perbench}", r"\small", r"\begin{tabular}{@{}l" + "r" * len(DESIGNS) * 2 + "@{}}", r"\toprule",
              " & " + " & ".join(rf"\multicolumn{{{len(DESIGNS)}}}{{c}}{{{t}}}" for _, t in REGIMES) + r" \\",
              r"\cmidrule(lr){2-" + str(1 + len(DESIGNS)) + r"}\cmidrule(l){" + str(2 + len(DESIGNS)) + "-" + str(1 + 2 * len(DESIGNS)) + "}",
              "Benchmark & " + " & ".join(SHORT[d] for d in DESIGNS) * 1 + " & " + " & ".join(SHORT[d] for d in DESIGNS) + r" \\",
              r"\midrule"]
    gm = {reg: {d: [] for d in DESIGNS} for reg, _ in REGIMES}
    for b in benches:
        cells = []
        for reg, _ in REGIMES:
            base = tab[(reg, b)]["Mesh"]
            vals = {d: tab[(reg, b)][d] / base for d in DESIGNS}
            best = min(vals.values())
            for d in DESIGNS:
                gm[reg][d].append(vals[d])
                out_rows.append({"benchmark": b, "regime": reg, "design": d, "runtime_rel_mesh": vals[d]})
                cells.append(rf"\textbf{{{vals[d]:.3f}}}" if vals[d] == best else f"{vals[d]:.3f}")
        lines.append(b + " & " + " & ".join(cells) + r" \\")
    lines.append(r"\midrule")
    geo = []
    for reg, _ in REGIMES:
        for d in DESIGNS:
            geo.append(f"{math.exp(sum(math.log(x) for x in gm[reg][d]) / len(gm[reg][d])):.3f}")
    lines += ["Geo.\\ mean & " + " & ".join(geo) + r" \\", r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    (ROOT / "paper" / "per_benchmark_table.tex").write_text("\n".join(lines) + "\n")
    with open(RESULTS / "per_benchmark.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        w.writeheader()
        w.writerows(out_rows)
    for line in lines[10:-4]:
        print(line.replace(r"\textbf{", "*").replace("}", "").replace(r" \\", ""))


if __name__ == "__main__":
    main()
