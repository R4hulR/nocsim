"""Cycle-level packet simulator for on-chip networks.

The 2023 thesis measured "latency" as the hop count of a BFS path, with a
``time.sleep`` per hop. That is the zero-load latency at best, and it can't
show congestion, throughput or deadlock. This module simulates actual router
hardware, cycle by cycle, following the methodology of Dally & Towles (ch.
23):

Router model (one per tile)
    * Input-queued router. Each input port has ``num_vcs`` virtual channels
      (VCs), and each VC is a FIFO holding ``buffer_depth`` packets. One extra
      input port is the local injection queue.
    * Single-flit packets with credit-based flow control. A packet moves only
      if the downstream VC has a free slot, so packets are never dropped and
      buffers can't overflow.
    * Each cycle, each output port sends at most one packet and each input
      port forwards at most one packet (crossbar limits). Ejection to the
      local core is also one packet per cycle.
    * Separable allocation with a rotating (round-robin) starting input for
      fairness.
    * Hop latency = ``router_delay`` cycles in the router pipeline + link
      traversal. A link takes 1 cycle (``link_latency="unit"``), or
      ``ceil(length / wire_reach)`` cycles for long wires
      (``link_latency="wire"``), so long wraparound links cost extra time as
      they would on real silicon. With the defaults a hop costs 2 cycles.

Measurement methodology
    * Open-loop: every node generates packets by a Bernoulli process at
      ``rate`` packets/node/cycle into an unbounded source queue, so the
      offered load does not depend on how congested the network is.
    * Warm up for ``warmup`` cycles, tag the packets created during the next
      ``measure`` cycles, then keep running until every tagged packet is
      delivered. Latency counts from packet creation to ejection, so it
      includes time spent queueing at the source.
    * The point is reported as ``saturated`` if tagged packets are still in
      flight ``drain_limit`` cycles after the window, or if the network
      delivered less than 95% of the offered load during the window.
    * If packets are in the network but none has moved for
      ``deadlock_window`` cycles, the run is reported as ``deadlocked``.

Simplifications (documented so results are read correctly): single-flit
packets (no wormhole/multi-flit serialization), credits are returned at the
end of the cycle the slot frees up, and allocation is greedy and separable,
not iSLIP. All of these affect every topology equally.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import asdict, dataclass

from . import traffic as traffic_mod
from .routing import UP_PHASE, RoutingTables
from .topology import Topology

ADAPTIVE = -1  # packet phase meaning "not (yet) in the escape network"

# Packet fields. A packet is a plain list because it is created and touched
# millions of times; attribute access on an object is noticeably slower.
SRC, DST, T_CREATE, T_INJECT, HOPS, WIRE, PHASE, TAGGED = range(8)


@dataclass
class SimConfig:
    routing: str = "adaptive"  # "adaptive" | "updown" | "bfs" (see nocsim.routing)
    num_vcs: int = 3  # adaptive uses VC 0 as escape, VCs 1.. as adaptive
    router_delay: int = 1  # pipeline cycles per router, on top of the link
    escape_return: bool = True  # adaptive: may escape packets go back to adaptive VCs?
    buffer_depth: int = 4  # packets per VC buffer
    link_latency: str = "unit"  # "unit" | "wire"
    wire_reach: float = 2.0  # tile pitches a signal crosses per cycle ("wire" mode)
    warmup: int = 1000
    measure: int = 3000
    drain_limit: int = 6000
    deadlock_window: int = 1000
    seed: int = 1


@dataclass
class SimResult:
    topology: str
    pattern: str
    routing: str
    rate: float  # requested injection rate (packets/node/cycle)
    offered: float  # packets actually generated per node per cycle
    accepted: float  # packets delivered per node per cycle in the window
    avg_latency: float  # creation -> ejection, cycles (nan if saturated)
    avg_network_latency: float  # injection -> ejection, cycles
    p99_latency: float
    avg_hops: float
    avg_wire: float  # tile pitches of wire crossed per packet (energy proxy)
    escape_fraction: float  # share of packets that fell back to the escape VC
    saturated: bool
    deadlocked: bool
    cycles: int

    def to_dict(self) -> dict:
        return asdict(self)


def simulate(
    topo: Topology,
    pattern: str,
    rate: float,
    cfg: SimConfig | None = None,
    tables: RoutingTables | None = None,
) -> SimResult:
    """Run one simulation at a single injection rate and return its statistics."""
    cfg = cfg or SimConfig()
    tables = tables or RoutingTables(topo)
    algo, V, B = cfg.routing, cfg.num_vcs, cfg.buffer_depth
    if algo not in ("adaptive", "updown", "bfs"):
        raise ValueError(f"unknown routing {algo!r}")
    if algo == "adaptive" and V < 2:
        raise ValueError("adaptive routing needs >= 2 VCs (one escape + one adaptive)")

    rng = random.Random(cfg.seed)
    rand = rng.random
    dest = traffic_mod.sampler(pattern, topo.n)
    N = topo.num_nodes
    nbrs = topo.neighbors
    deg = [len(a) for a in nbrs]

    # rev_port[u][p]: the input port index at neighbour v = nbrs[u][p] that the
    # link from u arrives on (equivalently, v's output port back to u).
    rev_port = [[nbrs[v].index(u) for v in nbrs[u]] for u in range(N)]
    link_len = [[topo.link_length(u, v) for v in nbrs[u]] for u in range(N)]
    if cfg.link_latency == "unit":
        link_lat = [[1] * deg[u] for u in range(N)]
    elif cfg.link_latency == "wire":
        link_lat = [[max(1, math.ceil(L / cfg.wire_reach)) for L in link_len[u]] for u in range(N)]
    else:
        raise ValueError(cfg.link_latency)

    # Router state. Input buffer index ``p * V + vc`` is VC ``vc`` of input
    # port ``p``. The final index (deg * V) is the local injection queue.
    inbufs = [[deque() for _ in range(deg[u] * V + 1)] for u in range(N)]
    # credits[u][p * V + vc]: free slots in the downstream buffer that output
    # port p, VC vc of router u feeds.
    credits = [[B] * (deg[u] * V) for u in range(N)]
    occupancy = [0] * N  # packets buffered at each router (lets us skip idle routers)
    rr = [0] * N  # round-robin pointer per router
    srcq = [deque() for _ in range(N)]  # unbounded source queues (open loop)

    hop_lat = [[cfg.router_delay + L for L in row] for row in link_lat]  # cycles per hop, by output port
    pipe_len = max(max(r) for r in hop_lat) + 1
    pipeline: list[list] = [[] for _ in range(pipe_len)]  # packets on the wire, by arrival slot

    minimal, bfs_port, updown = tables.minimal, tables.bfs, tables.updown
    init_phase = UP_PHASE if algo == "updown" else ADAPTIVE
    escape_return = cfg.escape_return
    vc_range_all = range(V)
    vc_range_adaptive = range(1, V)

    win_start, win_end = cfg.warmup, cfg.warmup + cfg.measure
    hard_stop = win_end + cfg.drain_limit
    generated_in_window = delivered_in_window = 0
    outstanding_tagged = 0
    latencies: list[int] = []
    net_latencies: list[int] = []
    hops_sum = wire_sum = escaped = 0
    in_network = 0
    last_progress = 0
    deadlocked = False

    cyc = 0
    while True:
        # 1) Packets whose link traversal finishes this cycle enter input buffers.
        slot = pipeline[cyc % pipe_len]
        for v, bi, pkt in slot:
            inbufs[v][bi].append(pkt)
            occupancy[v] += 1
        slot.clear()

        # 2) Traffic generation and injection into the local router.
        tagging = win_start <= cyc < win_end
        for u in range(N):
            if rand() < rate:
                d = dest(u, rng)
                if d >= 0:
                    srcq[u].append([u, d, cyc, -1, 0, 0, init_phase, tagging])
                    if tagging:
                        generated_in_window += 1
                        outstanding_tagged += 1
            if srcq[u]:
                inj = inbufs[u][-1]
                if len(inj) < B:
                    pkt = srcq[u].popleft()
                    pkt[T_INJECT] = cyc
                    inj.append(pkt)
                    occupancy[u] += 1
                    in_network += 1

        # 3) Route computation + switch allocation + switch traversal.
        credit_returns = []
        for u in range(N):
            if not occupancy[u]:
                continue
            d_u = deg[u]
            bufs = inbufs[u]
            nb = len(bufs)
            cr = credits[u]
            out_used = [False] * (d_u + 1)  # last entry is the ejection port
            in_used = [False] * (d_u + 1)  # last entry is the injection port
            start = rr[u]
            rr[u] = start + 1 if start + 1 < nb else 0

            for k in range(nb):
                bi = start + k
                if bi >= nb:
                    bi -= nb
                q = bufs[bi]
                if not q:
                    continue
                ip = bi // V if bi < d_u * V else d_u
                if in_used[ip]:
                    continue
                pkt = q[0]
                dst = pkt[DST]

                if dst == u:  # ---- ejection
                    if out_used[d_u]:
                        continue
                    out_used[d_u] = in_used[ip] = True
                    q.popleft()
                    occupancy[u] -= 1
                    in_network -= 1
                    last_progress = cyc
                    if ip < d_u:
                        credit_returns.append((nbrs[u][ip], rev_port[u][ip] * V + bi % V))
                    if win_start <= cyc < win_end:
                        delivered_in_window += 1
                    if pkt[TAGGED]:
                        outstanding_tagged -= 1
                        latencies.append(cyc - pkt[T_CREATE])
                        net_latencies.append(cyc - pkt[T_INJECT])
                        hops_sum += pkt[HOPS]
                        wire_sum += pkt[WIRE]
                        if algo == "adaptive" and pkt[PHASE] != ADAPTIVE:
                            escaped += 1
                    continue

                # ---- pick an output port and VC
                best_p = best_vc = -1
                best_c = 0
                new_phase = pkt[PHASE]
                phase = pkt[PHASE]
                if algo == "bfs":
                    p = bfs_port[u][dst]
                    if not out_used[p]:
                        for vc in vc_range_all:
                            c = cr[p * V + vc]
                            if c > best_c:
                                best_p, best_vc, best_c = p, vc, c
                elif phase == ADAPTIVE:
                    # Minimal adaptive: least-congested (most credits) minimal port
                    # on any adaptive VC; ties broken uniformly at random.
                    ties = 0
                    for p in minimal[u][dst]:
                        if out_used[p]:
                            continue
                        base = p * V
                        for vc in vc_range_adaptive:
                            c = cr[base + vc]
                            if c > best_c:
                                best_p, best_vc, best_c, ties = p, vc, c, 1
                            elif c == best_c and c > 0:
                                ties += 1
                                if rand() * ties < 1.0:
                                    best_p, best_vc = p, vc
                    if best_p < 0:
                        # Every minimal adaptive option is full: fall back to the
                        # deadlock-free up*/down* escape network on VC 0.
                        for p, nph in updown[u][dst][UP_PHASE]:
                            c = cr[p * V]
                            if c > best_c and not out_used[p]:
                                best_p, best_vc, best_c, new_phase = p, 0, c, nph
                else:
                    # Already following up*/down* (escape packet, or updown routing).
                    if algo == "adaptive" and escape_return:
                        # Virtual cut-through buffers hold whole packets, so an
                        # escape packet may hop back onto an adaptive VC whenever
                        # one is free (Duato's condition still holds).
                        for p in minimal[u][dst]:
                            if out_used[p]:
                                continue
                            for vc in vc_range_adaptive:
                                c = cr[p * V + vc]
                                if c > best_c:
                                    best_p, best_vc, best_c, new_phase = p, vc, c, ADAPTIVE
                    vcs = (0,) if algo == "adaptive" else vc_range_all
                    for p, nph in updown[u][dst][phase] if best_p < 0 else ():
                        if out_used[p]:
                            continue
                        for vc in vcs:
                            c = cr[p * V + vc]
                            if c > best_c:
                                best_p, best_vc, best_c, new_phase = p, vc, c, nph

                if best_p < 0:
                    continue  # blocked this cycle; try again next cycle

                # ---- traverse switch and link
                q.popleft()
                out_used[best_p] = in_used[ip] = True
                occupancy[u] -= 1
                last_progress = cyc
                cr[best_p * V + best_vc] -= 1  # reserve the downstream slot
                if ip < d_u:  # free our slot: credit goes back upstream
                    credit_returns.append((nbrs[u][ip], rev_port[u][ip] * V + bi % V))
                pkt[PHASE] = new_phase
                pkt[HOPS] += 1
                pkt[WIRE] += link_len[u][best_p]
                v = nbrs[u][best_p]
                arrive = (cyc + hop_lat[u][best_p]) % pipe_len
                pipeline[arrive].append((v, rev_port[u][best_p] * V + best_vc, pkt))

        for node, idx in credit_returns:
            credits[node][idx] += 1

        cyc += 1
        if in_network and cyc - last_progress > cfg.deadlock_window:
            deadlocked = True
            break
        if cyc >= win_end and outstanding_tagged == 0:
            break
        if cyc >= hard_stop:
            break

    offered = generated_in_window / (N * cfg.measure)
    accepted = delivered_in_window / (N * cfg.measure)
    # Past saturation the network can't keep up: it delivers measurably less
    # than is offered and the source queues grow without bound. (Tagged
    # packets can still drain eventually, because source queues are FIFO, so
    # draining alone is not enough to prove the network is below saturation.)
    saturated = deadlocked or outstanding_tagged > 0 or accepted < 0.95 * offered
    nan = float("nan")
    n_meas = len(latencies)
    lat_sorted = sorted(latencies)
    return SimResult(
        topology=topo.name,
        pattern=pattern,
        routing=algo,
        rate=rate,
        offered=offered,
        accepted=accepted,
        avg_latency=nan if saturated or not n_meas else sum(latencies) / n_meas,
        avg_network_latency=nan if saturated or not n_meas else sum(net_latencies) / n_meas,
        p99_latency=nan if saturated or not n_meas else float(lat_sorted[min(n_meas - 1, int(0.99 * n_meas))]),
        avg_hops=hops_sum / n_meas if n_meas else nan,
        avg_wire=wire_sum / n_meas if n_meas else nan,
        escape_fraction=escaped / n_meas if n_meas else nan,
        saturated=saturated,
        deadlocked=deadlocked,
        cycles=cyc,
    )
