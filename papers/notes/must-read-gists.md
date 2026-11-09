# Must-read papers: chapter-by-chapter gist

Built 2026-11-09 from the five must-read entries in `papers/triage-unfetched.csv`.
Four papers were present in `papers/`;

Every quotation below is verbatim from the paper. A short “Why it matters”
line says what the chapter does for teaspoon.

---

## Buzdalova, Kononov & Buzdalov (2014) — *Selecting Evolutionary Operators using Reinforcement Learning: Initial Explorations*

GECCO 2014 companion. Score 2/2 (must-read) on title. Directly tests RL-based
operator selection, including on the TSP, and is the historical precedent for
the state/reward design in Claim 2.

### Abstract (p. 1)

> We propose an adaptive method of selecting evolutionary operators, which takes a set of possible operators as input and learns what operators are efficient for the considered problem. One evolutionary algorithm run should be enough for both learning and obtaining suitable performance.

> The obtained results show that the proposed method significantly outperforms random selection, since it manages to select efficient evolutionary operators and ignore inefficient ones.

**Why it matters.** It states the exact question teaspoon asks — can one run
learn which operator is efficient — and uses random selection as the
competitor, which is one of teaspoon’s Claim 1 baselines.

### 1. Introduction (pp. 1–2)

> Choosing the best operator manually is resource and time consuming, so automation is needed.

> There are different techniques of adjusting evolutionary algorithms [4], including methods for adjusting and modifying of operators, but there is no the most efficient one.

**Why it matters.** Frames operator selection as an open automation problem and
shows that the choice is not settled by existing parameter-control methods.

### 2. Method description (p. 2)

> The reward is calculated as the difference of the best individual fitness in a two consequent generations of the evolutionary algorithm.

> The agent chooses an operator and the next population of the evolutionary algorithm is generated. Then an immediate numerical reward, as well as some representation of the algorithm state, are returned to the agent.

**Why it matters.** This is the simplest form of the online state → operator →
reward loop that teaspoon’s Claim 2 needs; the reward is per generation, not per
second, so it is a clean contrast to the project’s time-normalised reward.

### 3. Experiment 1: H-IFF optimisation (pp. 2–3)

> using EA+RL(O) allows to maximize H-IFF in less number of generations than a random strategy does. According to the one-way ANOVA test we have performed [12], p-value for the EA+RL(O) and random selection was less than 5×10−4, so they are statistically distinguishable.

> It is also important to notice that EA+RL(O) successfully handles the obstructive operator case.

**Why it matters.** Gives a statistically tested positive result against random
selection and, importantly, shows the learner can *ignore* a deliberately bad
operator — evidence that selection, not just having several operators, is doing
the work.

### 4. Experiment 2: Travelling Salesman Problem (pp. 3–4)

> EA+RL(O) outperforms the evolutionary algorithm with random selection of operators. According to the one-way ANOVA test we performed [12], p-value for the EA+RL(O) and random selection is less than 5 × 10−4, so they are statistically distinguishable.

> Notice that, if we replace the numerical values mentioned above with parameters, this state is problem-independent, thus can be used for solving any problem with EA+RL(O) method.

**Why it matters.** The TSP result is the closest historical analogue to
teaspoon’s problem, and the state is explicitly designed to be problem-independent
by using generation and fitness-improvement intervals — a precursor of a
state-based selector.

### 5. Conclusion (p. 4)

> A new EA+RL(O) method of adaptive selection between evolutionary operators using reinforcement learning is proposed.

**Why it matters.** Names the method teaspoon should cite as the earliest
RL-for-operator-selection precedent; its limitations (population-level choice,
fitness-difference reward, no wall-clock accounting) define what remains new.

---

## Durgut, Aydin & Atli (2021) — *Adaptive operator selection with reinforcement learning*

*Information Sciences* 581:773–790. Score 2/2 (must-read). The closest
state-based RL selector: Q-learning plus clustering maps the **problem state**
to the best operator, and it explicitly criticises prior AOS for ignoring state.

