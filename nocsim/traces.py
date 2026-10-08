"""Dependency-driven replay of application traces (Netrace PARSEC traces).

Netrace (Hestness et al., NoCArc 2010) recorded the packets a 64-core,
8x8-mesh CMP sent while running PARSEC, together with *dependencies*: a read
response cannot be sent before its request arrives, a dependent request
cannot issue before an earlier response returns, and so on. Replaying the
trace with those dependencies, rather than at the recorded timestamps, lets
a faster network finish the application sooner and a slower one later.
Completion time is therefore a proxy for application runtime.

Convert a trace with ``tools/netrace_dump`` first, then load it here.

Timing model (``timing``):

``"netrace"``
    The reference Netrace reader's rule: a packet is injected once all its
    dependencies are delivered *and* its recorded cycle has been reached.
    Conservative: a network faster than the original can't make the
    application finish earlier.

``"relative"`` (default)
    Keeps each dependency's *compute gap*. The recorded gap between a parent
    packet and its child includes the original network's latency for the
    parent, so that latency (estimated as the parent's zero-load latency on
    the 8x8 mesh the trace was captured on) is subtracted, and the remainder
    is added to the parent's delivery time in *our* network. Root packets
    (no dependencies inside the replayed window) inject at their recorded
    cycle.

``speedup`` divides all recorded times and gaps, emulating cores that
compute faster and so stress the network harder. The traces are light at
speedup 1 (about 0.001 packets/node/cycle), so results are reported at
several speedups.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from .simulator import HOPS, PID, T_CREATE, T_INJECT, WIRE


@dataclass
class TracePacket:
    pid: int
    cycle: int
    src: int
    dst: int
    flits: int
    parents: list
    children: list


def load_netrace_dump(path: str, max_packets: int | None = None, flit_bytes: int = 16) -> tuple[dict, list[TracePacket]]:
    """Read a ``netrace_dump`` text file. Returns (header, packets in file order)."""
    header, pkts = {}, []
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                parts = line[1:].split()
                if parts:
                    header[parts[0]] = " ".join(parts[1:])
                continue
            pid, cycle, src, dst, size, _type, ndeps, deps = line.split()
            children = [] if deps == "-" else [int(x) for x in deps.split(",")]
            pkts.append(TracePacket(int(pid), int(cycle), int(src), int(dst),
                                    max(1, math.ceil(int(size) / flit_bytes)), [], children))
            if max_packets and len(pkts) >= max_packets:
                break
    # Netrace stores, for each packet, the ids of the packets that depend on
    # it. Invert that into parent lists, keeping only edges inside the window.
    index = {p.pid: p for p in pkts}
    for p in pkts:
        p.children = [c for c in p.children if c in index]
        for c in p.children:
            index[c].parents.append(p.pid)
    return header, pkts


class TraceSource:
    """Traffic source for ``simulate_trace`` that releases packets as their dependencies clear."""

    def __init__(self, packets: list[TracePacket], n: int = 8, timing: str = "relative",
                 speedup: float = 1.0, ref_hop_cycles: int = 2):
        if timing not in ("relative", "netrace"):
            raise ValueError(timing)
        self.packets = packets
        self.index = {p.pid: p for p in packets}
        self.n, self.timing, self.speedup, self.ref_hop = n, timing, speedup, ref_hop_cycles
        self.t0 = min(p.cycle for p in packets)
        self.max_size = max(p.flits for p in packets)

    # -- helpers -------------------------------------------------------------
    def _rec(self, p: TracePacket) -> float:
        """Recorded cycle, relative to the window start and scaled by speedup."""
        return (p.cycle - self.t0) / self.speedup

    def _ref_latency(self, p: TracePacket) -> int:
        """Zero-load latency of p on the original 8x8 mesh (subtracted from gaps)."""
        (r1, c1), (r2, c2) = divmod(p.src, self.n), divmod(p.dst, self.n)
        return (abs(r1 - r2) + abs(c1 - c2)) * self.ref_hop + p.flits - 1

    # -- source interface used by the simulator engine -----------------------
    def reset(self, init_phase: int) -> None:
        self.init_phase = init_phase
        self.waiting = {p.pid: len(p.parents) for p in self.packets}
        self.ready_at = {p.pid: 0.0 for p in self.packets}  # max over cleared parents
        self.heap: list = []  # (inject cycle, pid)
        self.done_at: dict[int, int] = {}
        self.latencies: list[int] = []
        self.net_latencies: list[int] = []
        self.hops = self.wire = 0
        self.delivered = 0
        for p in self.packets:
            if not p.parents:
                heapq.heappush(self.heap, (math.ceil(self._rec(p)), p.pid))

    def generate(self, cyc: int, srcq) -> None:
        heap = self.heap
        while heap and heap[0][0] <= cyc:
            t, pid = heapq.heappop(heap)
            p = self.index[pid]
            pkt = [p.src, p.dst, t, -1, 0, 0, self.init_phase, True, p.flits, pid]
            if p.src == p.dst:  # local access: never enters the network
                pkt[T_INJECT] = t
                self.ejected(pkt, t)
            else:
                srcq[p.src].append(pkt)

    def ejected(self, pkt, done: int) -> None:
        pid = pkt[PID]
        self.done_at[pid] = done
        self.delivered += 1
        self.latencies.append(done - pkt[T_CREATE])
        self.net_latencies.append(done - pkt[T_INJECT])
        self.hops += pkt[HOPS]
        self.wire += pkt[WIRE]
        parent = self.index[pid]
        for cid in parent.children:
            child = self.index[cid]
            if self.timing == "relative":
                gap = max(0.0, (child.cycle - parent.cycle - self._ref_latency(parent)) / self.speedup)
                t = done + gap
            else:
                t = max(done, self._rec(child))
            if t > self.ready_at[cid]:
                self.ready_at[cid] = t
            self.waiting[cid] -= 1
            if self.waiting[cid] == 0:
                heapq.heappush(self.heap, (max(math.ceil(self.ready_at[cid]), done + 1), cid))

    def finished(self, cyc: int) -> bool:
        return self.delivered == len(self.packets)

    def next_event(self):
        return self.heap[0][0] if self.heap else None

    def summary(self) -> dict:
        lat = sorted(self.latencies)
        k = len(lat)
        return {
            "packets": len(self.packets),
            "delivered": self.delivered,
            "completion": max(self.done_at.values()) if self.done_at else 0,
            "avg_latency": sum(lat) / k if k else float("nan"),
            "p99_latency": float(lat[min(k - 1, int(0.99 * k))]) if k else float("nan"),
            "avg_network_latency": sum(self.net_latencies) / k if k else float("nan"),
            "avg_hops": self.hops / k if k else float("nan"),
            "avg_wire": self.wire / k if k else float("nan"),
        }
