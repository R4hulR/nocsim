"""DSENT-based area/energy/timing model sanity checks."""

import pytest

from nocsim.power import TechModel
from nocsim.topology import folded_torus, mesh, modified_torus, torus

tm = TechModel.from_dsent(tile_mm=1.5, freq_hz=2e9)


def test_reach_shrinks_as_the_clock_speeds_up():
    reaches = [tm.reach_mm(f * 1e9) for f in (1, 2, 3, 4)]
    assert reaches == sorted(reaches, reverse=True)
    assert tm.reach_mm(2e9) == pytest.approx(13.0)  # DSENT's 22 nm value at 2 GHz


def test_longest_link_sets_the_global_clock():
    assert tm.max_frequency(folded_torus(8)) > tm.max_frequency(torus(8))  # 3 mm vs 10.5 mm links
    assert tm.max_frequency(torus(8)) == pytest.approx(tm.max_frequency(modified_torus(8)))  # both keep 7-tile links


def test_area_tracks_wire():
    assert tm.area(mesh(8))["total_mm2"] < tm.area(modified_torus(8))["total_mm2"] < tm.area(torus(8))["total_mm2"]


def test_energy_accounts_every_component():
    e = tm.energy(torus(8), router_flits=1000, link_flit_tiles=3000, cycles=10_000)
    assert e["total_J"] == pytest.approx(e["router_dynamic_J"] + e["link_dynamic_J"] + e["clock_J"] + e["leakage_J"])
    assert min(e.values()) > 0


def test_mesh_routers_use_their_real_port_counts():
    # 4 corners (3 ports) + 24 edge (4 ports) + 36 interior (5 ports) < 64 five-port routers.
    full = 64 * tm.router(5)[3]
    assert tm.area(mesh(8))["router_mm2"] < full
    assert tm.area(torus(8))["router_mm2"] == pytest.approx(full)


def test_folded_torus_gets_the_torus_traffic_and_only_shorter_wires():
    from nocsim.metrics import summary
    f, t = folded_torus(8), torus(8)
    assert f.edges == t.edges  # same logical network and core mapping
    assert summary(f)["throughput_est"] == pytest.approx(summary(t)["throughput_est"])
    assert f.max_link_length == 2 and f.total_wire_length == t.total_wire_length
