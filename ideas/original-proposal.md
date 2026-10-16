# Original proposal: research questions and protocol

Written by hand. Where it differs from the claims, the claims apply; its
experimental protocol (runs per instance, metrics, comparison arms) is in
force.

```text
Title: Adaptive Operator Selection for Genetic Algorithms on the Traveling Salesman Problem

Research Questions:
-Can a Genetic Algorithm improve its performance on TSP by dynamically selecting between different
mutation/local-search operators based on their recent effectiveness?

-Does adaptive operator selection improve TSP solution quality compared with fixed operator strategies?

-Does it improve performance consistently across different TSP instances?

-Does the mechanism learn different operator preferences for different problem characteristics?

-How sensitive is the method to the learning rate and selection strategy?

This will be a simple, reproducible adaptive operator-selection mechanism, accompanied by a systematic
empirical evaluation and analysis of when and why it is effective.

Instead of using a fixed mutation operator, develop an adaptive operator selection (AOS) mechanism:

Genetic Algorithm -> Select operator -> Swap, invesion or 2-opt -> evaluate offspring -> update operator
effectiveness -> select next operator etc.

The algorithm learns during the run which operator appears to be producing useful improvements.

The algorithm dynamically chooses among:
swap, inversion, insertion, 2-opt.. (this is list non-exhaustive,
we should add any new operations that pop up in literature search.)

based on their observed contributions.

The interesting question becomes:
Does adaptive selection outperform fixed operator configurations across different TSP characteristics?

Reward-based operator selection:
For each operator, maintain a score:
where: estimated usefulness of operator, learning rate, reward obtained from using the operator.

For example:
select operators using something simple such as: probability matching, or greedy selection.
Use established TSPLIB instances rather than generating only artificial problems.

For example: 20–50 cities, 50–100 cities, 100–200 cities, larger instances if computational resources permit
For every instance: 30 independent runs, identical computational budget, identical population size, identical
termination condition, different random seeds

Compare:
Algorithm Operator strategy
GA-SWAP Fixed swap
GA-INV Fixed inversion
GA-2OPT Fixed 2-opt
GA-RANDOM Random operator
GA-AOS Adaptive selection

The random-operator GA is particularly useful because it helps determine whether any benefit comes from
adaptation rather than merely having multiple operators available.

Metrics
Record: Best tour length, Mean tour length, Median tour length, Standard deviation, Relative error from the
known optimum/best-known solution, Runtime, Number of objective evaluations, Convergence curve

This is the first milestone. Our first goal is to set up the infrastructure to run this experiment.

Another particularly interesting experiment:
Add TSP landscape characteristics. Divide instances into categories based on their structure, for example:
Euclidean vs non-Euclidean, small vs large, clustered vs dispersed, relatively easy vs difficult
Then investigate:

Does adaptive operator selection learn different operator preferences for different TSP landscapes?
Plot operator usage over time.

answer why the proposed mechanism works;

apply following experiments:
A. No adaptation
Fixed operator.
B. Random selection
Choose operators randomly.
C. Adaptive selection
Use the proposed reward mechanism.
D. Different learning rates

This marks the second milestone. If the experiment proves fruitful,
we can then perhaps look into improving state-of-the-art solvers like LKH.

If we can improve one of the SOTA solvers using AOS, 
and furthermore, explain the internal dynamics that allow the improvement
that would be an incredible contribution, and would likely get accepted as a paper.
```
