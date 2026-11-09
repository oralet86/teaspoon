# Paper-triage profile: teaspoon
Built 2026-11-09 from: README.md, roadmap.md, ideas/claims.md, ideas/novelty.md, ideas/prior-work.md

- Goal: a GECCO 2027 paper (deadline about 2027-01-25) on when the choice of operator matters under a wall-clock time budget; later, operator selection inside state-of-the-art routing solvers such as LKH-3. (README.md, ideas/claims.md)
- Problem: symmetric TSP local search with 16 operators (2-opt variants, Or-opt, 3-opt, LK, kicks/double bridge, ruin & recreate), on uniform, clustered, tspgen and random-matrix instances; later CVRP/LKH-3 variants. (README.md, ideas/claims.md)
- Core mechanism: per-state measurement of each operator's gain per second (E1 / measurement A) by local-optimality level; greedy-by-level policy and rollouts measuring greedy regret (E1R); adaptive operator selection and hyper-heuristics as the method family. (ideas/claims.md)
- Distinguishing aspects: (ideas/claims.md, ideas/novelty.md)
  - time is counted in the budget of every comparison, not only in the reward (N1);
  - every operator gets its best implementation (Rust, two tour representations, candidate lists, don't-look bits), and timing hygiene is part of the method;
  - the search state is the local-optimality level, which depends on the candidate set (Claims 1.1, 1.6);
  - the fixed cascade (2-opt -> 3-opt -> LK -> kick) or a tuned time schedule is the competitor to beat (N6).
- Items:
  - CORE: ground rule (improvement per wall-clock second at equal time budget); Claim 1.1 (leader changes during a run); Claim 1.2 (leader changes between instances); Claim 1.4 (greedy choice regret, rollouts); Claim 1.5 (per-application vs per-second leader); Claim 1.6 (local optimality depends on candidate set); Claim 1 competitor set (best single, best per instance, uniform random, fixed schedule/cascade); E1/E1R measurement protocol, instance families and statistics; Rust implementation and timing hygiene (E0). (ideas/claims.md, roadmap.md)
  - PLANNED: Claim 2 (online selector estimating the best operator with a time-normalised reward, its own time charged, tuned on other instances); E2-E5 full runs with twin evaluation/time budgets (N1); N4 (does slicing restore momentum). (ideas/claims.md, ideas/novelty.md)
  - EDGE: Claim 4 / LKH-3 operator selection (paused); Claim 5 learned per-level candidate selector (DIMES/DIFUSCO/NeuOpt, granular neighbourhoods, alpha candidates); chained selection with opportunity-cost acceptance (N7); local-context selection (N5); learned/RL selectors in general. (ideas/claims.md, roadmap.md)
- Conceded precedents: (ideas/novelty.md)
  - AOS in a GA for the TSP -> Boomsma 2003/2004, Jeong and Song 2025
  - reward = improvement per unit time -> COBRA / Tuson and Ross 1998, Cowling et al. 2000, Laborie and Godard 2007, Thomas and Schaus 2018, Gretsista and Burke 2017
  - Claim 1 competitor design -> Fialho 2010
  - per-solution best-operator measurement -> Pei et al. 2022
  - one operator distribution per fitness level -> Zhang, Weise, Li 2014
  - best operator changes during a run (without time) -> Boomsma 2004, Jeong and Song 2025, Hansen and Mladenovic 2006
  - chains judged as a whole -> Kheiri and Keedwell 2015, Chuang 2020, Kletzander and Musliu 2023
  - learned selectors on routing -> Lu et al. 2020, Dantas et al. 2021, LAST-RL 2023
- Already read: none recorded (papers/notes/ was cleared on 2026-11-09).
