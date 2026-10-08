"""Multi-flit packets, the VC turnaround limit, and dependency-driven trace replay."""

import pytest

from nocsim.simulator import SimConfig, simulate, simulate_trace
from nocsim.topology import mesh, modified_torus, torus
from nocsim.traces import TracePacket, TraceSource


# ---- multi-flit packets (virtual cut-through) --------------------------------

def test_multiflit_zero_load_latency_adds_serialization():
    # Head pays 2 cycles per hop; the tail arrives size-1 cycles after the head.
    cfg = SimConfig(packet_size=5, buffer_depth=8, warmup=200, measure=3000, seed=4)
    r = simulate(torus(6), "uniform", 0.002, cfg)
    assert r.avg_latency == pytest.approx(2 * r.avg_hops + 4, rel=0.05)


def test_multiflit_packets_saturate_at_lower_packet_rate():
    # Same packet rate, 4x the flits: the network must saturate.
    base = SimConfig(warmup=300, measure=1500, drain_limit=2000, buffer_depth=8)
    small = simulate(mesh(6), "uniform", 0.15, base)
    big = simulate(mesh(6), "uniform", 0.15, SimConfig(**{**base.__dict__, "packet_size": 4}))
    assert not small.saturated and big.saturated


def test_buffer_must_hold_a_whole_packet():
    with pytest.raises(ValueError):
        simulate(mesh(4), "uniform", 0.1, SimConfig(packet_size=5, buffer_depth=4))


def test_vc_turnaround_limits_single_vc_throughput():
    cfg = dict(routing="dor", num_vcs=1, warmup=300, measure=1500, drain_limit=2000, seed=2)
    fast = simulate(mesh(6), "uniform", 0.3, SimConfig(**cfg))
    slow = simulate(mesh(6), "uniform", 0.3, SimConfig(**cfg, vc_turnaround=3))
    assert slow.saturated or slow.avg_latency > fast.avg_latency


def test_multiflit_adaptive_routing_does_not_deadlock():
    cfg = SimConfig(packet_size=5, buffer_depth=8, warmup=200, measure=800, drain_limit=1500)
    for topo in (torus(6), modified_torus(6)):
        assert not simulate(topo, "uniform", 0.5, cfg).deadlocked


# ---- trace replay -----------------------------------------------------------

def _chain():
    # 0 -> 1 -> 2 dependency chain plus an independent local packet (3).
    return [
        TracePacket(0, 1000, 0, 35, 1, [], [1]),
        TracePacket(1, 1050, 35, 0, 5, [0], [2]),
        TracePacket(2, 1100, 0, 7, 1, [1], []),
        TracePacket(3, 5000, 9, 9, 1, [], []),
    ]


def test_trace_respects_dependencies_and_delivers_everything():
    src = TraceSource(_chain(), timing="relative")
    s = simulate_trace(torus(6), src, SimConfig(buffer_depth=8))
    assert s["delivered"] == 4 and not s["deadlocked"]
    done = src.done_at
    assert done[0] < done[1] < done[2]  # each child only after its parent arrived


def test_trace_netrace_timing_never_injects_before_recorded_cycle():
    src = TraceSource(_chain(), timing="netrace")
    simulate_trace(mesh(6), src, SimConfig(buffer_depth=8))
    assert src.done_at[2] >= 1100 - 1000  # recorded cycles are relative to the first packet


def test_trace_speedup_shortens_completion():
    slow = simulate_trace(torus(6), TraceSource(_chain(), speedup=1), SimConfig(buffer_depth=8))
    fast = simulate_trace(torus(6), TraceSource(_chain(), speedup=10), SimConfig(buffer_depth=8))
    assert fast["completion"] < slow["completion"]
