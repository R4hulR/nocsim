"""Analytical metrics checked against closed-form results for k-ary 2-cubes."""

import pytest

from nocsim import metrics
from nocsim.topology import mesh, torus


@pytest.mark.parametrize("k", [4, 5, 6, 8])
def test_torus_average_hops_closed_form(k):
    # Mean ring distance over all ordered pairs (self included) is k/4 for
    # even k and (k^2-1)/(4k) for odd k; two dimensions add, and we exclude
    # the k^2 self-pairs from the average.
    per_dim = k / 4 if k % 2 == 0 else (k * k - 1) / (4 * k)
    expected = 2 * per_dim * k * k / (k * k - 1)
    assert metrics.summary(torus(k))["avg_hops"] == pytest.approx(expected)


@pytest.mark.parametrize("k", [4, 6, 8])
def test_torus_uniform_throughput_estimate(k):
    # Dally & Towles: torus channel load under uniform traffic is k*lambda/8
    # (traffic including self), so saturation is at 8/k; excluding self-traffic
    # rescales it by (N-1)/N. Each router also ejects at most 1 packet/cycle,
    # which caps small tori at 1.0.
    n = k * k
    expected = min(1.0, 8 / k * (n - 1) / n)
    assert metrics.summary(torus(k))["throughput_est"] == pytest.approx(expected)


def test_mesh_diameter_and_wire():
    s = metrics.summary(mesh(6))
    assert s["diameter"] == 10
    assert s["total_wire"] == 2 * 6 * 5


def test_hop_accounting_is_consistent():
    # Sum of channel loads / total flow must equal the mean minimal hop count.
    s = metrics.summary(torus(6))
    assert s["avg_hops_traffic"] == pytest.approx(s["avg_hops"])


def test_hotspot_is_ejection_limited():
    # 20% of all traffic goes to one router, which can eject 1 packet/cycle:
    # the estimate must respect that limit no matter how good the topology is.
    from nocsim import traffic
    est = metrics.throughput_estimate(torus(8), traffic.matrix("hotspot", 8))
    assert est <= 1.0 / (0.2 * 63) + 1e-9
