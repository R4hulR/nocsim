"""Cycle-level packet simulator for on-chip networks.

The 2023 thesis measured "latency" as the hop count of a BFS path, with a
``time.sleep`` per hop. That is the zero-load latency at best, and it can't
show congestion, throughput or deadlock. This module simulates actual router
hardware, cycle by cycle, following the methodology of Dally & Towles (ch.
23):

Router model (one per tile)
    * Input-queued router. Each input port has ``num_vcs`` virtual channels
      (VCs), and each VC is a FIFO holding ``buffer_depth`` flits. One extra
      input port is the local injection queue.
    * Packets are ``size`` flits long (1 by default) and move with virtual
      cut-through flow control and credits: a packet advances only if the
      downstream VC has room for all of it, so packets are never dropped and
      buffers can't overflow. A packet holds its input port and output link
      for ``size`` cycles (serialization), and its buffer slots are returned
      upstream when its tail leaves.
    * Each output port and each input port carries at most one flit per
      cycle (crossbar limits). Ejection to the local core is the same.
    * Separable allocation with a rotating (round-robin) starting input for
      fairness. Optionally (``vc_turnaround``) a single VC may only start a
      new packet every few cycles, which models routers such as BookSim's
      where each packet must win VC and switch allocation in turn.
    * Hop latency = ``router_delay`` cycles in the router pipeline + link
      traversal. A link takes 1 cycle (``link_latency="unit"``), or
      ``ceil(length / wire_reach)`` cycles for long wires
      (``link_latency="wire"``), so long wraparound links cost extra time as
      they would on real silicon. With the defaults a hop costs 2 cycles.

Two traffic sources share this router engine:

``simulate`` (synthetic, open loop)
    * Every node generates packets by a Bernoulli process at ``rate``
      packets/node/cycle into an unbounded source queue, so the offered load
      does not depend on how congested the network is.
    * Warm up for ``warmup`` cycles, tag the packets created during the next
      ``measure`` cycles, then keep running until every tagged packet is
      delivered. Latency counts from packet creation to ejection of the tail,
      so it includes time spent queueing at the source.
    * The point is reported as ``saturated`` if tagged packets are still in
      flight ``drain_limit`` cycles after the window, or if the network
      delivered less than 95% of the offered load during the window.

``simulate_trace`` (application traces, closed loop)
    * Replays a dependency-annotated trace (see ``nocsim.traces``): a packet
      is injected only once the packets it depends on have been delivered.
      Idle stretches with nothing in flight are skipped in one step.

In both, if packets are in the network but none has moved for
``deadlock_window`` cycles, the run is reported as ``deadlocked``.

Simplifications (documented so results are read correctly): virtual
cut-through rather than wormhole, credits for a packet are returned together
when its tail leaves, and allocation is greedy and separable, not iSLIP. All
of these affect every topology equally. The simulator is validated against
BookSim 2 in ``experiments/05_booksim_validation.py``.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import asdict, dataclass, field

from . import traffic as traffic_mod
from .routing import UP_PHASE, RoutingTables, grid_kind
from .topology import Topology

ADAPTIVE = -1  # packet phase meaning "not (yet) in the escape network"

# Packet fields. A packet is a plain list because it is created and touched
# millions of times; attribute access on an object is noticeably slower.
SRC, DST, T_CREATE, T_INJECT, HOPS, WIRE, PHASE, TAGGED, SIZE, PID = range(10)


@dataclass
class SimConfig:
    routing: str = "adaptive"  # "adaptive" | "updown" | "bfs" | "dor" (see nocsim.routing)
    num_vcs: int = 3  # adaptive uses VC 0 as escape, VCs 1.. as adaptive
    router_delay: int = 1  # pipeline cycles per router, on top of the link
    escape_return: bool = True  # adaptive: may escape packets go back to adaptive VCs?
    buffer_depth: int = 4  # flits per VC buffer (must hold the largest packet)
    packet_size: int = 1  # flits per packet for synthetic traffic
    vc_turnaround: int = 1  # min cycles between packets leaving the same VC (1 = no limit)
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


@dataclass
class Activity:
    """Event counts for energy models (see ``nocsim.power``)."""

    router_flits: int = 0  # flits switched through a router (incl. ejection)
    link_flits: int = 0  # flits sent over router-to-router links
    link_flit_tiles: int = 0  # sum over link traversals of flits x link length
    per_router_flits: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Router engine
# ---------------------------------------------------------------------------


def _run(topo: Topology, cfg: SimConfig, tables: RoutingTables, source, rng: random.Random) -> tuple[int, bool, Activity]:
    """Cycle-level engine shared by every traffic source.

    ``source`` supplies packets (``generate``), is told about deliveries
    (``ejected``), says when to stop (``finished``) and, optionally, when the
    next packet will appear (``next_event``) so idle time can be skipped.
    Returns (cycles simulated, deadlocked, activity counts).
    """
    algo, V, B = cfg.routing, cfg.num_vcs, cfg.buffer_depth
    if algo not in ("adaptive", "updown", "bfs", "dor"):
        raise ValueError(f"unknown routing {algo!r}")
    if algo == "adaptive" and V < 2:
        raise ValueError("adaptive routing needs >= 2 VCs (one escape + one adaptive)")
    is_torus = False
    if algo == "dor":
        kind = grid_kind(topo)
        if kind == "other":
            raise ValueError("dor routing needs a plain mesh or torus")
        if kind == "torus" and V % 2:
            raise ValueError("dor on a torus needs an even number of VCs (two dateline classes)")
        is_torus = kind == "torus"
    if source.max_size > B:
        raise ValueError(f"buffer_depth={B} cannot hold a {source.max_size}-flit packet (virtual cut-through)")

    rand = rng.random
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
    # credits[u][p * V + vc]: free flit slots in the downstream buffer that
    # output port p, VC vc of router u feeds.
    credits = [[B] * (deg[u] * V) for u in range(N)]
    # Cycle from which each output port (last entry: ejection) and each input
    # port (last entry: injection) is free again; > now means still busy
    # serializing an earlier packet.
    out_free = [[0] * (deg[u] + 1) for u in range(N)]
    in_free = [[0] * (deg[u] + 1) for u in range(N)]
    turnaround = cfg.vc_turnaround
    vc_free = [[0] * (deg[u] * V + 1) for u in range(N)] if turnaround > 1 else None
    inj_flits = [0] * N  # flits waiting in each injection queue
    occupancy = [0] * N  # packets buffered at each router (lets us skip idle routers)
    rr = [0] * N  # round-robin pointer per router
    srcq = [deque() for _ in range(N)]  # unbounded source queues
    credit_due: dict[int, list] = {}  # cycle -> [(node, buffer index, flits)]

    hop_lat = [[cfg.router_delay + L for L in row] for row in link_lat]  # cycles per hop, by output port
    pipe_len = max(max(r) for r in hop_lat) + 1
    pipeline: list[list] = [[] for _ in range(pipe_len)]  # packets on the wire, by arrival slot

    minimal, bfs_port, updown = tables.minimal, tables.bfs, tables.updown
    n = topo.n
    port_of = [{v: p for p, v in enumerate(nbrs[u])} for u in range(N)]  # neighbour id -> output port
    half_v = V // 2
    escape_return = cfg.escape_return
    vc_range_all = range(V)
    vc_range_adaptive = range(1, V)

    act = Activity(per_router_flits=[0] * N)
    per_router = act.per_router_flits
    router_flits = link_flits = link_flit_tiles = 0
    in_network = 0
    last_progress = 0
    deadlocked = False

    cyc = 0
    while True:
        # 0) Credits for slots freed by tails that have now left.
        due = credit_due.pop(cyc, None)
        if due:
            for node, idx, amount in due:
                credits[node][idx] += amount

        # 1) Packets whose link traversal finishes this cycle enter input buffers.
        slot = pipeline[cyc % pipe_len]
        for v, bi, pkt in slot:
            inbufs[v][bi].append(pkt)
            occupancy[v] += 1
        slot.clear()

        # 2) Traffic generation and injection into the local router.
        source.generate(cyc, srcq)
        for u in range(N):
            q = srcq[u]
            if q:
                size = q[0][SIZE]
                if inj_flits[u] + size <= B:
                    pkt = q.popleft()
                    pkt[T_INJECT] = cyc
                    inbufs[u][-1].append(pkt)
                    inj_flits[u] += size
                    occupancy[u] += 1
                    in_network += 1

        # 3) Route computation + switch allocation + switch traversal.
        for u in range(N):
            if not occupancy[u]:
                continue
            d_u = deg[u]
            bufs = inbufs[u]
            nb = len(bufs)
            cr = credits[u]
            ofree = out_free[u]
            ifree = in_free[u]
            vfree = vc_free[u] if vc_free is not None else None
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
                if ifree[ip] > cyc or (vfree is not None and vfree[bi] > cyc):
                    continue
                pkt = q[0]
                dst = pkt[DST]
                size = pkt[SIZE]

                if dst == u:  # ---- ejection
                    if ofree[d_u] > cyc:
                        continue
                    ofree[d_u] = ifree[ip] = cyc + size
                    if vfree is not None:
                        vfree[bi] = cyc + max(size, turnaround)
                    q.popleft()
                    occupancy[u] -= 1
                    in_network -= 1
                    last_progress = cyc
                    router_flits += size
                    per_router[u] += size
                    if ip < d_u:
                        credit_due.setdefault(cyc + size, []).append((nbrs[u][ip], rev_port[u][ip] * V + bi % V, size))
                    else:
                        inj_flits[u] -= size
                    source.ejected(pkt, cyc + size - 1)  # tail arrives size-1 cycles after the head
                    continue

                # ---- pick an output port and VC (needs room for the whole packet)
                best_p = best_vc = -1
                best_c = size - 1
                new_phase = pkt[PHASE]
                phase = pkt[PHASE]
                if algo == "dor":
                    # Dimension-order routing, written to match BookSim's
                    # dim_order_mesh / dim_order_torus so the two simulators
                    # can be compared like for like. Route along columns
                    # (dimension 0) first, then rows. The packet's state,
                    # stored in PHASE, is dim * 4 + dir * 2 + partition, fixed
                    # when the packet turns into a dimension.
                    r, c = divmod(u, n)
                    dr, dc = divmod(dst, n)
                    dim = 0 if c != dc else 1
                    if phase < 0 or phase // 4 != dim:
                        cur, end = (c, dc) if dim == 0 else (r, dr)
                        if is_torus:
                            # Shorter way round the ring; a coin flip on ties.
                            dist2 = n - 2 * ((end - cur) % n)
                            positive = dist2 > 0 or (dist2 == 0 and rand() < 0.5)
                            # BookSim's fixed-dateline VC partition, verbatim.
                            part = 1 if (positive and cur > end) or (not positive and end < cur) else 0
                        else:
                            positive, part = end > cur, 0
                        phase = pkt[PHASE] = dim * 4 + (0 if positive else 2) + part
                    step = 1 if phase % 4 < 2 else -1
                    if dim == 0:
                        v = r * n + (c + step) % n
                    else:
                        v = ((r + step) % n) * n + c
                    p = port_of[u][v]
                    if ofree[p] <= cyc:
                        # On a torus each dateline partition owns half the VCs.
                        lo, hi = (half_v * (phase % 2), half_v * (phase % 2 + 1)) if is_torus else (0, V)
                        for vc in range(lo, hi):
                            c_ = cr[p * V + vc]
                            if c_ > best_c:
                                best_p, best_vc, best_c = p, vc, c_
                elif algo == "bfs":
                    p = bfs_port[u][dst]
                    if ofree[p] <= cyc:
                        for vc in vc_range_all:
                            c = cr[p * V + vc]
                            if c > best_c:
                                best_p, best_vc, best_c = p, vc, c
                elif phase == ADAPTIVE:
                    # Minimal adaptive: least-congested (most credits) minimal port
                    # on any adaptive VC; ties broken uniformly at random.
                    ties = 0
                    for p in minimal[u][dst]:
                        if ofree[p] > cyc:
                            continue
                        base = p * V
                        for vc in vc_range_adaptive:
                            c = cr[base + vc]
                            if c > best_c:
                                best_p, best_vc, best_c, ties = p, vc, c, 1
                            elif c == best_c and c >= size:
                                ties += 1
                                if rand() * ties < 1.0:
                                    best_p, best_vc = p, vc
                    if best_p < 0:
                        # Every minimal adaptive option is full: fall back to the
                        # deadlock-free up*/down* escape network on VC 0.
                        for p, nph in updown[u][dst][UP_PHASE]:
                            c = cr[p * V]
                            if c > best_c and ofree[p] <= cyc:
                                best_p, best_vc, best_c, new_phase = p, 0, c, nph
                else:
                    # Already following up*/down* (escape packet, or updown routing).
                    if algo == "adaptive" and escape_return:
                        # Virtual cut-through buffers hold whole packets, so an
                        # escape packet may hop back onto an adaptive VC whenever
                        # one is free (Duato's condition still holds).
                        for p in minimal[u][dst]:
                            if ofree[p] > cyc:
                                continue
                            for vc in vc_range_adaptive:
                                c = cr[p * V + vc]
                                if c > best_c:
                                    best_p, best_vc, best_c, new_phase = p, vc, c, ADAPTIVE
                    vcs = (0,) if algo == "adaptive" else vc_range_all
                    for p, nph in updown[u][dst][phase] if best_p < 0 else ():
                        if ofree[p] > cyc:
                            continue
                        for vc in vcs:
                            c = cr[p * V + vc]
                            if c > best_c:
                                best_p, best_vc, best_c, new_phase = p, vc, c, nph

                if best_p < 0:
                    continue  # blocked this cycle; try again next cycle

                # ---- traverse switch and link
                q.popleft()
                ofree[best_p] = ifree[ip] = cyc + size
                if vfree is not None:
                    vfree[bi] = cyc + max(size, turnaround)
                occupancy[u] -= 1
                last_progress = cyc
                cr[best_p * V + best_vc] -= size  # reserve the downstream slots
                if ip < d_u:  # our slots free up once the tail has left
                    credit_due.setdefault(cyc + size, []).append((nbrs[u][ip], rev_port[u][ip] * V + bi % V, size))
                else:
                    inj_flits[u] -= size
                L = link_len[u][best_p]
                router_flits += size
                per_router[u] += size
                link_flits += size
                link_flit_tiles += size * L
                pkt[PHASE] = new_phase
                pkt[HOPS] += 1
                pkt[WIRE] += L
                v = nbrs[u][best_p]
                arrive = (cyc + hop_lat[u][best_p]) % pipe_len
                pipeline[arrive].append((v, rev_port[u][best_p] * V + best_vc, pkt))

        cyc += 1
        if in_network and cyc - last_progress > cfg.deadlock_window:
            deadlocked = True
            break
        if source.finished(cyc):
            break
        # Fast-forward: nothing in flight or queued, so skip to the next packet.
        if not in_network:
            nxt = source.next_event()
            if nxt is not None and nxt > cyc and not any(srcq):
                for t in sorted(credit_due):
                    for node, idx, amount in credit_due.pop(t):
                        credits[node][idx] += amount
                cyc = nxt
                last_progress = cyc

    act.router_flits, act.link_flits, act.link_flit_tiles = router_flits, link_flits, link_flit_tiles
    return cyc, deadlocked, act


# ---------------------------------------------------------------------------
# Synthetic traffic
# ---------------------------------------------------------------------------


class _BernoulliSource:
    """Open-loop synthetic traffic with a tagged measurement window."""

    next_event = staticmethod(lambda: None)  # never idle: traffic arrives every cycle

    def __init__(self, topo, pattern, rate, cfg, rng, init_phase, count_escape):
        self.N, self.rate, self.rng = topo.num_nodes, rate, rng
        self.dest = traffic_mod.sampler(pattern, topo.n)
        self.size = self.max_size = cfg.packet_size
        self.init_phase, self.count_escape = init_phase, count_escape
        self.win_start, self.win_end = cfg.warmup, cfg.warmup + cfg.measure
        self.hard_stop = self.win_end + cfg.drain_limit
        self.generated_in_window = self.delivered_in_window = self.outstanding_tagged = 0
        self.latencies, self.net_latencies = [], []
        self.hops_sum = self.wire_sum = self.escaped = 0

    def generate(self, cyc, srcq):
        rand, rng, dest, rate = self.rng.random, self.rng, self.dest, self.rate
        tagging = self.win_start <= cyc < self.win_end
        for u in range(self.N):
            if rand() < rate:
                d = dest(u, rng)
                if d >= 0:
                    srcq[u].append([u, d, cyc, -1, 0, 0, self.init_phase, tagging, self.size, -1])
                    if tagging:
                        self.generated_in_window += 1
                        self.outstanding_tagged += 1

    def ejected(self, pkt, done):
        if self.win_start <= done < self.win_end:
            self.delivered_in_window += 1
        if pkt[TAGGED]:
            self.outstanding_tagged -= 1
            self.latencies.append(done - pkt[T_CREATE])
            self.net_latencies.append(done - pkt[T_INJECT])
            self.hops_sum += pkt[HOPS]
            self.wire_sum += pkt[WIRE]
            if self.count_escape and pkt[PHASE] != ADAPTIVE:
                self.escaped += 1

    def finished(self, cyc):
        return (cyc >= self.win_end and self.outstanding_tagged == 0) or cyc >= self.hard_stop


def simulate(
    topo: Topology,
    pattern: str,
    rate: float,
    cfg: SimConfig | None = None,
    tables: RoutingTables | None = None,
) -> SimResult:
    """Run one synthetic-traffic simulation at a single injection rate."""
    cfg = cfg or SimConfig()
    tables = tables or RoutingTables(topo)
    rng = random.Random(cfg.seed)
    init_phase = UP_PHASE if cfg.routing == "updown" else ADAPTIVE
    src = _BernoulliSource(topo, pattern, rate, cfg, rng, init_phase, cfg.routing == "adaptive")
    cyc, deadlocked, _ = _run(topo, cfg, tables, src, rng)

    N = topo.num_nodes
    offered = src.generated_in_window / (N * cfg.measure)
    accepted = src.delivered_in_window / (N * cfg.measure)
    # Past saturation the network can't keep up: it delivers measurably less
    # than is offered and the source queues grow without bound. (Tagged
    # packets can still drain eventually, because source queues are FIFO, so
    # draining alone is not enough to prove the network is below saturation.)
    saturated = deadlocked or src.outstanding_tagged > 0 or accepted < 0.95 * offered
    nan = float("nan")
    lat = src.latencies
    n_meas = len(lat)
    lat_sorted = sorted(lat)
    return SimResult(
        topology=topo.name,
        pattern=pattern,
        routing=cfg.routing,
        rate=rate,
        offered=offered,
        accepted=accepted,
        avg_latency=nan if saturated or not n_meas else sum(lat) / n_meas,
        avg_network_latency=nan if saturated or not n_meas else sum(src.net_latencies) / n_meas,
        p99_latency=nan if saturated or not n_meas else float(lat_sorted[min(n_meas - 1, int(0.99 * n_meas))]),
        avg_hops=src.hops_sum / n_meas if n_meas else nan,
        avg_wire=src.wire_sum / n_meas if n_meas else nan,
        escape_fraction=src.escaped / n_meas if n_meas else nan,
        saturated=saturated,
        deadlocked=deadlocked,
        cycles=cyc,
    )


# ---------------------------------------------------------------------------
# Application traces
# ---------------------------------------------------------------------------


def simulate_trace(topo: Topology, trace, cfg: SimConfig | None = None, tables: RoutingTables | None = None) -> dict:
    """Replay a dependency-driven trace (``nocsim.traces.TraceSource``).

    Returns a dict with the completion time (a runtime proxy), packet latency
    statistics, hop/wire averages and the activity counts used for energy.
    """
    cfg = cfg or SimConfig()
    tables = tables or RoutingTables(topo)
    rng = random.Random(cfg.seed)
    trace.reset(ADAPTIVE if cfg.routing != "updown" else UP_PHASE)
    cyc, deadlocked, act = _run(topo, cfg, tables, trace, rng)
    stats = trace.summary()
    stats.update(topology=topo.name, routing=cfg.routing, cycles=cyc, deadlocked=deadlocked,
                 router_flits=act.router_flits, link_flits=act.link_flits,
                 link_flit_tiles=act.link_flit_tiles, per_router_flits=act.per_router_flits)
    return stats