### Abstract (p. 1)

> State of art adaptive selection schemes have been proposed to select the best next operator without considering the problem state in the process. In this study, a reinforcement learning algorithm is proposed to embed in a standard artificial bee colony algorithm for taking the problem state on board in operator selection process.

> The state of art approaches are outperformed with respect to the quality of solution in solving Set Union Knapsack problem over 30 benchmarking instances.

**Why it matters.** This is the paper’s core novelty claim and the reason it is a
must-read: it moves AOS from a stateless success history to a state-conditioned
decision, which is also teaspoon’s Claim 2 direction.

### 1. Introduction (pp. 1–2)

> A wide range of studies demonstrated that recruiting multiple operators offers a better payoff with respect to efficiency [7,8]. Studies reveal that harmonic use of complementary operators helps improve the diversity of search, but, a well-customised switch scheme will be required to achieve higher efficiency.

> It is clear that a comprehensive study has not been seen yet on use of RL in building adaptive operator selection schemes in order to furnish artificial bee colony (ABC) algorithms for solving binary problems.

**Why it matters.** Separates the value of a *pool* of operators from the value
of the *switching scheme* — exactly the distinction teaspoon’s Claim 1
competitors are designed to test.

### 2.1. Artificial bee colony algorithms (p. 2)

> Two approaches are mainly devised for implementing ABC to solve combinatorial problems. The commonly used approach is to keep the continuous variables in use with an operation to map the variable in use to a binary vector and vice versa [19].

**Why it matters.** Shows the algorithmic frame is a population metaheuristic on
a binary representation, not local search on a TSP tour; it is a method-family
precedent, not a direct competitor.

### 2.2. Adaptive operator selection (pp. 2–3)

> In other words, an operator that performs well at the beginning might act poorly over time or vice versa. Operator selection involves a two-stage process; credit assignment and operator selection [20,21].

> The most popular and promising algorithms are Probability Matching (PM) [29], Adaptive Pursuit (AP) [30] as the probability-based method and multi-armed bandit-based (MAB) methods [31,26,21].

**Why it matters.** Gives the standard credit-assignment/selection decomposition
and the PM/AP/MAB baseline family that teaspoon’s Claim 2 selector must beat.

### 2.3. Reinforcement learning (p. 3)

> The main principle behind reinforcement learning is to let agents learn how to act optimally in order to maximise the environmental reward generated in response to their actions.

> It would facilitate building more conscious adaptive selection schemes to take inputs into account while selecting the operators and crediting each aftermath of conducting each operation.

**Why it matters.** States the argument for using RL rather than a bandit: the
state (input) enters the decision, which is the feature teaspoon needs.

### 2.4. Set union knapsack problem (pp. 3–4)

> SUKP is a special case of generic knapsack problem, which involves items to be optimally composed in subsets so as to gain the maximum benefit.

**Why it matters.** Confirms the test bed is a binary knapsack problem, not a
routing problem; the transfer of the method to TSP is an open question, not a
result.

### 2.5. Related works (p. 4)

> Although a considerable level of success has been achieved through these studies, the situational information such as problem state has not been considered by any of state-of-the-art studies. None of the adaptive approaches reported above have considered the problem state and/or the history of previous acts while selecting an operator to produce new solutions.

**Why it matters.** This is the gap teaspoon also targets. It is the closest
published claim that state-aware AOS is missing, and therefore the paper to
cite and differentiate from.

### 3. Operator selection with reinforcement learning (pp. 4–5)

> The problem state does not play a major role in the operator selection process while an efficient selection mechanism cannot avoid disregarding the problem state from the selection process.

**Why it matters.** The design premise: selection must condition on state. It
supports teaspoon’s state-based reference arm, though the state here is the
solution vector, not the local-optimality level.

### 3.1. Operator selection (pp. 5–6)

