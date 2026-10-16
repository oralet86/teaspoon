# Background and prior work

## Adaptive operator selection in GAs is well covered

Every research question in the appendix has published work behind it. No
single recent paper has the exact combination (swap, inversion, insertion and
2-opt under probability matching on TSPLIB), so the ground is covered
piecewise.

| Question | Prior work | What it already shows |
|---|---|---|
| Score per operator, learning rate, reward, probability matching | Fialho, *Adaptive Operator Selection for Optimization* (PhD thesis, 2010) | The mechanism is the textbook baseline. The thesis compares it with adaptive pursuit and bandit rules and recommends rank-based bandits. |
| Same | Da Costa et al., adaptive operator selection with dynamic multi-armed bandits (GECCO 2008) | Bandit selection that handles operators whose usefulness changes during the run. |
| Same | Sharma et al., RecPM-AOS (PPSN 2018) | A newer probability-matching variant, tested against other selection rules. |
| Does a GA on TSP improve by choosing operators adaptively? | *Controlling Genetic Algorithms with Reinforcement Learning* | A Q(λ) agent picks crossover and mutation pairs. It beats a non-adaptive GA on 40- and 100-city TSP. |
| Same | *Study of genetic algorithm with reinforcement learning to solve the TSP* (2009) | A Q-learning-based mutation inside a GA, tested on TSPLIB. |
| Same | *Enhancing Genetic Algorithms using Multi Mutations* (2016) | Several mutation operators applied together on TSP, compared with single fixed ones. |
| Fixed operator strategies compared | *Appropriate Combination of Crossover and Mutation Operators in GAs for the TSP* (2024) | Six mutation and seven crossover operators on TSPLIB. Insertion mutation came out best in their setup. |
| Adaptive versus random selection | Turkeš, Sörensen and Hvattum, meta-analysis of ALNS adaptiveness (EJOR 2021) | Across 25 implementations the adaptive layer improves the objective by 0.14% on average (95% interval 0.07 to 0.22%). The authors recommend it only in specific situations. |
| Same | Kallestad et al., deep RL hyper-heuristic (EJOR 2023) | A learned selector compared against both ALNS and uniform random selection. |
| Same | Lagos and Pereira, multi-armed bandit hyper-heuristics (EJOR 2024) | Bandit selection of low-level heuristics. It identifies heuristics that do not help. |
| Selection among swap, insertion, 2-opt style heuristics | Mischek and Musliu, RL for cross-domain hyper-heuristics (IJCAI 2022) | RL selects low-level heuristics across the HyFlex benchmark domains. |
| Different preferences for different landscapes | Consoli et al., dynamic selection of operators from landscape analysis (2016) | Online landscape features drive operator choice, on arc routing. |
| Same | Aydin et al., feature-based search space characterisation for data-driven AOS (2024) | Landscape features feed a supervised operator selector. |
| Same | University of Coimbra thesis on landscape metrics and operator choice for TSP | Reports that predicting good operators from landscape metrics is not trivial. |

The finding that matters most is the meta-analysis: the expected gain of
adaptive over non-adaptive selection is small, so any result must beat a
uniform-random baseline.

## Time-aware selection

Rewarding improvement per unit of time is established in hyper-heuristics
(2000), in large neighbourhood search (2007) and, as theory, in budgeted
bandits. It is not a contribution by itself.

"Checked" says what was actually read on 2026-10-12. Nine searches; not a
systematic review.

### Improvement divided by time as the selection signal

