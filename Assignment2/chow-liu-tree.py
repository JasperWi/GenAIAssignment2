from scipy.sparse.csgraph import minimum_spanning_tree, breadth_first_order
from scipy.special import logsumexp
import numpy as np
import itertools
import csv
# imports for plotting the tree
import networkx as nx
import matplotlib.pyplot as plt
#imports for measuring the run time
import time 

class BinaryCLT:

    def __init__(self, data, root: int = None, alpha: float = 0.01):
        """
        Initialize and learn the Chow-Liu Tree structure and parameters.
        """
        self.data = data
        self.alpha = alpha
        self.n, self.d = data.shape
        self.root = np.random.randint(self.d) if root is None else root
        self.mst = None
        self.tree = None
        self.mi = None
        self.log_params = None

    def _learn_parameters(self):
        self.log_params = []
        for i in range(self.d):
            if i == self.root:
                counts = np.zeros((2,))
                for j in range(self.n):
                    xi = int(self.data[j, i])
                    counts[xi] += 1
                counts += 2 * self.alpha
                probs = counts / counts.sum()
                log_prob = np.log(probs)
                log_prob = np.tile(log_prob[None, :], (2, 1))  # (2,2)
            else:
                parent = self.tree[i]
                counts = np.zeros((2, 2))
                for j in range(self.n):
                    pi = int(self.data[j, parent])
                    xi = int(self.data[j, i])
                    counts[pi, xi] += 1
                counts += self.alpha
                log_prob = np.log(counts / counts.sum(axis=1, keepdims=True))
            self.log_params.append(log_prob)
        return self.log_params


    def _pairwise_mi(self):
        mi = np.zeros((self.d, self.d))

        # combinations gives us all the possible combinations between two variables ([0,0], [0,1], [1,0], [1,1])
        for i, j in itertools.combinations(range(self.d), 2):
            # gives us a 2x2 table, which will store the the number of occurences each variable combination has
            nij = np.zeros((2, 2), dtype=float) + self.alpha
            # take xi and xj columns from the input data
            xi, xj = self.data[:, i], self.data[:, j]

            # counting occurences
            nij[0, 0] += np.sum((xi == 0) & (xj == 0))
            nij[0, 1] += np.sum((xi == 0) & (xj == 1))
            nij[1, 0] += np.sum((xi == 1) & (xj == 0))
            nij[1, 1] += np.sum((xi == 1) & (xj == 1))

            # convertion from occurences to probabilities
            pij = nij / (self.n + 4 * self.alpha)
            pi  = pij.sum(axis=1, keepdims=True)
            pj  = pij.sum(axis=0, keepdims=True)

            # using the formula
            mi_ij = (pij * (np.log(pij) - np.log(pi) - np.log(pj))).sum()
            mi[i, j] = mi[j, i] = mi_ij

        self.mi = mi
    

    def _run_search(self):
        # From assignment description: Note that minimum spanning tree(-M) = maximum spanning tree(M), where M is a matrix
        mi_neg = -self.mi
        self.mst = minimum_spanning_tree(mi_neg)

        # https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.csgraph.breadth_first_order.html
        _, parents = breadth_first_order(
            self.mst, i_start=self.root, directed=False, return_predecessors=True
        )
        # there is no parent of the original node, thus -1
        parents[self.root] = -1
        self.tree = parents


    def get_tree(self):
        """
        Return the list of parents for each variable in the tree based on the MST
        """
        self._pairwise_mi()
        self._run_search()

        return self.tree
    

    def get_log_params(self):
        """
        Efficiently return the learned log CPTs as a (d, 2, 2) NumPy array.
        Assumes _learn_parameters has already populated self.log_params.
        """
        self._learn_parameters()

        return np.stack(self.log_params, axis=0)

    def log_prob(self, x, exhaustive: bool = False):
        """
        Compute the log-probability of observed or partially observed queries.

        Parameters:
            x (np.ndarray): N x D matrix of queries. Each row represents a sample.
                            Observed values are 0 or 1; missing values are np.nan.
            exhaustive (bool): If True, perform exhaustive enumeration over missing values.
                            If False, use efficient variable elimination.

        Returns:
            np.ndarray: N x 1 array of log-probabilities, one for each query.
        """
        lp = []

        for query in x:
            if exhaustive:
                # === Exhaustive Inference ===
                # Identify indices of missing variables
                missing_indices = np.where(np.isnan(query))[0]
                log_probs = []

                # Iterate over all 2^k completions for k missing variables
                for values in itertools.product([0, 1], repeat=len(missing_indices)):
                    filled = query.copy()
                    filled[missing_indices] = values

                    # Compute joint log-probability
                    logp = 0.0
                    for i in range(self.d):
                        xi = int(filled[i])
                        parent = self.tree[i]
                        if parent == -1:
                            logp += self.log_params[i][0, xi]  # Root node: unconditional
                        else:
                            pi = int(filled[parent])
                            logp += self.log_params[i][pi, xi]  # Child node: conditional

                    log_probs.append(logp)

                # Marginal log-probability via log-sum-exp over completions
                lp.append(logsumexp(log_probs))

            else:
                # === Efficient Inference via Variable Elimination ===

                # Identify observed and missing variable indices
                observed_vars = np.where(~np.isnan(query))[0]
                missing_vars = np.where(np.isnan(query))[0]

                # Case 1: Fully observed query (so nan values)
                if len(missing_vars) == 0:
                    logp = 0.0
                    for i in range(self.d):
                        xi = int(query[i])
                        parent = self.tree[i]
                        if parent == -1:
                            logp += self.log_params[i][0, xi]
                        else:
                            pi = int(query[parent])
                            logp += self.log_params[i][pi, xi]
                    lp.append(logp)
                    continue

                # Case 2: Partial observation (nan values are present)
                factors = {}
                for i in range(self.d):
                    parent = self.tree[i]

                    if i in observed_vars:
                        xi = int(query[i])

                        if parent == -1:
                            # Root node — 
                            factors[i] = np.array([self.log_params[i][0, xi]])
                        else:
                            if parent in observed_vars:
                                # Both child and parent observed → single entry
                                pi = int(query[parent])
                                factors[i] = np.array([self.log_params[i][pi, xi]])
                            else:
                                # Child observed, parent missing → keep full CPT row
                                factors[i] = self.log_params[i][:, xi]
                    else:
                        if parent == -1:
                            # Missing root variable → keep full marginal (1 row)
                            factors[i] = self.log_params[i][0, :]
                        else:
                            if parent in observed_vars:
                                # Child missing, parent observed → keep row
                                pi = int(query[parent])
                                factors[i] = self.log_params[i][pi, :]
                            else:
                                # Both parent and child missing → keep full CPT
                                factors[i] = self.log_params[i]

                # Eliminate missing variables from leaves up to root
                elimination_order = get_post_order(self.tree, self.root)
                for var in elimination_order:
                    if var in missing_vars:
                        parent = self.tree[var]

                        if parent == -1:
                            # Root node marginalization
                            del factors[var]
                        else:
                            if parent in factors:
                                combined = factors[parent] + logsumexp(factors[var], axis=-1)
                                factors[parent] = combined
                            del factors[var]

                # Multiply remaining factors (observed or partially reduced)
                logp = 0.0
                for var in factors:
                    val = factors[var]
                    if isinstance(val, np.ndarray):
                        logp += np.sum(val) if val.ndim > 0 else float(val)
                    else:
                        logp += float(val)

                lp.append(logp)

        lp = np.array(lp).reshape(-1, 1) # Return shape: (N, 1)
        return lp
    
    def sample(self, n_samples: int):
        """
        Generate i.i.d. samples from the CLT distribution using ancestral sampling.
        """

        samples = []
        for i in range(n_samples):
            sample = -1 * np.ones(self.d, dtype=int)
            sample[self.root] = 1 if np.random.rand() < np.exp(self.log_params[self.root][1][1]) else 0
            num_variables_set = 1
            while num_variables_set < self.d:
                for j in range(self.d):
                    parent = self.tree[j]
                    if j != self.root and sample[j] == -1 and sample[parent] != -1:
                        # Sample from the conditional distribution
                        prob = np.exp(self.log_params[j][sample[parent]][1])
                        sample[j] = 1 if np.random.rand() < prob else 0
                        num_variables_set += 1
            samples.append(sample)

        return np.array(samples)