> The success level per operation can be estimated based on either the value of an objective function, i.e. a value in real number, or the success status, i.e. success or failure.

**Why it matters.** Makes explicit the credit-assignment choice teaspoon faces;
the paper uses objective-value rewards, whereas teaspoon uses gain per second.

### 3.2. Q-learning combined with clustering (pp. 6–7)

> Q Table is the most and earlier used data structure proposed to sustain learned experience [35]. However, Q Table is not sufficiently scalable with respect to problem size.

**Why it matters.** Identifies the scalability limit that motivates clustering
the state space; teaspoon faces the same issue when the state includes
local-optimality level, instance and candidate set.

### 3.3. ABC algorithm embedded with combined Q-learning (RLABC) (pp. 7–9)

> The adaptive operator selector (AOS), composed of the three components sketched in Fig. 1 and explained above, has been embedded in a standard artificial bee colony (ABC) algorithm, labeled with RLABC henceforth, to solve SUKP problems as indicated before.

**Why it matters.** Defines the full architecture — selection engine, evaluator,
learner — that can be compared with teaspoon’s proposed selector loop.

### 4.1. RLABC implementation (pp. 9–11)

> The algorithm solves 2 problems inherently; (i) it seeks for the best set of items to place in a knapsack, while (ii) it also looks for the set of best operator to be paired with each solution

**Why it matters.** Shows the operator pool is three binary ABC variants; the
pool is small and homogeneous, unlike teaspoon’s 16 local-search operators with
very different costs.

### 4.2. Test problems (p. 11)

> He et al. [47] have introduced 30 benchmarking problem instances of SUKP as tabulated in Table 1 with all configuration details, where 3 different configurations presented varying with comparative status of m and n

**Why it matters.** Gives the 30-instance benchmark that a state-aware selector
is evaluated on; useful as a contrast to teaspoon’s TSP families.

### 4.3. Parameter tuning (pp. 12–14)

> The results suggest that RLABC outperforms PM in most of the instances while Random produces the worst results as expected.

> The best parametric setting is concluded that c is 0.3, W is 25 iterations, reward is extreme

**Why it matters.** Reports both the headline comparison (beats PM, beats random)
and the tuned settings, which are the kind of protocol detail teaspoon’s Claim 2
must report and charge.

### 4.4. Comparative discussions (pp. 14–15)

> RLABC overwhelmingly solves the instance problems of Set1 and Set3 better; winning 8 cases out of 10, while solves 6 out of 10 cases in Set2 .

**Why it matters.** The strongest result claim: a state-aware RL selector beats
state-of-the-art metaheuristics on most instances. It is the empirical bar
teaspoon would have to match or explain.

### 5. Conclusion (pp. 15–17)

> In this paper, a novel adaptive operator selection scheme has been proposed based on reinforcement learning in which the problem states are mapped to the operators based on the success level per operation.

> For longer term, it is needed to achieve transferability of learned experience via RL in problem solving while gained experience with some problem types and instances can be exploited in solving different problem types and instances.

**Why it matters.** Names the next gap — transferability — which is exactly what
the 2022 follow-up addresses and what teaspoon’s “tuned on other instances”
protocol must handle.

---

## Durgut, Aydin & Rakib (2022) — *Transfer Learning for Operator Selection: A Reinforcement Learning Approach*

*Algorithms* 15(1):24. Score 2/2 (must-read). The follow-up to Durgut 2021:
it tests whether a learned operator selector can be reused across runs, which is
the training/test split issue in teaspoon’s Claim 2.

### Abstract (p. 1)

> However, existing research fails to show that learned information may be transferred from one problem-solving procedure to another. The primary goal of the proposed research is to determine the impact of transfer learning on RL and MOAs.

> The learning process, according to the findings, improved the convergence speed while significantly reducing the CPU time.

**Why it matters.** States the transfer question and the headline result:
transferred experience mainly buys time, not solution quality. That is directly
relevant to teaspoon’s distinction between time budget and solution quality.

