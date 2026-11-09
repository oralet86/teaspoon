# Background and prior work

## Adaptive operator selection in GAs is well covered

Every research question in the appendix has published work behind it. No
single recent paper has the exact combination (swap, inversion, insertion and
2-opt under probability matching on TSPLIB), so the ground is covered
piecewise.

| Question | Prior work | What it already shows |
|---|---|---|
| Score per operator, learning rate, reward, probability matching | Fialho, *Adaptive Operator Selection for Optimization* (PhD thesis, 2010) | The mechanism is the textbook baseline. The thesis compares it with adaptive pursuit and bandit rules and recommends rank-based (AUC) bandits as efficient and robust. Its baseline set (each operator alone, uniform, best static mix, fitness-indexed oracle) is our Claim 1 design. Tested on OneMax, Royal Road, Long k-Path, SAT and BBOB, not the TSP. Read: defense slides only ([note](../papers/notes/fialho2010-aos-thesis-defense.md)). |
| Same | Da Costa et al., adaptive operator selection with dynamic multi-armed bandits (GECCO 2008) | Bandit selection that handles operators whose usefulness changes during the run. |
| Same | Sharma et al., RecPM-AOS (PPSN 2018) | A newer probability-matching variant, tested against other selection rules. |
| Does a GA on TSP improve by choosing operators adaptively? | Pettinger and Everson, *Controlling Genetic Algorithms with Reinforcement Learning* (GECCO 2002, p. 692) | A Q(λ) agent picks crossover and mutation pairs. It beats a non-adaptive GA on 40- and 100-city TSP. Not read. |
| Same | Boomsma, CEC 2003 and EvoCOP 2004 ([notes](../papers/notes/boomsma2004-aos-comparison-tsp.md)) | Seven schedulers over 18 operators on TSPLIB up to 3,038 cities; greedy crossover leads early, EAX late; uniform selection is close behind the adaptive methods; budgets in evaluations. |
| Same | Jeong and Song, Applied Soft Computing 185, 2025 ([note](../papers/notes/jeong2025-lstm-aos-tsp.md)) | Offline-trained LSTM picks one crossover and one mutation per generation; 9.87% median gap vs 12.20% for the best fixed pair; no time in the reward; no uniform baseline; weak base GA. |
| Same | *Study of genetic algorithm with reinforcement learning to solve the TSP* (2009) | A Q-learning-based mutation inside a GA, tested on TSPLIB. |
| Same | *Enhancing Genetic Algorithms using Multi Mutations* (2016) | Several mutation operators applied together on TSP, compared with single fixed ones. |
| Fixed operator strategies compared | *Appropriate Combination of Crossover and Mutation Operators in GAs for the TSP* (2024) | Six mutation and seven crossover operators on TSPLIB. Insertion mutation came out best in their setup. |
| Adaptive versus random selection | Turkeš, Sörensen and Hvattum, meta-analysis of ALNS adaptiveness (EJOR 2021) ([note](../papers/notes/turkes2021-alns-adaptiveness-meta-analysis.md)) | Across 25 implementations the adaptive layer improves the objective by 0.14% on average (95% interval 0.07 to 0.22%; random-effects model). It matters when the pool contains heuristics aimed at different problems or instance types; it adds almost nothing once the pool is pruned offline. Iteration budgets bias the comparison. Read: 2019 working-paper version. |
| Same | Gretsista and Burke, LION 2017 ([note](../papers/notes/gretsista2017-ils-aos-nurse-rostering.md)) | Time-normalised perturbation selection in an ILS for nurse rostering; the best single perturbation beats every selector; PM, softmax and UCB behave like uniform. |
| Same | Pei, Mei, Liu, Yao, PRICAI 2022 ([note](../papers/notes/pei2022-aos-complex-vrp.md)) | Stateless AOS never beats the fixed operator order of a state-of-the-art local search for a large real-world VRP; the best operator persists between consecutive solutions only 12–27% of the time. |
| Same | Kallestad et al., deep RL hyper-heuristic (EJOR 2023) | A learned selector compared against both ALNS and uniform random selection. |
| Same | Lagos and Pereira, multi-armed bandit hyper-heuristics (EJOR 2024) | Bandit selection of low-level heuristics. It identifies heuristics that do not help. |
| Selection among swap, insertion, 2-opt style heuristics | Mischek and Musliu, RL for cross-domain hyper-heuristics (IJCAI 2022) | RL selects low-level heuristics across the HyFlex benchmark domains. Not read. |
| Same | Kletzander and Musliu, LAST-RL, AAAI 2023 ([note](../papers/notes/kletzander2023-last-rl.md)) | SARSA(λ) over 15 trajectory features with Luby-length chains; 1st on CHeSC overall but 4th on TSP; runtime penalty helps only when heuristic costs differ widely. |
| Same | Dantas, do Rego, Pozo, GECCO 2021 Companion ([note](../papers/notes/dantas2021-dqn-hyperheuristic.md)) | DQN vs DMAB and FRRMAB on HyFlex TSP and VRP; DMAB best on 8 of 10 TSP instances. |
| Fitness-level-dependent operator choice | Zhang, Weise, Li, CEC 2014 ([note](../papers/notes/zhang2014-fitness-level-aos.md)) | One operator distribution per fitness level (cutting stock); better than one global distribution; learned distributions are not reported. |
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

