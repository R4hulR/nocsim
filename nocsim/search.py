"""Automated topology design: where should the boundary links go?

``nocsim.topology`` shows that the mesh, the torus and the thesis' modified
torus all solve the same problem in different ways. Each one decides how to
pair the 4n spare ports on a mesh's boundary. The torus pairs every port
with the opposite edge, which gives long wires and well-balanced load. The
modified torus folds most of them onto the same edge, which saves wire but
concentrates traffic on a few links. This module turns that choice into an
optimization problem:

    maximise   J(G) = mean over traffic patterns p of  thr_p(G) / thr_p(torus)
                      - hop_weight * avg_hops(G) / avg_hops(torus)
    subject to total_wire(G) <= wire_budget,  every router has radix 4

where thr_p is the analytical throughput estimate under pattern p. Averaging
over several patterns (uniform, transpose, bit-complement, tornado) matters.
An earlier version optimised uniform traffic alone, and the cycle-level
simulator showed that the design it found was better on uniform but clearly
worse on transpose and tornado: it had overfit the objective.

and compares three ways of searching the space of port matchings:

* ``random_search``       - sample valid matchings uniformly (the baseline);
* ``simulated_annealing`` - local search with 2-opt style "re-pair two
  links" moves;
* ``reinforce``           - policy-gradient RL. The policy builds a matching
  one port at a time (autoregressive), choosing each partner from a learned
  softmax, and is trained with REINFORCE (Williams, 1992) using J as the
  episode reward.

All three get the same number of objective evaluations, so the comparison is
fair. Throughput is the analytical channel-load estimate from
``nocsim.metrics``, which is cheap enough to call thousands of times. The
best designs are then checked in the cycle-level simulator.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np

from . import metrics, traffic
from .topology import Topology, _mesh_edges, _norm, boundary_ports, from_boundary_links, torus


DEFAULT_PATTERNS = ("uniform", "transpose", "bit_complement", "tornado")


@dataclass
class Objective:
    """Scores a set of boundary links. Results are cached, so repeats are free.

    Over-budget designs aren't thrown away. They get a penalty proportional
    to the overshoot, which gives search methods a gradient back toward
    feasible designs.
    """

    n: int
    wire_budget: float
    patterns: tuple = DEFAULT_PATTERNS
    hop_weight: float = 0.25
    penalty: float = 5.0
    evaluations: int = 0
    cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self.matrices = {p: traffic.matrix(p, self.n) for p in self.patterns}
        ref = self.evaluate(torus(self.n))
        self.ref_thr = {p: ref[f"throughput_est_{p}"] for p in self.patterns}
        self.ref_hops = ref["avg_hops"]
        self.mesh_wire = len(_mesh_edges(self.n))  # every mesh link is 1 tile long

    def evaluate(self, topo: Topology) -> dict:
        """Static summary plus one throughput estimate per traffic pattern."""
        paths = metrics.shortest_paths(topo)
        s = metrics.summary(topo)
        for p, m in self.matrices.items():
            s[f"throughput_est_{p}"] = metrics.throughput_estimate(topo, m, paths)
        return s

    def wire(self, links) -> int:
        return self.mesh_wire + sum(abs(u // self.n - v // self.n) + abs(u % self.n - v % self.n) for u, v in links)

    def __call__(self, links) -> float:
        key = frozenset(_norm(*e) for e in links)
        if key in self.cache:
            return self.cache[key][0]
        self.evaluations += 1
        s = self.evaluate(from_boundary_links(self.n, list(key)))
        thr = float(np.mean([s[f"throughput_est_{p}"] / self.ref_thr[p] for p in self.patterns]))
        s["throughput_rel_torus"] = thr
        score = thr - self.hop_weight * s["avg_hops"] / self.ref_hops
        over = max(0.0, s["total_wire"] - self.wire_budget)
        score -= self.penalty * over / self.wire_budget
        self.cache[key] = (score, s)
        return score

    def stats(self, links) -> dict:
        self(links)
        return self.cache[frozenset(_norm(*e) for e in links)][1]


class DesignSpace:
    """Valid perfect matchings of the 4n spare boundary ports of an n x n mesh."""

    def __init__(self, n: int):
        self.n = n
        self.ports = boundary_ports(n)  # node id per port; corners appear twice
        self.mesh = set(_mesh_edges(n))

    def can_link(self, u: int, v: int, existing: set) -> bool:
        """A new link must not be a self-loop, a mesh link, or a duplicate."""
        e = _norm(u, v)
        return u != v and e not in self.mesh and e not in existing

    def random_matching(self, rng: random.Random, tries: int = 1000) -> list[tuple[int, int]]:
        """Uniformly random valid matching, by shuffling and pairing with rejection."""
        for _ in range(tries):
            ports = self.ports[:]
            rng.shuffle(ports)
            links, ok = set(), True
            for a, b in zip(ports[::2], ports[1::2]):
                if not self.can_link(a, b, links):
                    ok = False
                    break
                links.add(_norm(a, b))
            if ok:
                return sorted(links)
        raise RuntimeError("could not sample a valid matching")

    def neighbor(self, links: list, rng: random.Random) -> list | None:
        """Re-pair two random links: (a,b),(c,d) -> (a,c),(b,d) or (a,d),(b,c).

        Every router keeps its radix, so the result is still a valid design
        (or None if the move would create a self-loop or a duplicate link).
        """
        i, j = rng.sample(range(len(links)), 2)
        (a, b), (c, d) = links[i], links[j]
        new1, new2 = ((a, c), (b, d)) if rng.random() < 0.5 else ((a, d), (b, c))
        rest = set(links) - {links[i], links[j]}
        if not self.can_link(*new1, rest):
            return None
        rest.add(_norm(*new1))
        if not self.can_link(*new2, rest):
            return None
        rest.add(_norm(*new2))
        return sorted(rest)


@dataclass
class SearchResult:
    method: str
    best_links: list
    best_score: float
    history: list  # best score so far, after each objective evaluation

    def topology(self, n: int, name: str | None = None) -> Topology:
        return from_boundary_links(n, self.best_links, name or f"Searched ({self.method})", {"method": self.method})


def _track(obj: Objective, history: list, best: list, links) -> float:
    """Evaluate ``links`` and record the best-so-far curve per new evaluation."""
    before = obj.evaluations
    s = obj(links)
    if s > best[0]:
        best[0], best[1] = s, list(links)
    if obj.evaluations > before:
        history.append(best[0])
    return s


def random_search(obj: Objective, budget: int, seed: int = 0) -> SearchResult:
    rng, space = random.Random(seed), DesignSpace(obj.n)
    best, history = [-math.inf, None], []
    start = obj.evaluations
    while obj.evaluations - start < budget:
        _track(obj, history, best, space.random_matching(rng))
    return SearchResult("random", best[1], best[0], history)


def simulated_annealing(
    obj: Objective, budget: int, seed: int = 0, t_start: float = 0.05, t_end: float = 0.001, init=None
) -> SearchResult:
    """Metropolis local search with a geometric cooling schedule.

    The temperatures are in units of J. 0.05 lets the search accept moves that
    cost about 5% of torus throughput early on.
    """
    rng, space = random.Random(seed), DesignSpace(obj.n)
    best, history = [-math.inf, None], []
    start = obj.evaluations
    cur = list(init) if init is not None else space.random_matching(rng)
    cur_s = _track(obj, history, best, cur)
    steps = 0
    while obj.evaluations - start < budget:
        frac = min(1.0, (obj.evaluations - start) / budget)
        temp = t_start * (t_end / t_start) ** frac
        cand = space.neighbor(cur, rng)
        steps += 1
        if cand is None:
            continue
        s = _track(obj, history, best, cand)
        if s >= cur_s or rng.random() < math.exp((s - cur_s) / temp):
            cur, cur_s = cand, s
        if steps > 50 * budget:  # safety net if the cache stops yielding new designs
            break
    return SearchResult("annealing", best[1], best[0], history)


def reinforce(
    obj: Objective, budget: int, seed: int = 0, batch: int = 16, lr: float = 0.5, entropy_floor: float = 0.02
) -> SearchResult:
    """REINFORCE with a mean-reward baseline over an autoregressive matching policy.

    State: which ports are still unmatched. At each step the policy takes the
    lowest-index unmatched port i and picks a partner j from a softmax over
    learnable logits theta[i, j], masked to the partners that are still valid.
    An episode ends when every port is matched, and its reward is J. If the
    policy paints itself into a corner (no valid partner left), the episode
    gets a fixed low reward, so the policy learns to avoid that.

    With a tabular policy like this, REINFORCE amounts to learning a
    distribution over designs. It's simple and transparent, and it's a fair
    RL baseline to set against annealing at an equal evaluation budget.
    """
    rng, space = np.random.default_rng(seed), DesignSpace(obj.n)
    ports = space.ports
    P = len(ports)
    theta = np.zeros((P, P))
    best, history = [-math.inf, None], []
    start = obj.evaluations
    fail_reward = None
    # A cache hit costs no evaluation, so cap the total number of episodes too.
    max_episodes = 40 * budget

    episodes = 0
    while obj.evaluations - start < budget and episodes < max_episodes:
        grads, rewards = [], []
        for _ in range(batch):
            episodes += 1
            unmatched = list(range(P))
            links: set = set()
            steps = []  # (i, candidate ports, chosen index, probabilities)
            failed = False
            while unmatched:
                i = unmatched.pop(0)
                cands = [j for j in unmatched if space.can_link(ports[i], ports[j], links)]
                if not cands:
                    failed = True
                    break
                logits = theta[i, cands]
                q = np.exp(logits - logits.max())
                q /= q.sum()
                # Sample from q mixed with a little uniform probability so
                # exploration never stops. The gradient below uses q itself;
                # that ignores the small mixture term.
                p = (1 - entropy_floor) * q + entropy_floor / len(cands)
                k = int(rng.choice(len(cands), p=p))
                j = cands[k]
                unmatched.remove(j)
                links.add(_norm(ports[i], ports[j]))
                steps.append((i, cands, k, q))
            if failed:
                if fail_reward is None:
                    fail_reward = obj(space.random_matching(random.Random(seed))) - 1.0
                r = fail_reward
            else:
                r = _track(obj, history, best, sorted(links))
            grads.append(steps)
            rewards.append(r)

        # Policy-gradient step: theta += lr * (R - baseline) * grad log pi.
        rewards = np.array(rewards)
        adv = rewards - rewards.mean()
        if rewards.std() > 1e-9:
            adv /= rewards.std()
        for steps, a in zip(grads, adv):
            for i, cands, k, q in steps:
                g = -q
                g[k] += 1.0  # d log softmax / d logits = onehot - p
                theta[i, cands] += lr * a * g / batch
    return SearchResult("reinforce", best[1], best[0], history)