### 1. Introduction (pp. 1–2)

> The problem of operator selection becomes a sequencing problem in the sense that additional operators are added one after the other to make it easier to move solutions to more fruitful regions of the search space.

> This is the first attempt, to the best of the authors’ knowledge, to apply transfer learning in building adaptive operator selection processes designed with reinforcement learning and implemented in swarm intelligence algorithms, such as the artificial bee colony algorithm.

**Why it matters.** Frames operator selection as a sequence, not an independent
per-step choice; teaspoon’s chained-selection idea (N7) is the same shift.

### 2.1. Artificial bee colony algorithm (pp. 2–3)

> The outmost optimisation framework used in this study is a swarm intelligence algorithm, which is the artificial bee colony algorithm (ABC). It is a population-based metaheuristic and evolutionary technique developed inspired by the foraging behavior of honey bees when seeking a quality food source [6].

**Why it matters.** Confirms the host algorithm is a population metaheuristic;
the transfer result is about ABC, not about local search on a tour.

### 2.2. Reinforcement learning (pp. 3–4)

> Reinforcement Learning (RL) is a type of machine learning technique for solving sequential decision-making problems. In this technique, a learning agent interacts with the environment to improve its performance through trial and error [9].

**Why it matters.** A compact statement of the sequential-decision view that
justifies treating operator selection as an RL problem.

### 2.3. Adaptive operator selection (p. 4)

> In practice, Credit Assignment (CA) and Operator Selection (OS) are the two components that are used during the operator selection process [13,14].

**Why it matters.** Repeats the two-component decomposition; teaspoon’s Claim 2
must specify both, and its reward is time-normalised while this paper’s is not.

### 2.4. Transfer learning (p. 4)

> The following three critical questions must be addressed during the transfer learning process: What needs to be transferred, when should it be transferred, and how should it be transferred?

**Why it matters.** Gives the three design questions teaspoon must answer when
it tunes a selector on other instances and transfers it to the test set.

### 2.5. Related work (pp. 4–5)

> Despite the fact that these research studies have shown significant results, no state-of-the-art studies have taken into account situational information such as problem state.

**Why it matters.** Repeats the state gap and positions both Durgut papers as
the state-aware alternative to stateless AOS.

### 3. Proposed approach for transfer learning with RL (pp. 5–8)

> The aim of this research is to investigate how to achieve some degree of transferability. In this context, it is envisaged that the knowledge and experience gained through prior searches be carried out on three levels: (i) transferring experience across the runs of the same problem subject to different circumstances, but with the same configuration and settings; (ii) different problems with the same size and context; and (iii) different problems of various sizes and contexts.

> The algorithm is set up to run once to solve a specific problem instance, with online learning switched ON to train the cluster centres and then switched OFF to repeat the experiments with the same problem instance but with new random number sequences.

**Why it matters.** Defines the three transfer levels and the concrete protocol
(train once, freeze, rerun). This is the closest precedent for teaspoon’s
“tuned on other instances, tested on the rest” requirement.

### 4.1. The problem and datasets (pp. 8–9)

> Both the selection scheme and the RL (i.e., Q learning) are embedded within a standard ABC algorithm equipped with three recent state-of-the-art binary operators; binABC [25], ibinABC [26], and disABC [27] to solve the set union knapsack problem (SUKP)

**Why it matters.** Confirms the experimental domain is SUKP with three binary
operators, so the transfer result does not directly carry to TSP local search.

### 4.2. Experimental settings (p. 9)

> three algorithms have been set up: (i) RLABC taken from [4] is the baseline algorithm that solves the problems with ABC embedded with a Q learning-based adaptive operators selection scheme, (ii) RLABC-T extends RLABC with a static transfer learning that switches off the gain experience in upcoming runs, and (iii) RLABC-TC keeps online learning while solving new problems and executes new runs.

**Why it matters.** Gives the three-arm design — no transfer, frozen transfer,
continued learning — which teaspoon can reuse when separating selector training
from evaluation.