# === Utility for loading datasets ===
def load_csv_dataset(filename):
    with open(filename, "r") as file:
        reader = csv.reader(file, delimiter=',')
        dataset = np.array(list(reader)).astype(np.float64)
    return dataset

# === Required for the log_prob method === 
def get_post_order(tree, root):
    """
    Return post-order traversal of the tree rooted at `root`
    """
    children = {i: [] for i in range(len(tree))}
    for child, parent in enumerate(tree):
        if parent != -1:
            children[parent].append(child)

    order = []
    visited = set()

    def dfs(node):
        for child in children[node]:
            dfs(child)
        order.append(node)

    dfs(root)
    return order


# === Question 2e 1st ===
def plot_tree(tree):
    """
    Plot a Chow-Liu tree given a parent list.
    """
    G = nx.DiGraph()
    for child, parent in enumerate(tree):
        if parent != -1:
            G.add_edge(parent, child)

    # Improved layout (hierarchical / shell)
    pos = nx.shell_layout(G)

    plt.figure(figsize=(10, 6))
    nx.draw(
        G, pos, with_labels=True, arrows=True,
        node_size=800, node_color='lightblue',
        edge_color='black', font_size=12, font_weight='bold'
    )
    plt.title("Chow-Liu Tree Structure", fontsize=14)
    plt.axis('off')
    plt.tight_layout()
    plt.show()

# question 2e 1st
def predecessors(tree):
    print("Predecessors  of each node:")
    for child, parent in enumerate(tree):
        if parent == -1:
            print(f"Node {child}: ROOT (no predecessor)")
        else:
            print(f"Node {child}: Parent = {parent}")

#questing 2e 3rd
def compute_avg_log_likelihood(model, data, exhaustive=False):
    """Compute the average log-likelihood over a dataset."""
    log_likelihoods = model.log_prob(data, exhaustive=exhaustive)
    return np.mean(log_likelihoods)

