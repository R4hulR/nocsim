"""Topology construction: the generalised modified torus must reproduce the
thesis' hand-written 5x5 adjacency list, and every design must keep radix 4."""

import pytest

from nocsim.topology import boundary_ports, mesh, modified_torus, torus

# The 5x5 adjacency list from the original 2023 code (project_8th_sem.txt),
# transcribed as (row, col) pairs.
LEGACY_5x5 = {
    (0, 0): [(0, 1), (1, 0), (4, 0), (0, 4)], (1, 0): [(1, 1), (2, 0), (3, 0), (0, 0)],
    (2, 0): [(3, 0), (2, 4), (2, 1), (1, 0)], (3, 0): [(4, 0), (3, 1), (2, 0), (1, 0)],
    (4, 0): [(3, 0), (4, 1), (0, 0), (4, 4)], (0, 1): [(0, 0), (0, 3), (1, 1), (0, 2)],
    (1, 1): [(1, 0), (1, 2), (2, 1), (0, 1)], (2, 1): [(1, 1), (2, 2), (2, 0), (3, 1)],
    (3, 1): [(3, 0), (2, 1), (3, 2), (4, 1)], (4, 1): [(4, 0), (3, 1), (4, 2), (4, 3)],
    (0, 2): [(0, 1), (4, 2), (0, 3), (1, 2)], (1, 2): [(1, 1), (0, 2), (1, 3), (2, 2)],
    (2, 2): [(2, 1), (1, 2), (2, 3), (3, 2)], (3, 2): [(3, 1), (2, 2), (3, 3), (4, 2)],
    (4, 2): [(4, 1), (3, 2), (4, 3), (0, 2)], (0, 3): [(0, 2), (0, 1), (0, 4), (1, 3)],
    (1, 3): [(1, 2), (0, 3), (1, 4), (2, 3)], (2, 3): [(2, 2), (1, 3), (2, 4), (3, 3)],
    (3, 3): [(3, 2), (2, 3), (3, 4), (4, 3)], (4, 3): [(4, 2), (3, 3), (4, 4), (4, 1)],
    (0, 4): [(0, 3), (4, 4), (0, 0), (1, 4)], (1, 4): [(1, 3), (0, 4), (3, 4), (2, 4)],
    (2, 4): [(2, 3), (1, 4), (2, 0), (3, 4)], (3, 4): [(3, 3), (2, 4), (1, 4), (4, 4)],
    (4, 4): [(4, 3), (3, 4), (4, 0), (0, 4)],
}


def test_modified_torus_matches_original_5x5_code():
    legacy = {tuple(sorted((r * 5 + c, a * 5 + b))) for (r, c), nb in LEGACY_5x5.items() for a, b in nb}
    assert set(modified_torus(5).edges) == legacy


@pytest.mark.parametrize("n", range(5, 12))
def test_torus_and_modified_have_radix_4(n):
    for topo in (torus(n), modified_torus(n)):
        assert set(topo.degrees) == {4}
        assert len(topo.edges) == 2 * n * n


@pytest.mark.parametrize("n", range(5, 12))
def test_modified_torus_saves_wire(n):
    assert modified_torus(n).total_wire_length < torus(n).total_wire_length


def test_boundary_ports_count():
    for n in range(3, 10):
        assert len(boundary_ports(n)) == 4 * n
    assert mesh(4).extra_links() == []


def test_modified_torus_rejects_small_grids():
    with pytest.raises(ValueError):
        modified_torus(4)
