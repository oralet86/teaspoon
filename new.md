# Neural Algorithm Selection of Large-Scale TSP Solvers and Their Parameters (S1: We need a name here for the algorithm)

The central claim is: Different solvers are more appropriate for different instances.
These instances may differ by: Size, distribution, (S2: We need more search dimensions for instances here), etc.

Our goal is a picker for the possible choices of solvers and their parameters.

The solution here is abstractly as follows:

One mechanism called VR (for vector representator) (S3: Need architecture suggestions here) to convert a graph into a representational vector of the graph of size D. (S4: Need size suggestions here)

A mechanism HO (for hyperparameter optimizer) to act as a that convert the representation into hyperparameters for a specific solver to maximize an objective A. (S5: Need proper objective here.), another mechanism called CE (cost estimator) estimates the cost given a hyperparameter setup and a vector representation (S6: Need a good definition for cost here. S6 and S5 are the same thing basically.)

Assume we have N solvers (like LKH, EAX etc.) and HO+CE mechanisms are trained for each.
Assuming all mechanisms work correctly within reason the only thing left to do is to pick the choice among solvers which minimizes the predicted cost.
