"""Analytical (simulation-free) metrics for a topology.

Hop count and diameter are what the 2023 thesis reported. On their own they
can't say anything about throughput, so this module also computes channel
load: how much traffic each directed link carries when every packet takes a
minimal path and each flow is split evenly over all of its shortest paths.
The busiest channel limits throughput. If it carries ``gamma_max`` flits
per cycle for each flit/cycle injected per node, the network saturates at an
injection rate of ``1 / gamma_max`` (Dally & Towles, *Principles and
Practices of Interconnection Networks*, ch. 3). Each router can also eject
only one packet per cycle, which caps hotspot traffic.

The result is reported as ``throughput_est``. It is an *estimate*, not a
hard bound: adaptive routing can balance load better than an even split
(the simulator sometimes beats it), and real router microarchitecture
usually falls short of it. It costs milliseconds to compute, though, so the
topology search can afford to call it thousands of times. Every conclusion
drawn from it is re-checked in the cycle-level simulator.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from .topology import Topology


def shortest_paths(topo: Topology) -> tuple[np.ndarray, np.ndarray]:
    """All-pairs hop distances and shortest-path counts, via BFS from each node.

    Returns:
        dist:  dist[s, t] = minimum number of hops from s to t.
        count: count[s, t] = number of distinct minimal paths from s to t.
    """
    n = topo.num_nodes
    nbrs = topo.neighbors
    dist = np.full((n, n), -1, dtype=np.int64)
    count = np.zeros((n, n), dtype=np.float64)
    for s in range(n):
        dist[s, s], count[s, s] = 0, 1.0
        q = deque([s])
        while q:
            u = q.popleft()
            for v in nbrs[u]:
                if dist[s, v] < 0:  # first time we reach v: it's one hop further
                    dist[s, v] = dist[s, u] + 1
                    q.append(v)
                if dist[s, v] == dist[s, u] + 1:  # u is on a shortest path to v
                    count[s, v] += count[s, u]
    if (dist < 0).any():
        raise ValueError(f"{topo.name} is disconnected")
    return dist, count


def uniform_traffic(num_nodes: int) -> np.ndarray:
    """Traffic matrix where each node spreads its packets evenly over all others."""
    t = np.full((num_nodes, num_nodes), 1.0 / (num_nodes - 1))
    np.fill_diagonal(t, 0.0)
    return t


def directed_channels(topo: Topology) -> list[tuple[int, int]]:
    """Every link as two one-way channels (a router-to-router link is full duplex)."""
    return [(u, v) for u, v in topo.edges] + [(v, u) for u, v in topo.edges]


def channel_loads(topo: Topology, traffic: np.ndarray | None = None, paths=None) -> dict[tuple[int, int], float]:
    """Load on every directed channel per unit injection rate.

    ``traffic[s, t]`` is the fraction of node s's packets that go to t (each
    row sums to 1, or to 0 for a node that sends nothing). Each (s, t) flow is
    split evenly over all of its minimal paths, so channel u->v gets the share
    count[s, u] * count[v, t] / count[s, t] whenever u->v lies on a minimal
    s -> t path, i.e. dist[s, u] + 1 + dist[v, t] == dist[s, t].
    """
    dist, count = paths if paths is not None else shortest_paths(topo)
    if traffic is None:
        traffic = uniform_traffic(topo.num_nodes)
    chans = directed_channels(topo)
    U = np.array([u for u, _ in chans])
    V = np.array([v for _, v in chans])
    weight = traffic / count  # per-path share of each flow

    loads = np.empty(len(chans))
    chunk = 64  # bounds memory at chunk * n^2 floats
    for i in range(0, len(chans), chunk):
        u, v = U[i : i + chunk], V[i : i + chunk]
        # on_path[e, s, t]: channel e lies on some minimal s -> t path
        on_path = dist[:, u].T[:, :, None] + 1 + dist[v, :][:, None, :] == dist[None, :, :]
        paths_through = count[:, u].T[:, :, None] * count[v, :][:, None, :]
        loads[i : i + chunk] = (on_path * paths_through * weight[None]).sum(axis=(1, 2))
    return dict(zip(chans, loads))


def throughput_estimate(topo: Topology, traffic: np.ndarray | None = None, paths=None) -> float:
    """Saturation injection rate (packets/node/cycle) under even-split minimal routing.

    Limited by the busiest channel and by each router's ejection port (one
    packet per cycle), whichever saturates first.
    """
    if traffic is None:
        traffic = uniform_traffic(topo.num_nodes)
    max_channel = max(channel_loads(topo, traffic, paths).values())
    max_eject = traffic.sum(axis=0).max()  # packets/cycle arriving at the busiest sink, per unit rate
    return float(1.0 / max(max_channel, max_eject))


def summary(topo: Topology, traffic: np.ndarray | None = None) -> dict:
    """Headline static metrics for one topology (one row of a results table)."""
    dist, count = shortest_paths(topo)
    n = topo.num_nodes
    if traffic is None:
        traffic = uniform_traffic(n)
    loads = channel_loads(topo, traffic, (dist, count))
    total_flow = traffic.sum()
    max_load = max(loads.values())
    # Expected distance a packet travels, in hops and in physical wire (tile
    # pitches), with each flow split evenly over its minimal paths. Wire per
    # packet is a first-order proxy for link energy per packet.
    exp_hops = sum(loads.values()) / total_flow
    exp_wire = sum(load * topo.link_length(u, v) for (u, v), load in loads.items()) / total_flow
    return {
        "topology": topo.name,
        "n": topo.n,
        "nodes": n,
        "links": len(topo.edges),
        "max_radix": max(topo.degrees),
        "diameter": int(dist.max()),
        "avg_hops": float(dist.sum() / (n * (n - 1))),
        "avg_hops_traffic": float(exp_hops),
        "total_wire": topo.total_wire_length,
        "max_link_length": topo.max_link_length,
        "max_channel_load": float(max_load),
        "throughput_est": float(1.0 / max(max_load, traffic.sum(axis=0).max())),
        "wire_per_packet": float(exp_wire),
    }
