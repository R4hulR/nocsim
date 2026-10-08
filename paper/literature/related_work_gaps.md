# Literature review: gaps and reading list (Milestone 1)

All 32 papers below are verified: DOI or arXiv id, metadata pulled from Crossref/arXiv,
in `references_verified.bib`. Summaries come from abstracts and search snippets, **not from
reading the full papers**. Everything marked *(check)* must be confirmed when you read them.

Mix: 18 from 2021–2026, 9 from 2015–2020, 5 pre-2015 (tools). The classic theory references
already in the paper (Dally & Towles, Duato, Dally & Seitz, Autonet, Williams, Kirkpatrick)
stay; the old descriptive survey material (CLICHE, SPIN 2000, Octagon, BFT, etc.) is dropped.

## Gap table (the related work is restructured around this)

| Theme | Representative work | Their setting | Gap this paper addresses |
|---|---|---|---|
| **Low-diameter on-chip topologies** | Slim NoC (ASPLOS'18), Sparse Hamming Graph (DAC'23) | Monolithic NoCs; radix **above 4**, buying low diameter with more router ports; SHG uses approximate floorplanning for cost | Keeps radix fixed at 4 (mesh-compatible routers) and asks how to spend a **wire budget**, not extra ports |
| **Topologies with link-length limits** | Butter Donut (MICRO'15), Kite (DAC'20), NetSmith (ICPP'24), Library of Networks (ISPASS'25) | **Chiplet / interposer** networks; link length bounded by interposer technology; Kite by expert design, NetSmith by MIP | Same question (where do long links go under length limits), but for a **monolithic radix-4 2D NoC**, with **wire length as a continuous budget** traced as a Pareto frontier |
| **Chiplet & wafer-scale arrangement** | HexaMesh (DAC'23), PlaceIT (arXiv'25), FoldedHexaTorus (arXiv'25), RapidChiplet (2025), wafer co-design (ISCA'25), wafer-on-wafer (arXiv'26) | Physical placement and packaging co-designed with topology | Shows the field moving toward **physical-design-aware topology design**; this paper brings that view (wire length + DSENT energy) to a classic 2D NoC |
| **Deadlock freedom on irregular topologies** | Avoidance: up*/down*, Duato (classic). Recovery: Static Bubble (HPCA'17), SPIN (ISCA'18), BINDU (NOCS'19), SWAP (MICRO'19). Chiplets: modular routing (ISCA'18), UPP (HPCA'22), Absorb (2024) | Recovery schemes avoid VC overhead; modular schemes target chiplets | Uses **avoidance with an escape VC** so every searched topology is provably deadlock-free *(check: argue why avoidance over recovery: simpler, verifiable via CDG)*; quantifies that the thesis-era BFS routing deadlocks |
| **ML / RL for NoCs** | RL routing: DeepNR (2022), Khan & Pasricha (IEEE D&T'23), DRLAR (2024). Learned performance models: NoCeption (DATE'22), ML assessment (2024), latency prediction (2026), NOCTOPUS (2026) | ML mostly for **routing decisions** or as a **fast performance predictor** | Uses RL for **topology search** and reports honestly that it loses to annealing; points to GNN surrogates (NoCeption, NOCTOPUS) as the fix |
| **Evaluation methodology** | BookSim (ISPASS'13), Garnet/HeteroGarnet (gem5 20.0+), Netrace (NoCArc'10), DSENT (NOCS'12), RapidChiplet | Validated simulators; dependency-driven traces; power/area models | Many topology papers report synthetic traffic only. This paper combines **synthetic + dependency-driven application traces + DSENT** with a simulator **validated against BookSim** |

**One-sentence positioning (draft):** prior work on length-constrained topologies targets chiplet
interposers with higher-radix or MIP-optimised designs, and low-diameter on-chip topologies buy
diameter with extra router ports. We study the remaining corner: a *monolithic, radix-4* NoC under
an explicit *wire budget*, evaluated with validated simulation, application traces and physical
energy/area models.

## Your reading list (read these 9 before the rewrite is final)
Priority order. For each: abstract, intro, and the evaluation setup. Confirm the *(check)* claims.

1. **Kite** (DAC'20): closest in spirit (long links, expert topologies). Confirm the radix and link-length assumptions.
2. **NetSmith** (ICPP'24): automated topology synthesis under length/radix limits; our search must be contrasted with its MIP approach.
3. **Slim NoC** (ASPLOS'18): the standard on-chip low-diameter baseline; note its radix and wiring arguments.
4. **Sparse Hamming Graph** (DAC'23): cost-performance trade-off plus floorplan-aware cost model, similar to our wire-budget framing.
5. **Butter Donut** (MICRO'15): the origin of the folded-torus/butterfly hybrids.
6. **Static Bubble** (HPCA'17) or **SPIN** (ISCA'18): one of these, to justify avoidance vs. recovery.
7. **HexaMesh** (DAC'23) or **PlaceIT** (2025): one, for the physical-arrangement trend.
8. **Netrace** (NoCArc'10): you'll describe its dependency model; know it.
9. **NoCeption** (DATE'22) or **NOCTOPUS** (2026): one GNN surrogate paper, for the future-work argument.

Free copies: most are linked from the authors' pages or arXiv; ACM/IEEE versions via the DOIs in the .bib file.