| Work | What it does | Checked |
|---|---|---|
| Cowling, Kendall, Soubeiga, choice function hyper-heuristic (2000). Described in [Drake, Özcan, Burke, "An Improved Choice Function Heuristic Selection for Cross Domain Heuristic Search" (2012)](https://people.cs.nott.ac.uk/pszeo/docs/publications/improvedCF.pdf) | Scores each low-level heuristic by its change in objective divided by the time it took, weighted towards recent uses, plus a pairwise term and a time-since-last-use term. | Formula read in the Drake et al. PDF. The 2000 paper itself not opened. |
| Laborie and Godard, "Self-adapting large neighborhood search: Application to single-mode scheduling problems" (2007) | Self-adapting choice between large-neighbourhood operators; later papers describe its reward in terms of objective improvement and operator time. | Not opened. Known only through citations. No link found. |
| [Thomas and Schaus, "Revisiting the Self-adaptive Large Neighborhood Search" (CPAIOR 2018)](https://webperso.info.ucl.ac.be/~pschaus/assets/publi/cpaior18_alns.pdf) | Revisits Laborie and Godard and changes the weight update for the case where operators have unequal running times. Closest match to our question. | Abstract seen in the search index. The PDF link returned 404 to my fetch; DOI is probably [10.1007/978-3-319-93031-2_40](https://doi.org/10.1007/978-3-319-93031-2_40), which resolves but which I could not read. |
| [Maturana and Saubion, "A Compass to Guide Genetic Algorithms" (PPSN 2008)](https://doi.org/10.1007/978-3-540-87700-4_26) | Operator control for a GA that considers each operator's effect on quality, population diversity and computation time. Tested on SAT. | Abstract seen in the search index. Full text not opened. |
| [Dudel, "Runtime-Sensitive Learned Operator Selection in ALNS" (Junior Management Science 11(1), 2026)](https://doi.org/10.5282/jums/v11i1pp1-26) | Gives slow operators reduced rewards, and trains a Deep-Q selection policy on both solution quality and operator runtime. CVRP with 100 to 400 customers. | Abstract read on the journal page. |

#### Theory: bandits where each pull has a cost

| Work | What it does | Checked |
|---|---|---|
| [Tran-Thanh, Chapman, Rogers, Jennings, "Knapsack Based Optimal Policies for Budget-Limited Multi-Armed Bandits" (AAAI 2012)](https://arxiv.org/abs/1204.1909) | Arms have costs and a shared budget. The best policy is a mix of arms chosen by reward-to-cost ratio, not the single best arm. | Abstract seen in the search index. |
| [Badanidiyuru, Kleinberg, Slivkins, "Bandits with Knapsacks" (FOCS 2013)](https://arxiv.org/abs/1305.2545) | General model of bandits under resource constraints, with near-optimal algorithms. | Abstract seen in the search index. |

#### Background for the hypothesis

| Work | What it does | Checked |
|---|---|---|
| [Hansen and Mladenović, "First vs. best improvement: An empirical study" (Discrete Applied Mathematics 154(5), 2006)](https://doi.org/10.1016/j.dam.2005.05.020) | For 2-opt on the TSP, first improvement beats best improvement from a random start; best improvement is better and faster from greedy or nearest-neighbour starts. | Abstract read on a repository page. |
| [Jeong and Song, "Meta-learning-based adaptive operator selection for traveling salesman problem" (Applied Soft Computing 185, 2025)](https://doi.org/10.1016/j.asoc.2025.113930) | LSTM that predicts operator performance in a GA for the TSP. Shows the GA-on-TSP setting is still being published. | Abstract read. Whether its reward uses time is not stated there. |
| [Consoli, Geng, Minku, Yao, "Dynamic selection of evolutionary operators based on online learning and fitness landscape analysis" (Soft Computing, 2016)](https://research.birmingham.ac.uk/files/27714088/Consoli_Dynamic_selection_Soft_Computing.pdf) | States that almost all credit assignment strategies rely only on offspring fitness. | Sentence seen in the search index; PDF returned 403 to my fetch. Author list from memory. |

#### Not found

- A time-aware reward for choosing between mutation operators in a GA for the
  TSP, with the budget in the same unit.
- A test of whether the best operator per unit of time changes during a run,
  for cheap random moves against a scan-based move.
- A comparison of the best operator across implementations (Claim 3). This
  was not searched for specifically.

"Not found" after nine searches is weak evidence. Thomas and Schaus, and the
ALNS line generally, are close enough that a reviewer would expect them cited
and compared against.

### Read in full before building on this

- Thomas and Schaus (2018): their weight update for unequal running times may
  already be the method we would propose.
- Laborie and Godard (2007): the original reward definition.
- Pisinger and Ropke, "A general heuristic for vehicle routing problems"
  (2007): it may normalise operator scores by time; not confirmed.
- Hansen and Mladenović (2006): how strong the first-versus-best effect is
  and at what tour quality it flips.

## Caveats

- **Abstracts only.** Most papers on adaptive operator selection in GAs were
  read as abstracts or search summaries, not full texts. The time-aware
  tables say per entry what was read.
- **When adaptiveness helps.** Conditions such as a large operator pool or
  intermediate run lengths are sometimes attributed to the ALNS meta-analysis;
  they were not checked in its full text.
- **Who tested on the TSP.** Fialho's and Maturana's work used other
  benchmarks as far as known; recheck before citing them for TSP results.
