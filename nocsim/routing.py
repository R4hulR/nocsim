"""Routing functions, plus a deadlock analysis based on channel dependencies.

The thesis routed every packet along one BFS shortest path. That is minimal,
but on any topology with cycles (a torus, the modified torus, anything with
wraparounds) it can **deadlock**. Packets hold buffers in a cycle, each one
waits for the next buffer, and none of them can move. Dally & Seitz (1987)
showed that deterministic routing is deadlock-free iff its *channel
dependency graph* (CDG) is acyclic. ``channel_dependency_graph`` builds that graph, so the risk can
be checked instead of argued.

Three routing functions are provided:

``bfs``
    Deterministic shortest-path routing, ties broken by the lowest neighbor
    id. This reproduces the thesis algorithm. Its CDG is cyclic on tori.

``updown``
    Up*/down* routing (Autonet; Schroeder et al., 1991). Build a BFS spanning
    tree, call each link direction "up" (toward the root) or "down", and only
    allow paths that go up zero or more times and then down zero or more
    times. The CDG is acyclic on *any* connected topology, which matters
    because the search in ``nocsim.search`` produces arbitrary topologies.
    Paths are not always minimal.

``adaptive``
    Minimal adaptive routing with an up*/down* escape channel (Duato, 1993).
    Virtual channels 1..V-1 may take *any* minimal next hop, picking the
    least congested one. Virtual channel 0 is reserved for up*/down*. A
    blocked packet can always drop into VC 0 and follow up*/down* rules from
    there. The escape network's CDG is acyclic, so it always drains and the
    whole scheme is deadlock-free. Because buffers hold whole packets
    (virtual cut-through), an escape packet may also hop back onto an
    adaptive VC as soon as one frees up.
"""

from __future__ import annotations

from collections import deque

from .metrics import shortest_paths
from .topology import Topology

UP_PHASE, DOWN_PHASE = 0, 1  # up*/down*: still allowed to go up / only down from here


class RoutingTables:
    """Precomputed next-hop tables for one topology.

    All next hops are stored as *output port indices*: port ``p`` of router
    ``u`` leads to ``topo.neighbors[u][p]``.
    """

    def __init__(self, topo: Topology, root: int | None = None):
        self.topo = topo
        n = topo.num_nodes
        nbrs = topo.neighbors
        self.dist, _ = shortest_paths(topo)

        # minimal[u][t]: every output port of u that lies on a shortest u -> t path.
        self.minimal = [
            [tuple(p for p, v in enumerate(nbrs[u]) if self.dist[v, t] == self.dist[u, t] - 1) for t in range(n)]
            for u in range(n)
        ]
        # bfs[u][t]: the single deterministic port used by the thesis' BFS routing.
        self.bfs = [[(ports[0] if ports else -1) for ports in row] for row in self.minimal]

        self._build_updown(root if root is not None else self._central_node())

    # ----- up*/down* -------------------------------------------------------

    def _central_node(self) -> int:
        """Root of the up*/down* tree: the node with the smallest eccentricity.

        A central root keeps the up*/down* detours short.
        """
        ecc = self.dist.max(axis=1)
        return int(ecc.argmin())

    def is_up(self, u: int, v: int) -> bool:
        """True if hop u -> v goes 'up', i.e. toward the root.

        Ties between nodes at the same BFS level are broken by node id, which
        gives every link exactly one up direction.
        """
        return (self.level[v], v) < (self.level[u], u)

    def _build_updown(self, root: int) -> None:
        topo, n, nbrs = self.topo, self.topo.num_nodes, self.topo.neighbors
        self.root = root
        self.level = [int(d) for d in self.dist[root]]

        # updown_dist[t][u][phase] = fewest hops to reach t legally from u,
        # given whether the packet may still go up (phase UP) or not (DOWN).
        # One reverse BFS over (node, phase) states per destination.
        INF = 1 << 30
        self.updown_dist = []
        for t in range(n):
            d = [[INF, INF] for _ in range(n)]
            d[t] = [0, 0]
            q = deque([(t, UP_PHASE), (t, DOWN_PHASE)])
            while q:
                v, phase = q.popleft()
                for u in nbrs[v]:
                    # Predecessor states (u, ph) that can legally step to (v, phase).
                    if phase == UP_PHASE and self.is_up(u, v):
                        preds = (UP_PHASE,)
                    elif phase == DOWN_PHASE and not self.is_up(u, v):
                        preds = (UP_PHASE, DOWN_PHASE)
                    else:
                        continue
                    for ph in preds:
                        if d[u][ph] == INF:
                            d[u][ph] = d[v][phase] + 1
                            q.append((u, ph))
            self.updown_dist.append(d)

        # updown[u][t][phase] = tuple of (port, next_phase) that make progress.
        self.updown = []
        for u in range(n):
            row = []
            for t in range(n):
                per_phase = []
                for phase in (UP_PHASE, DOWN_PHASE):
                    here = self.updown_dist[t][u][phase]
                    opts = []
                    for p, v in enumerate(nbrs[u]):
                        up = self.is_up(u, v)
                        if phase == DOWN_PHASE and up:
                            continue  # once you've gone down you may never go up again
                        nxt = UP_PHASE if up else DOWN_PHASE
                        if self.updown_dist[t][v][nxt] == here - 1:
                            opts.append((p, nxt))
                    per_phase.append(tuple(opts))
                row.append(per_phase)
            self.updown.append(row)

    def updown_path_length(self, s: int, t: int) -> int:
        return self.updown_dist[t][s][UP_PHASE]