### 4.3. Results and discussions (pp. 9–11)

> The results also indicate that RLABC-T and RLABC-TC remain competitive, but RLABC-TC has done slightly better than RLABC-T. Almost for all benchmarks except two instances, improvements on time performance are statistically meaningful.

> This suggests that when the problem dimension increases, RLABC has shown worse performance in terms of computational time.

**Why it matters.** The key empirical finding: transfer helps mainly at larger
sizes and mainly in CPU time; quality gains are small. That is a warning for
teaspoon’s Claim 2: a selector’s benefit may be a time effect, not a quality
effect, and size matters.

### 5. Conclusions and future work (pp. 15–16)

> The experimental results demonstrated that the transferred experience across runs helped achieve slightly better solution quality but significantly faster convergence. Both scenarios of keeping learning ON and OFF are tested, and it is observed that each has its own set of advantages and disadvantages.

> This study has considered the first level of experience and knowledge transfer in solving combinatorial optimisation problems, which implies training the agents in one run and utilising its gained experiences in the next runs. The next two levels, transfer across problem instances and problem types, remain as the future study

**Why it matters.** States the result and the open levels. teaspoon’s Claim 2
would be the “across instances” level that this paper leaves to future work —
a useful gap to cite.

---

## Mara, Norcahyo, Jodiawan, Lusiantoro & Rifai (2022) — *A survey of adaptive large neighborhood search algorithms and applications*

*Computers & Operations Research* 146:105903. Score 2/2 (must-read). The
systematic map of ALNS, the operator family teaspoon uses for ruin-and-recreate
and one of its Claim 1 competitors (Turkeš et al. is the other).

### Abstract (p. 1)

> This article provides a survey on the highly popular metaheuristic framework, the adaptive large neighborhood search (ALNS). The basic concepts of ALNS are discussed in this paper. Based on a simple taxonomy, the analysis of publication intensity, application areas, and the variant of ALNS features are executed on 252 scientific publications to synthesize the state-of-the-art of ALNS research.

**Why it matters.** Defines the scope: 252 papers, taxonomy, research trends.
It is the reference map for ALNS, not a method paper.

### 1. Introduction (pp. 1–2)

> ALNS is a relatively new metaheuristic which was first introduced by Stefan Ropke and David Pisinger in their seminal works (Ropke and Pisinger, 2006a; Ropke and Pisinger, 2006b; Pisinger and Ropke, 2007) as an extension of the Large Neighborhood Search (LNS) (Shaw, 1998). Like its preceding method, ALNS deploys two sub-classes of operators: destroy and repair.

> In this regard, the framework of ALNS allows us to utilize multiple neighborhoods within the same searching process in an adaptive way, where this adaptiveness is attained by recording the performance of each neighborhood and dynamically adjusting the selection of methods according to this record.

**Why it matters.** Gives the canonical definition and the adaptive mechanism:
record operator performance, adjust selection. That is the ancestor of
teaspoon’s per-operator measurement and selection loop.

### 2.1. Foundations of ALNS (pp. 2–3)

> The main benefit of considering the large neighborhood compared to small-neighborhood heuristics is that easier to navigate from one to another new promising area in the solution space of tightly constrained problems (Ropke and Pisinger, 2006a). However, the computational time of exploring large neighborhoods becomes the main trade-off when one considers implementing the large neighborhood-based heuristic.

**Why it matters.** Names the cost/benefit trade-off that teaspoon’s ground rule
formalises: large neighborhoods gain quality but cost time, so comparisons must
be time-equalised.

### 2.2. Basic structure of ALNS (p. 3)

> These probabilities are assigned based on the corresponding weight w−i and w+i Which implies the historical performance of each operator. The value of each w−i and each w+i are initially set equal and then updated every ηs iterations.

**Why it matters.** Describes the standard weighted-selection mechanism and its
update period; the period is a parameter teaspoon’s selector design has to
consider.

