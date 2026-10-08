"""Shared helpers for the experiment scripts: paths, parallel sweeps, plotting."""

from __future__ import annotations

import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # lets the scripts run without installing the package

from nocsim.routing import RoutingTables  # noqa: E402
from nocsim.simulator import SimConfig, simulate  # noqa: E402
from nocsim.topology import Topology, mesh, modified_torus, torus  # noqa: E402

RESULTS = ROOT / "results"
FIGURES = RESULTS / "figures"
DESIGNS = RESULTS / "designs"
for d in (RESULTS, FIGURES, DESIGNS):
    d.mkdir(parents=True, exist_ok=True)

WORKERS = max(1, min(24, (os.cpu_count() or 2) - 1))

# One fixed colour per topology so every figure reads the same way.
COLORS = {
    "Mesh": "#7f7f7f",
    "Torus": "#1f77b4",
    "Folded torus": "#9467bd",
    "Modified torus": "#d62728",
    "Searched": "#2ca02c",  # any searched design not listed below
    "Searched (links": "#2ca02c",
    "Searched (wire": "#bcbd22",
}
MARKERS = {"Mesh": "s", "Torus": "o", "Folded torus": "P", "Modified torus": "^",
           "Searched": "D", "Searched (links": "D", "Searched (wire": "X"}


def style(name: str) -> dict:
    key = name
    if name.startswith("Searched"):
        key = next((k for k in ("Searched (links", "Searched (wire") if name.startswith(k)), "Searched")
    return {"color": COLORS.get(key, "black"), "marker": MARKERS.get(key, "x")}


def paper_designs(n: int = 8) -> list[Topology]:
    """The six designs compared in the resubmission (experiments 4, 7, 8)."""
    from nocsim.topology import folded_torus
    out = [mesh(n), torus(n), folded_torus(n), modified_torus(n)]
    for f, name in (("maxlink_8x8_L5.json", "Searched (links <= 5)"), ("frontier_8x8_w208.json", "Searched (wire <= 208)")):
        d = load_design(DESIGNS / f)
        out.append(Topology(name, d.n, d.edges, d.info))
    return out


def baselines(n: int) -> list[Topology]:
    return [mesh(n), torus(n), modified_torus(n)]


def load_design(path: Path) -> Topology:
    return Topology.from_dict(json.loads(path.read_text()))


def save_json(obj, path: Path) -> None:
    path.write_text(json.dumps(obj, indent=2, default=float))


# ---------------------------------------------------------------------------
# Latency-vs-load sweeps
# ---------------------------------------------------------------------------


def _sweep_one(args):
    """Simulate one topology/pattern at increasing load until it saturates."""
    topo, pattern, cfg, rates = args
    tables = RoutingTables(topo)
    out = []
    for r in rates:
        res = simulate(topo, pattern, r, cfg, tables)
        out.append(res.to_dict())
        if res.saturated:  # everything past this point is saturated too
            break
    return out


def sweep(jobs: list[tuple[Topology, str, SimConfig, list[float]]]) -> list[list[dict]]:
    """Run many independent load sweeps in parallel, one process per curve."""
    with ProcessPoolExecutor(WORKERS) as ex:
        return list(ex.map(_sweep_one, jobs))


def saturation_throughput(curve: list[dict], latency_factor: float = 3.0) -> float:
    """Highest load the network sustains: it must drain, and stay below
    ``latency_factor`` x its zero-load latency (a common convention)."""
    ok = [p for p in curve if not p["saturated"] and not math.isnan(p["avg_latency"])]
    if not ok:
        return 0.0
    zero_load = ok[0]["avg_latency"]
    good = [p["accepted"] for p in ok if p["avg_latency"] <= latency_factor * zero_load]
    return max(good) if good else 0.0


def rate_grid(step: float = 0.025, top: float = 1.0) -> list[float]:
    """Injection rates to sweep: a low-load point, then ``step`` increments."""
    return [0.01] + [round(step * i, 4) for i in range(1, int(top / step) + 1)]


# ---------------------------------------------------------------------------
# Topology drawing
# ---------------------------------------------------------------------------


def draw_topology(topo: Topology, ax) -> None:
    """Draw routers on their floorplan positions. Mesh links are straight
    grey lines; extra links are arcs coloured by wire length."""
    import matplotlib
    from matplotlib.patches import FancyArrowPatch

    n = topo.n
    xy = lambda u: (u % n, -(u // n))  # noqa: E731
    mesh_edges = set(mesh(n).edges)
    extra = [e for e in topo.edges if e not in mesh_edges]
    cmap = matplotlib.colormaps["plasma"]
    for u, v in topo.edges:
        if (u, v) in mesh_edges:
            (x1, y1), (x2, y2) = xy(u), xy(v)
            ax.plot([x1, x2], [y1, y2], color="#bbbbbb", lw=1.2, zorder=1)
    for u, v in extra:
        (x1, y1), (x2, y2) = xy(u), xy(v)
        L = topo.link_length(u, v)
        color = cmap(0.15 + 0.7 * (L - 1) / max(1, n - 2))
        if L == n - 1 and (x1 == x2 or y1 == y2):
            # Full-length wraparound: draw the usual "leaves one edge,
            # re-enters the opposite one" stubs instead of a line across the die.
            dx, dy = (x2 - x1) / L, (y2 - y1) / L
            ax.plot([x1, x1 - 0.55 * dx], [y1, y1 - 0.55 * dy], color=color, lw=1.6, zorder=2)
            ax.plot([x2, x2 + 0.55 * dx], [y2, y2 + 0.55 * dy], color=color, lw=1.6, zorder=2)
            continue
        # Shorter extra links: an arc bent away from the grid centre.
        cx, cy = (n - 1) / 2, -(n - 1) / 2
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        cross = (x2 - x1) * (cy - my) - (y2 - y1) * (cx - mx)
        rad = 0.35 if cross > 0 else -0.35
        ax.add_patch(
            FancyArrowPatch(
                (x1, y1), (x2, y2), connectionstyle=f"arc3,rad={rad}", arrowstyle="-",
                color=color, lw=1.6, zorder=2,
            )
        )
    xs = [xy(u)[0] for u in range(topo.num_nodes)]
    ys = [xy(u)[1] for u in range(topo.num_nodes)]
    ax.scatter(xs, ys, s=60, color="white", edgecolor="black", zorder=3)
    ax.set_title(f"{topo.name}\nwire = {topo.total_wire_length} tiles", fontsize=10)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_xlim(-1.4, n + 0.4)
    ax.set_ylim(-n - 0.4, 1.4)