# question 2e 4th and 5th
def compare_marginal_inference_and_run_time(model, marginals, accidents):
    """Compare exhaustive vs efficient inference on marginal queries and Runtime"""
    #start the time
    start = time.time()
    logp_exhaustive = model.log_prob(marginals, exhaustive=True)
    #end the time
    t_exhaustive = time.time() - start

    #start time
    start = time.time()
    logp_efficient = model.log_prob(marginals, exhaustive=False)
    #end time
    t_efficient = time.time() - start

    # Check consistency
    match_nltcs = np.allclose(logp_exhaustive, logp_efficient)

    #accidents
    start = time.time()
    logp_accidents_exhaustive = model.log_prob(accidents, exhaustive=True)
    accidents_exhaustive = time.time() - start

    return match_nltcs, logp_exhaustive, logp_efficient, logp_accidents_exhaustive, t_exhaustive, t_efficient, accidents_exhaustive


# questiong 2e 6th
def evaluate_sample_quality(model, n_samples):
    """Evaluate log-likelihood of generated samples and compare to test set."""
    samples = model.sample(n_samples)
    avg_loglik_samples = compute_avg_log_likelihood(model, samples)
    
    return avg_loglik_samples

#load the datasets
nltcs_train_data = load_csv_dataset("nltcs_train.csv")
nltcs_test_data = load_csv_dataset("nltcs_test.csv")
nltcs_marginals_data = load_csv_dataset("nltcs_marginals.csv")
accidents_data = load_csv_dataset("accidents.train.csv")

#load the CLT
model_nltcs = BinaryCLT(nltcs_train_data, root=0, alpha=0.01)

def append_section_to_csv(filename, section_title, data, headers=None):
    """
    Append a titled section to a CSV file.

    Parameters:
        filename: path to output CSV
        section_title: string header for the section (e.g., "Question 2e.1: Tree Structure")
        data: list or np.array of rows
        headers: optional list of column names
    """
    with open(filename, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([section_title])
        if headers:
            writer.writerow(headers)
        if isinstance(data, np.ndarray):
            data = data.tolist()
        for row in data:
            if isinstance(row, (float, int)):
                writer.writerow([row])
            else:
                writer.writerow(row)
        writer.writerow([])  # blank row for separation


#save the results to a csv file
output_file = "results_question_2e.csv"

# 2e.1 — Tree structure
tree_structure = model_nltcs.get_tree()
tree_data = [(i, parent) for i, parent in enumerate(tree_structure)]
append_section_to_csv(output_file, "Question 2e.1 — Tree Structure (Node, Parent)", tree_data, headers=["Node", "Parent"])

# 2e.2 — Log CPTs
log_cpts = model_nltcs.get_log_params().reshape(model_nltcs.d, -1)
append_section_to_csv(output_file, "Question 2e.2 — Log CPTs (Flattened)", log_cpts,  headers=["log P(0|0)", "log P(1|0)", "log P(0|1)", "log P(1|1)"])

# 2e.2 —  CPTs
log_cpts = model_nltcs.get_log_params().reshape(model_nltcs.d, -1)
append_section_to_csv(output_file, "Question 2e.2 — CPTs (Flattened)", np.exp(log_cpts),  headers=["P(0|0)", "P(1|0)", "P(0|1)", "P(1|1)"])

# 2e.3 — Train/Test Avg Log-Likelihoods
train_ll = compute_avg_log_likelihood(model_nltcs, nltcs_train_data, exhaustive=False)
test_ll = compute_avg_log_likelihood(model_nltcs, nltcs_test_data, exhaustive=False)
likelihoods_data = [["Train", train_ll], ["Test", test_ll]]
append_section_to_csv(output_file, "Question 2e.3 — Avg Log-Likelihoods", likelihoods_data, headers=["Split", "Avg Log-Likelihood"])

# 2e.4 + 2e.5 — Inference Comparison
marginal_results, logp_exhaustive, logp_efficient, logp_accidents_exhaustive, t_exh, t_eff, accidents_runtime = compare_marginal_inference_and_run_time(model_nltcs, nltcs_marginals_data, accidents_data)
comparison_data = [
    ["Match (Exhaustive vs Efficient)", marginal_results],
    ["Exhaustive Result", logp_exhaustive],
    ["Efficient Result", logp_efficient],
    ["Runtime (Exhaustive)", t_exh],
    ["Runtime (Efficient)", t_eff],
    ["Accidents Exhaustive Result", logp_accidents_exhaustive],
    ["Accidents Run Time", accidents_runtime],
]
append_section_to_csv(output_file, "Question 2e.4/5 — Marginal Inference Comparison & Runtimes", comparison_data)

# 2e.6 — Sampled Data Likelihood
sample_ll = evaluate_sample_quality(model_nltcs, n_samples=1000)
append_section_to_csv(output_file, "Question 2e.6 — Avg Log-Likelihood of 1000 Samples", [["Sampled", sample_ll]], headers=["Source", "Avg Log-Likelihood"])