### 3. Survey methodology (pp. 3–4)

> In total, this search process resulted in a database of 252 journal articles published from 2006 to 2021.

> only articles that implement ALNS were included in this survey.

**Why it matters.** Establishes the evidence base and the inclusion rule. It
also shows the survey excludes pure LNS, so its counts are about the adaptive
layer specifically.

### 3.2. Taxonomy of studies on ALNS (p. 4)

> The second level of ALNS development consists of two sub-levels, namely the modification of ALNS structure (e.g., destroy and repair operators, adaptive mechanism, acceptance criterion, and termination criterion of the algorithm) and the hybridization with other optimization frameworks

**Why it matters.** Gives the taxonomy teaspoon can use to position its own
operator-selection contribution: which ALNS component is being changed.

### 4. Publication intensity, trends, and bibliometric analysis (pp. 4–6)

> In terms of the type of study, among those 252 articles, we found only two articles (0.79%) from Santini et al. (2018) and Turkeš et al. (2020) that can be classified as a meta-analysis article.

> Apart from those two articles, the other 238 articles can be classified as an implementation article, in which the ALNS-based heuristic is deployed to solve certain optimization problems

**Why it matters.** The key bibliometric finding: almost all ALNS work is
application, only two are meta-analyses. This is the gap teaspoon’s measurement
study fills for operator value.

### 5.1.1. Structure development (pp. 7–9)

> Our results suggest that only a single article from Chowdhury et al. (2019) investigated an adaptive mechanism to select the destroy and repair operators to be deployed, namely a stochastic universal sampling strategy introduced by Baker (1987).

> Their results concluded that the presence of an adaptive mechanism contributed to 0.14% improvement of objective value and is recommended to be included in several specific situations.

**Why it matters.** Two crucial facts: the adaptive mechanism itself is barely
studied, and the one meta-analysis finds only 0.14% average gain. This is the
empirical basis for teaspoon’s scepticism and for the fixed/uniform competitors.

### 5.1.2. Hybridization (pp. 9–10)

> However, there is still no conclusive result on which metaheuristic framework works best when implemented along with ALNS, as the majority of reviewed articles still implemented ALNS using the original structure proposed in Ropke and Pisinger (2006a; 2006b)

**Why it matters.** Says the hybrid design space is unresolved; teaspoon’s fixed
cascade is one such hybrid, and the lack of a conclusive comparison is a gap.

### 5.2. Overview of ALNS application (pp. 10–12)

> In terms of the number of objective functions, we observed that the majority of studies on ALNS (242 papers, 96.80%) still focused on the implementation of the ALNS-based heuristic to solve a single-objective optimization problem. Contrarily, there were only eight studies (3.20%) from Buer and Kopfer (2014), Rifai et al. (2016), Cota et al. (2019), Ji et al. (2019a), Liao et al. (2020), Liu and Liao (2021), Al Chami et al. (2021), and Rifai et al. (2021) which applied ALNS-based heuristic for MOOPs.

**Why it matters.** Shows the field is overwhelmingly single-objective and
application-driven, so a careful single-objective TSP operator study is still
useful and comparable.

### 6. Suggestions for future research (pp. 13–14)

> From the taxonomical review, we observe that this flexible paradigm leads to a drawback of the presence of overly tuned ALNS-based heuristics with a large number of operators. From practical point of view, this trend reduces the ability of the algorithms to be reproduced

**Why it matters.** Identifies two open problems — too many tuned operators and
too many parameters — that a principled per-state selector could address.

### 7. Concluding remarks (p. 15)

> Our review in the application areas of ALNS (Fig. 5) also has shown that ALNS-based algorithms have been implemented in various domains, which highlight the flexibility of this algorithm. In essence, these facts confirm the establishment of ALNS as a new popular metaheuristic framework.

**Why it matters.** The survey’s bottom line: ALNS is established and flexible,
so its operator-selection layer is a mature target for a time-aware study rather
than a niche method.
