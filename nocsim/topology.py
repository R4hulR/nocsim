"""Network-on-Chip topologies on an n x n grid of tiles.

Every topology here is a 2D mesh plus some extra links. That gives one common
frame for the three designs we compare:

* ``mesh``           - the plain 2D mesh (no extra links).
* ``torus``          - the mesh plus a wraparound link on every row and column.
* ``modified_torus`` - the topology proposed in the 2023 thesis: corners keep
  their torus wraparounds, but most boundary nodes are "folded" onto another
  node on the *same* edge instead of wrapping to the opposite edge.

In a mesh, boundary routers have unused ports: corners have 2, the other edge
nodes have 1, so there are 4n spare ports in total. A torus and the modified
torus both spend those ports on 2n extra links, so every router has radix 4.
They differ only in *which* spare ports get paired up. ``from_boundary_links``
builds any such pairing, and that is the design space ``nocsim.search``
explores.

Nodes are numbered row-major: node ``r * n + c`` sits at row ``r``, column
``c``. Physical wire length is measured in tile pitches (Manhattan distance on
the floorplan). A wraparound link on an n x n die is therefore n - 1 tiles
long.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

Edge = tuple[int, int]


def _norm(u: int, v: int) -> Edge:
    """Undirected edge in canonical (small, large) order."""
    return (u, v) if u < v else (v, u)


@dataclass(frozen=True)
class Topology:
    """An undirected network of routers placed on an n x n grid.

    Attributes:
        name:  Human-readable label used in plots and tables.
        n:     Side length of the grid; the network has n * n routers.
        edges: Sorted tuple of undirected links (u, v) with u < v.
    """

    name: str
    n: int
    edges: tuple[Edge, ...]
    # Free-form metadata, e.g. how a searched topology was found.
    info: dict = field(default_factory=dict, compare=False, hash=False)

    # ----- geometry --------------------------------------------------------

    @property
    def num_nodes(self) -> int:
        return self.n * self.n

    def coord(self, node: int) -> tuple[int, int]:
        """(row, col) of a node on the floorplan."""
        return divmod(node, self.n)

    def node(self, row: int, col: int) -> int:
        return row * self.n + col

    def link_length(self, u: int, v: int) -> int:
        """Physical wire length of link u-v in tile pitches."""
        (r1, c1), (r2, c2) = self.coord(u), self.coord(v)
        return abs(r1 - r2) + abs(c1 - c2)

    # ----- connectivity ----------------------------------------------------

    @cached_property
    def neighbors(self) -> tuple[tuple[int, ...], ...]:
        """neighbors[u] is the tuple of routers directly linked to u.

        The position of a neighbor in this tuple is the output port the
        simulator uses to reach it, so the order is fixed (sorted).
        """
        adj: list[list[int]] = [[] for _ in range(self.num_nodes)]
        for u, v in self.edges:
            adj[u].append(v)
            adj[v].append(u)
        return tuple(tuple(sorted(a)) for a in adj)

    @cached_property
    def degrees(self) -> tuple[int, ...]:
        return tuple(len(a) for a in self.neighbors)

    @property
    def total_wire_length(self) -> int:
        return sum(self.link_length(u, v) for u, v in self.edges)

    @property
    def max_link_length(self) -> int:
        return max(self.link_length(u, v) for u, v in self.edges)

    def extra_links(self) -> list[Edge]:
        """Links that are not part of the underlying 2D mesh."""
        mesh_edges = set(_mesh_edges(self.n))
        return [e for e in self.edges if e not in mesh_edges]

    def to_networkx(self):
        """Export as a networkx.Graph with 'pos' and 'length' attributes."""
        import networkx as nx

        g = nx.Graph()
        for u in range(self.num_nodes):
            r, c = self.coord(u)
            g.add_node(u, pos=(c, -r))
        for u, v in self.edges:
            g.add_edge(u, v, length=self.link_length(u, v))
        return g

    def to_dict(self) -> dict:
        return {"name": self.name, "n": self.n, "edges": [list(e) for e in self.edges], "info": self.info}

    @staticmethod
    def from_dict(d: dict) -> "Topology":
        return Topology(d["name"], d["n"], tuple(sorted(_norm(*e) for e in d["edges"])), d.get("info", {}))


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _mesh_edges(n: int) -> list[Edge]:
    edges = []
    for r in range(n):
        for c in range(n):
            u = r * n + c
            if c + 1 < n:
                edges.append((u, u + 1))
            if r + 1 < n:
                edges.append((u, u + n))
    return edges


def _build(name: str, n: int, extra: list[Edge], info: dict | None = None) -> Topology:
    """Mesh plus ``extra`` links. Rejects self-loops and duplicate links."""
    edges = set(_mesh_edges(n))
    for u, v in extra:
        e = _norm(u, v)
        if u == v:
            raise ValueError(f"self-loop at node {u}")
        if e in edges:
            raise ValueError(f"duplicate link {e}")
        edges.add(e)
    return Topology(name, n, tuple(sorted(edges)), info or {})


def mesh(n: int) -> Topology:
    """Plain 2D mesh (the CLICHE layout)."""
    return _build("Mesh", n, [])


def torus(n: int) -> Topology:
    """2D torus (k-ary 2-cube): a wraparound link on every row and column."""
    if n < 3:
        raise ValueError("a torus needs n >= 3 (otherwise wraparounds duplicate mesh links)")
    extra = []
    for i in range(n):
        extra.append((i * n, i * n + n - 1))  # row i: left end <-> right end
        extra.append((i, (n - 1) * n + i))  # column i: top end <-> bottom end
    return _build("Torus", n, extra)


def _fold_pairs(n: int) -> tuple[list[tuple[int, int]], int | None]:
    """How the modified torus pairs up positions 1..n-2 along one edge.

    The positions strictly between the two corners are split into two equal
    halves, and position i of the first half is linked to position i of the
    second half. When n is odd, the middle position is left out and keeps an
    ordinary torus wraparound instead.

    Returns (pairs, middle), e.g. n=5 -> ([(1, 3)], 2) and n=8 ->
    ([(1, 4), (2, 5), (3, 6)], None).
    """
    middle = (n - 1) // 2 if n % 2 == 1 else None
    positions = [p for p in range(1, n - 1) if p != middle]
    half = len(positions) // 2
    return list(zip(positions[:half], positions[half:])), middle


def modified_torus(n: int) -> Topology:
    """The modified torus from the 2023 thesis, generalised to any n >= 5.

    The rule, read off the thesis figures for 5x5 through 10x10 (and
    identical to the hand-written 5x5 adjacency list in the original code):

    * Corner routers keep both torus wraparound links.
    * When n is odd, the middle row and the middle column keep their
      wraparounds.
    * Every other boundary router is linked to a router on the *same* edge,
      (n - 1) // 2 positions away (``_fold_pairs``). These links are shorter
      than a wraparound, which saves wire.

    Every router still has exactly 4 links, just like a torus.
    """
    if n < 5:
        raise ValueError("the modified torus needs n >= 5 (for n = 4 the fold links duplicate mesh links)")
    pairs, middle = _fold_pairs(n)
    last = n - 1
    node = lambda r, c: r * n + c  # noqa: E731

    extra = [
        (node(0, 0), node(0, last)), (node(last, 0), node(last, last)),  # top / bottom rows
        (node(0, 0), node(last, 0)), (node(0, last), node(last, last)),  # left / right cols
    ]
    if middle is not None:
        extra.append((node(middle, 0), node(middle, last)))  # middle row wraparound
        extra.append((node(0, middle), node(last, middle)))  # middle column wraparound
    for a, b in pairs:
        extra.append((node(0, a), node(0, b)))  # top edge
        extra.append((node(last, a), node(last, b)))  # bottom edge
        extra.append((node(a, 0), node(b, 0)))  # left edge
        extra.append((node(a, last), node(b, last)))  # right edge
    return _build("Modified torus", n, extra)


# ---------------------------------------------------------------------------
# The boundary-port design space shared by torus, modified torus and search
# ---------------------------------------------------------------------------


def boundary_ports(n: int) -> list[int]:
    """Spare router ports left over on a mesh's boundary.

    Every boundary node appears once per unused port, so corners appear twice.
    The list has 4n entries. Any perfect matching of these ports (with no
    self-loops and no duplicate links) gives a topology where every router has
    radix 4.
    """
    ports = []
    for r in range(n):
        for c in range(n):
            spare = 4 - sum((r > 0, r < n - 1, c > 0, c < n - 1))
            ports.extend([r * n + c] * spare)
    return ports


def from_boundary_links(n: int, links: list[Edge], name: str = "Searched", info: dict | None = None) -> Topology:
    """Mesh plus an arbitrary set of extra links between boundary routers."""
    return _build(name, n, list(links), info)


def baseline(name: str, n: int) -> Topology:
    """Look up one of the three reference topologies by name."""
    builders = {"mesh": mesh, "torus": torus, "modified": modified_torus, "modified_torus": modified_torus}
    return builders[name.lower()](n)