def grid_kind(topo: Topology) -> str:
    """'mesh' or 'torus' if ``topo`` is exactly one of them, else 'other'.

    Dimension-order routing (``dor``) only makes sense on these two, because
    it relies on every router having a +/- neighbour in each dimension.
    """
    from .topology import mesh, torus

    edges = set(topo.edges)
    if edges == set(mesh(topo.n).edges):
        return "mesh"
    if topo.n >= 3 and edges == set(torus(topo.n).edges):
        return "torus"
    return "other"


# ---------------------------------------------------------------------------
# Deadlock analysis
# ---------------------------------------------------------------------------


def channel_dependency_graph(tables: RoutingTables, algorithm: str):
    """Channel dependency graph of a routing function, as a networkx.DiGraph.

    Vertices are directed channels (u, v). There is an arc (a, u) -> (u, v)
    when some packet can hold channel a->u while waiting for u->v. For
    ``adaptive`` routing, Duato's theorem only needs the *escape* sub-network
    to be acyclic, so for that algorithm this returns the up*/down* CDG.
    """
    import networkx as nx

    topo, n, nbrs = tables.topo, tables.topo.num_nodes, tables.topo.neighbors
    g = nx.DiGraph()
    if algorithm == "bfs":
        for t in range(n):
            for a in range(n):
                if a == t:
                    continue
                u = nbrs[a][tables.bfs[a][t]]
                if u != t:
                    v = nbrs[u][tables.bfs[u][t]]
                    g.add_edge((a, u), (u, v))
    elif algorithm in ("updown", "adaptive"):
        # Every legal (phase-respecting) consecutive pair of hops, for every
        # destination. This is a superset of the dependencies that actually
        # occur, so if it is acyclic the real CDG is too.
        for t in range(n):
            for a in range(n):
                for phase in (UP_PHASE, DOWN_PHASE):
                    for p, ph2 in tables.updown[a][t][phase]:
                        u = nbrs[a][p]
                        if u == t:
                            continue
                        for p2, _ in tables.updown[u][t][ph2]:
                            g.add_edge((a, u), (u, nbrs[u][p2]))
    else:
        raise ValueError(algorithm)
    return g


def is_deadlock_free(tables: RoutingTables, algorithm: str) -> bool:
    """True if the (escape) CDG has no cycle, which guarantees deadlock freedom."""
    import networkx as nx

    return nx.is_directed_acyclic_graph(channel_dependency_graph(tables, algorithm))
