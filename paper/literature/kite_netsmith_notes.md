# Close reading: Kite (DAC'20) and NetSmith (ICPP'24)

Both papers read in full (Oct 2026). Page/section references are to the PDFs.

## Kite — Bharadwaj, Yin, Beckmann, Krishna (AMD / Georgia Tech), DAC 2020
- **Setting:** network-on-interposer (NoI) for a 64-core system: 4 chiplets × 16 cores, 16 memory channels, concentration 4 → **20 interposer routers** in a "misaligned" 4×5 layout, **max router degree 8** (§III-C).
- **Key idea, effective hop count:** `H_eff = H_avg / f`, where the max clock frequency `f` is set by the **longest link** and the largest router radix (DSENT, 22 nm; Fig. 3). Mesh 4.0 GHz, CMesh 3.6, Butter Donut / Double Butterfly 2.7 GHz (§III-D). A long-link topology can lose its hop-count advantage to clock frequency.
- **Link classes:** *k-straight* (skip k−1 routers along one axis) and *k-m-diagonal*. Kite-Small/Medium/Large use 1-1-diagonal, 2-straight and 2-1-diagonal as their longest link (§III-B/C).
- **Routing:** shortest path plus **escape VCs** (Duato); in the escape VCs, DOR for meshes and "no double-back turns" otherwise (§V-A). *Same family as our adaptive + up\*/down\* escape scheme.*
- **Evaluation:** HeteroGarnet (gem5) with synthetic traffic (50% coherence / 50% memory; 8 B and 72 B packets, as in Netrace), plus gem5 full-system GPU apps (Rodinia, HPC proxies, 64-CU AMD GPU). DSENT 22 nm, 2.0 mm core pitch, 24×36 mm interposer (§V).
- **Results:** Kite-Medium −7.5% latency, +17% saturation vs. Butter Donut; 12% / 9.2% application speedup vs. Double Butterfly / Butter Donut. Area 3.83 mm² (1.3% of interposer).
- **Their footnote 2:** "most of these arguments are true for modern many-core systems as well", i.e. the frequency/long-link argument applies on-chip too.

## NetSmith — Green & Thottethodi (Purdue), ICPP 2024
- **Setting:** same NoI context (20 routers, 4×5; also 30 and 48 routers). Router layout and radix are inputs; links are limited to a **maximum length** class (small (1,1) / medium (2,0) / large (2,1)), following Kite (§III-A b).
- **Method:** MILP (Gurobi). Objectives: total hop count via a triangle-inequality formulation, and/or **sparsest-cut bandwidth** (exhaustive over partitions). Allows **asymmetric links** (+3% throughput). Routing by a second MILP (MCLB) that picks one shortest path per flow to minimise max channel load. Deadlock via DFSSSP-style VC partitioning: 4 VCs suffice for all 20-router designs; **Folded Torus needs 4** (§IV-A).
- **Solver cost:** small converges in <5 min; large reaches ~9% gap in 30 min; **48 routers ~2 days** (Fig. 5). An earlier MILP (LPBT) took 20+ days.
- **Evaluation:** gem5 v22 + HeteroGarnet; synthetic uniform/shuffle traffic; **full-system PARSEC** on 64 OoO cores at 3.8 GHz (100M-instruction ROI). DSENT 22 nm power/area. NoI clocked by longest link: 3.6 / 3.0 / 2.7 GHz (§IV).
- **Results:** +50–75% bisection bandwidth, −8–13.5% avg hops vs. expert designs; up to 11% mean PARSEC speedup over Kite-Large. **Kite-Small turned out to be the optimal topology** for the sparsest-cut objective (§III-B).
- **Baselines include Folded Torus**, which performed worst among the medium designs in full-system runs (§V-C).
- Notes the measured-vs-analytical throughput gap is due to input-queued routers (Karol et al., 1987), the same effect our BookSim validation exposed.

## What this means for our paper (action items)
1. **Max link length matters as well as total wire.** Under Kite/NetSmith's model, clock frequency is set by the *longest* link. Our modified torus keeps the corner wraparounds, which are **n−1 = 7 tiles long, as long as the torus's**. So it gets **no frequency advantage** over the torus unless long links are pipelined. The paper must model this explicitly:
   - add `H_eff`-style metrics (hops × per-hop delay from the longest link), and
   - evaluate both regimes: (a) a global clock set by the longest link (Kite/NetSmith), and (b) pipelined long links (our existing `link_latency="wire"`).
   - Extend the search with a **max-link-length constraint** (small/medium/large classes) alongside the total-wire budget. This makes the results directly comparable to Kite/NetSmith.
2. **Our "hand design is on the frontier" finding has precedent:** NetSmith found Kite-Small optimal. Cite it as consistent evidence, not as a weakness.
3. **Folded Torus is a required baseline** (both papers use it); NetSmith reports it needs 4 VCs for deadlock freedom.
4. **Novelty must be stated narrowly.** NetSmith claims its method generalises to any layout and radix, so "machine-designed topologies for our setting" is *not* new on its own. Defensible differences:
   - **Design space:** we keep the full mesh and re-assign only the 4n spare boundary ports, a minimal, floorplan-compatible change to existing mesh NoCs, rather than free-form wiring.
   - **Objective:** total wire as a *budget* (energy/area) traced as a frontier, plus max link length.
   - **Scale:** 64 routers (8×8), a size at which NetSmith's MILP reports multi-day solves; our heuristics are minutes.
   - **Monolithic on-chip**, not interposer.
   - **Open, BookSim-validated simulator; RL vs. annealing comparison.**
5. **Application evidence is a weak spot vs. NetSmith.** They use full-system gem5 PARSEC; we plan Netrace (trace-driven, 2010, in-order cores). Be upfront about it in threats to validity. Full-system gem5 is the stronger (slower) option if reviewers insist.
6. **Routing fairness:** both papers use shortest-path routing with escape VCs, as we do. Good; say so.
