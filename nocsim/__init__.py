"""nocsim: topology design and cycle-level simulation for 2D Networks-on-Chip.

Modules:
    topology   - mesh, torus, the thesis' modified torus, and arbitrary boundary-link designs
    metrics    - analytical hop count, wire length and channel-load throughput estimates
    routing    - BFS, up*/down* and Duato-style adaptive routing, plus deadlock (CDG) checks
    traffic    - synthetic traffic patterns
    simulator  - cycle-level router/link simulator
    search     - simulated annealing and policy-gradient (RL) topology search
"""

from .topology import Topology, mesh, modified_torus, torus  # noqa: F401
