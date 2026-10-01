"""Routing correctness, deadlock analysis, and simulator sanity checks."""

import pytest

from nocsim.routing import RoutingTables, is_deadlock_free
from nocsim.search import DesignSpace, Objective, simulated_annealing
from nocsim.simulator import SimConfig, simulate
from nocsim.topology import from_boundary_links, mesh, modified_torus, torus

import random


@pytest.mark.parametrize("topo", [torus(6), modified_torus(6), mesh(6)])
def test_escape_network_is_deadlock_free(topo):
    assert is_deadlock_free(RoutingTables(topo), "updown")


def test_thesis_bfs_routing_has_cyclic_dependencies_on_torus():
    assert not is_deadlock_free(RoutingTables(torus(6)), "bfs")
    assert not is_deadlock_free(RoutingTables(modified_torus(6)), "bfs")


def test_updown_reaches_every_destination():
    tables = RoutingTables(modified_torus(7))
    n = tables.topo.num_nodes
    for s in range(n):
        for t in range(n):
            assert tables.updown_path_length(s, t) < 10 * n


def test_random_designs_are_also_deadlock_free():
    space = DesignSpace(6)
    for seed in range(5):
        topo = from_boundary_links(6, space.random_matching(random.Random(seed)))
        assert is_deadlock_free(RoutingTables(topo), "adaptive")


def test_zero_load_latency_matches_hops():
    # At very low load each hop costs router_delay + 1 link cycle = 2 cycles.
    r = simulate(torus(6), "uniform", 0.005, SimConfig(warmup=200, measure=4000, seed=3))
    assert not r.saturated
    assert r.avg_latency == pytest.approx(2 * r.avg_hops, rel=0.05)


def test_every_packet_is_delivered_below_saturation():
    r = simulate(modified_torus(6), "transpose", 0.1, SimConfig(warmup=300, measure=1500))
    assert not r.saturated and not r.deadlocked
    assert r.accepted == pytest.approx(r.offered, rel=0.1)


def test_adaptive_routing_survives_overload():
    cfg = SimConfig(warmup=200, measure=800, drain_limit=1500)
    for topo in (torus(6), modified_torus(6)):
        r = simulate(topo, "uniform", 1.0, cfg)
        assert r.saturated and not r.deadlocked


def test_bfs_single_vc_deadlocks_on_torus():
    r = simulate(torus(6), "uniform", 0.5, SimConfig(routing="bfs", num_vcs=1, warmup=200, measure=800))
    assert r.deadlocked


def test_annealing_beats_hand_design_on_small_grid():
    n = 6
    budget = modified_torus(n).total_wire_length
    obj = Objective(n, budget)
    hand = obj(modified_torus(n).extra_links())
    res = simulated_annealing(obj, 600, seed=0, init=modified_torus(n).extra_links())
    assert res.best_score >= hand
    assert obj.stats(res.best_links)["total_wire"] <= budget


# ---- dimension-order routing (used to validate against BookSim) -------------

def test_dor_zero_load_latency_is_hops_times_hop_delay():
    # router_delay=3 + 1-cycle link = 4 cycles per hop, as in BookSim's default pipeline.
    cfg = SimConfig(routing="dor", num_vcs=2, router_delay=3, warmup=200, measure=3000, seed=5)
    r = simulate(mesh(6), "uniform", 0.005, cfg)
    assert r.avg_latency == pytest.approx(4 * r.avg_hops, rel=0.03)


def test_dor_is_minimal_on_mesh_and_torus():
    for topo in (mesh(6), torus(6)):
        r = simulate(topo, "uniform", 0.01, SimConfig(routing="dor", num_vcs=2, warmup=200, measure=2000))
        from nocsim.metrics import summary
        assert r.avg_hops == pytest.approx(summary(topo)["avg_hops"], rel=0.05)


def test_dor_torus_with_dateline_survives_overload():
    cfg = SimConfig(routing="dor", num_vcs=2, warmup=200, measure=800, drain_limit=1500)
    r = simulate(torus(6), "uniform", 1.0, cfg)
    assert not r.deadlocked


def test_dor_rejects_unsupported_setups():
    with pytest.raises(ValueError):
        simulate(modified_torus(6), "uniform", 0.1, SimConfig(routing="dor"))
    with pytest.raises(ValueError):
        simulate(torus(6), "uniform", 0.1, SimConfig(routing="dor", num_vcs=3))