"Checked" says what was actually read. Started with nine searches on
2026-10-12; updated 2026-10-16 after reading the full texts listed in
[`papers/notes/`](../papers/notes/README.md). Not a systematic review.

### Improvement divided by time as the selection signal

| Work | What it does | Checked |
|---|---|---|
| Cowling, Kendall, Soubeiga, choice function hyper-heuristic (2000). Described in [Drake, Özcan, Burke, "An Improved Choice Function Heuristic Selection for Cross Domain Heuristic Search" (2012)](https://people.cs.nott.ac.uk/pszeo/docs/publications/improvedCF.pdf) | Scores each low-level heuristic by its change in objective divided by the time it took, weighted towards recent uses, plus a pairwise term and a time-since-last-use term. | Formula read in the Drake et al. PDF. The 2000 paper itself not opened. |
| Corne, Ross, Fang, COBRA (GA Research Note 7, Edinburgh, 1994); studied by Tuson and Ross, Evolutionary Computation 6(2), 1998 ([note](../papers/notes/tuson1998-adapting-operator-settings.md)) | Every G evaluations, ranks operators by benefit/cost (mean gain of improving children divided by the computational effort of evaluating a child) and assigns fixed probabilities by rank. No performance gain over a tuned fixed GA; the most productive operator can want a low share. | Tuson and Ross read in full (research-paper version); COBRA's original note not opened. The cost actually used in the experiments is not stated. |
| Barbosa and Sá, "On adaptive operator probabilities in real coded genetic algorithms" (Chilean Computer Science Society, 2000) | Probability matching in a real-coded GA, with the reward lowered by the operator's cost in function evaluations. | Known only from DaCosta et al. (2008). Not opened. |
| Laborie and Godard, "Self-adapting large neighborhood search: Application to single-mode scheduling problems" (MISTA 2007) | Self-adapting choice between large-neighbourhood operators. Reward per use r = Δc/Δt, smoothed with a reaction factor α. | Reward confirmed in Thomas and Schaus (2018). The paper itself not opened. |
| [Thomas and Schaus, "Revisiting the Self-adaptive Large Neighborhood Search" (CPAIOR 2018)](https://doi.org/10.1007/978-3-319-93031-2_40) ([note](../papers/notes/thomas2018-revisiting-salns.md)) | Fixes the bias of Laborie and Godard's update for operators with unequal run times: weight = summed Δc over summed Δt in a time window anchored at the last improvement, blended with the run-long ratio. 240 s wall-clock budget. Neither adaptive variant beats the per-instance best fixed operator pair. **This is the estimator we planned for Claim 2.** | Read in full (authors' version). |
| [Gretsista and Burke, "An Iterated Local Search Framework with Adaptive Operator Selection for Nurse Rostering" (LION 2017)](https://doi.org/10.1007/978-3-319-69404-7_7) | Reward = (1 + improvements) / cumulative time of the perturbation. | Read in full. |
| Kletzander and Musliu, "Large-State Reinforcement Learning for Hyper-Heuristics" (AAAI 2023, 37(10), pp. 12444–12452) | Runtime penalty 100·t′/(T·c′) at chain end; useful only where heuristic run times differ widely. | Read in full. |
| [Maturana and Saubion, "A Compass to Guide Genetic Algorithms" (PPSN 2008)](https://doi.org/10.1007/978-3-540-87700-4_26) | Operator control for a GA from each operator's effect on quality and population diversity. Whether execution time enters is **unverified**: the AOS survey (Pei et al. 2025) describes Compass as a weighted sum of fitness and diversity only. Tested on SAT. | Abstract seen in the search index. Full text not opened. DOI confirmed by the reference list of Pei et al. (2022). |
| [Dudel, "Runtime-Sensitive Learned Operator Selection in ALNS" (Junior Management Science 11(1), 2026)](https://doi.org/10.5282/jums/v11i1pp1-26) | Gives slow operators reduced rewards, and trains a Deep-Q selection policy on both solution quality and operator runtime. CVRP with 100 to 400 customers. | Abstract read on the journal page. |

#### Theory: bandits where each pull has a cost

| Work | What it does | Checked |
|---|---|---|
| [Tran-Thanh, Chapman, Rogers, Jennings, "Knapsack Based Optimal Policies for Budget-Limited Multi-Armed Bandits" (AAAI 2012)](https://arxiv.org/abs/1204.1909) | Arms have costs and a shared budget. The best policy is a mix of arms chosen by reward-to-cost ratio, not the single best arm. | Abstract seen in the search index. |
| [Badanidiyuru, Kleinberg, Slivkins, "Bandits with Knapsacks" (FOCS 2013)](https://arxiv.org/abs/1305.2545) | General model of bandits under resource constraints, with near-optimal algorithms. | Abstract seen in the search index. |

#### Background for the hypothesis

| Work | What it does | Checked |
|---|---|---|
| [Hansen and Mladenović, "First vs. best improvement: An empirical study" (Discrete Applied Mathematics 154(5), 2006)](https://doi.org/10.1016/j.dam.2005.05.020) | For 2-opt descent on the TSP, first improvement beats best improvement from a random start (3.5% better and about 100× faster with neighbour lists at n = 1000); from greedy or nearest-neighbour starts best improvement is slightly better and not slower. The flip point in tour quality is **not** located. | Read in full ([note](../papers/notes/hansen2006-first-vs-best.md)). |
| Johnson and McGeoch, "The Traveling Salesman Problem: A Case Study in Local Optimization" (1997) | Costs and quality of 2-opt, 3-opt, LK and iterated LK; neighbour lists, don't-look bits and tour data structures change the cost class; comparisons on a time-equalised basis. | Sections 2.5, 3, 4.2 and the ILK part of 6 read ([note](../papers/notes/johnson1997-tsp-local-optimization.md)). |
| [Consoli, Geng, Minku, Yao, "Dynamic selection of evolutionary operators based on online learning and fitness landscape analysis" (Soft Computing, 2016)](https://research.birmingham.ac.uk/files/27714088/Consoli_Dynamic_selection_Soft_Computing.pdf) | States that almost all credit assignment strategies rely only on offspring fitness. | Sentence seen in the search index; PDF returned 403 to my fetch. Author list from memory. |

#### Not found

After reading 23 sources (2026-10-16), still not found:

- A study that compares operators or selectors under both an evaluation
  budget and a wall-clock budget and reports whether the conclusions change
  (`novelty.md`, N1). Turkeš et al. name the bias but do not measure it.
- A measured map of which operator has the best gain **per second** at each
  tour quality on the TSP, with located switch points (N2). The closest are
  Fialho's OneMax curves (evaluations) and Pei et al. 2022 (per move, VRP, no
  time).
- A tuned time-based schedule used as the competitor to an adaptive selector
  (N6).

Still weak evidence: the reading followed the AOS survey's references, not a
systematic search.

### Still to read

Laborie and Godard (2007), Pisinger and Ropke (2007), Maturana and Saubion
(2008) and Pei et al. (2023) are open; they are tracked in
[`papers/reading-list.md`](../papers/reading-list.md), second round.

## Caveats

- **Read status varies.** Entries with a link to `papers/notes/` were read
  as full texts (or the parts listed in the note); the others are abstracts or
  second-hand descriptions.
- **When adaptiveness helps.** Checked in the Turkeš et al. full text
  (2019 version): it helps when the pool contains heuristics aimed at
  different problems or instance types, and little once the pool is pruned
  offline. "Large pools" or "redundant pools dilute the reward" are **not**
  findings of that paper; the number of studies was too small for moderator
  analysis. Limited time increasing the benefit is stated as an expectation,
  not tested.
- **Who tested on the TSP.** Fialho: not the TSP (OneMax, Royal Road, Long
  k-Path, SAT, BBOB), confirmed from the defense slides. Maturana and Saubion:
  SAT, as far as known.
- **Deep-research report (`deepresearch.txt`).** Several of its statements
  were wrong: the Compass DOI, the Laborie and Godard title (it gave Godard,
  Laborie, Nuijten, ICAPS 2005), and the pool-size and statistics claims about
  Turkeš et al. Use it only as a list of titles.
