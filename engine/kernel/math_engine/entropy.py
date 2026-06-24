# engine/kernel/math_engine/entropy.py
"""
Structural Entropy Management System.
Computes Average Information Content Classification (AICC), Class Design Entropy (CDE),
Spectral Graph/Laplacian Energy of dependencies, and 3D Coupling Between Objects (CBO).
"""

import os
import ast
import hashlib
import numpy as np
from typing import Dict, List, Tuple, Any

class DependencyGraphBuilder(ast.NodeVisitor):
    """AST visitor to find file dependencies (imports) and method definitions."""
    def __init__(self, current_file: str, base_dir: str):
        self.current_file = current_file
        self.base_dir = base_dir
        self.imports = []
        self.methods = []
        self.calls = 0

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            self.imports.append(node.module)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.methods.append(node.name)
        self.generic_visit(node)

    def visit_Call(self, node):
        self.calls += 1
        self.generic_visit(node)

def calculate_cde(file_path: str) -> float:
    """
    Computes Class Design Entropy (CDE) based on the size distribution of defined methods.
    CDE = -sum(p_m * log2(p_m)) where p_m is the proportion of lines in method m.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        tree = ast.parse(content)
    except Exception:
        return 0.0

    methods_lines = []
    # Simple calculation of lines in functions
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            start = node.lineno
            # Estimate end line using body nodes
            end = start
            for child in ast.walk(node):
                if hasattr(child, 'lineno'):
                    end = max(end, child.lineno)
            methods_lines.append(end - start + 1)
            
    if not methods_lines:
        return 0.0
        
    total_lines = sum(methods_lines)
    if total_lines == 0:
        return 0.0
        
    probs = np.array(methods_lines) / total_lines
    probs = probs[probs > 0]
    return float(-np.sum(probs * np.log2(probs)))

def calculate_aicc(file_path: str) -> float:
    """
    Computes Average Information Content Classification (AICC) of the raw text in the file.
    Normalized entropy based on character frequency distribution.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return 0.0
        
    if not content:
        return 0.0
        
    # Count character frequencies
    chars, counts = np.unique(list(content), return_counts=True)
    probs = counts / len(content)
    entropy = -np.sum(probs * np.log2(probs))
    
    # Normalize by log2 of file size (AICC indicator)
    return float(entropy / np.log2(len(content) + 1.0))

class StructuralEntropyMonitor:
    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)

    def scan_project(self) -> Dict[str, Any]:
        """Scans python files, builds adjacency matrix and computes metrics."""
        files = []
        for root, _, filenames in os.walk(self.root_dir):
            # Ignore hidden folders and virtual environments
            if any(p in root for p in [".git", ".venv", "node_modules", "__pycache__", "DELETED"]):
                continue
            for f in filenames:
                if f.endswith('.py'):
                    files.append(os.path.join(root, f))
                    
        file_to_idx = {f: i for i, f in enumerate(files)}
        n_files = len(files)
        
        if n_files == 0:
            return {}

        # Parse each file
        dependencies = {f: [] for f in files}
        rfc_dict = {} # Response For Class/Module (methods + external calls)
        cde_dict = {}
        aicc_dict = {}
        
        for f in files:
            aicc_dict[f] = calculate_aicc(f)
            cde_dict[f] = calculate_cde(f)
            
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    tree = ast.parse(fh.read())
                visitor = DependencyGraphBuilder(f, self.root_dir)
                visitor.visit(tree)
                
                rfc_dict[f] = len(visitor.methods) + visitor.calls
                
                # Filter dependencies matching our local project
                for imp in visitor.imports:
                    # Resolve module name to filename if possible
                    # E.g. "engine.kernel.heuristics" -> "engine/kernel/heuristics.py"
                    parts = imp.split('.')
                    potential_rel_path = os.path.join(self.root_dir, *parts)
                    potential_file = potential_rel_path + ".py"
                    if potential_file in file_to_idx:
                        dependencies[f].append(potential_file)
                    else:
                        # Check __init__.py package imports
                        potential_init = os.path.join(potential_rel_path, "__init__.py")
                        if potential_init in file_to_idx:
                            dependencies[f].append(potential_init)
            except Exception:
                rfc_dict[f] = 0

        # Build Adjacency Matrix for component dependencies
        adj = np.zeros((n_files, n_files))
        for f, deps in dependencies.items():
            u = file_to_idx[f]
            for dep in deps:
                v = file_to_idx[dep]
                adj[u, v] = 1.0 # Directed import edge

        # Compute Graph Energy: sum of absolute values of eigenvalues
        # Symmetric version of adjacency for undirected energy approximation
        adj_sym = 0.5 * (adj + adj.T)
        evals_adj = np.linalg.eigvalsh(adj_sym)
        graph_energy = float(np.sum(np.abs(evals_adj)))

        # Compute Laplacian matrix: L = D - A
        degrees = np.sum(adj_sym, axis=0)
        D = np.diag(degrees)
        L = D - adj_sym
        
        evals_lap = np.linalg.eigvalsh(L)
        # Laplacian Energy = sum(|mu_i - 2m/n|)
        n_vertices = n_files
        n_edges = float(np.sum(degrees) / 2.0)
        mean_degree = (2.0 * n_edges) / n_vertices if n_vertices > 0 else 0.0
        laplacian_energy = float(np.sum(np.abs(evals_lap - mean_degree)))

        # 3D CBO Analysis (Strength, Distance, Volatility)
        # We calculate CBO for each file node
        cbo_scores = {}
        refactoring_alerts = []
        
        for f in files:
            u = file_to_idx[f]
            strength = float(np.sum(adj[u, :]) + np.sum(adj[:, u]))
            
            # Distance metric: average directory distance to import targets
            distances = []
            for dep in dependencies[f]:
                # Relative path distance
                f_parts = os.path.relpath(f, self.root_dir).split(os.sep)
                dep_parts = os.path.relpath(dep, self.root_dir).split(os.sep)
                # Find common prefix length
                common = 0
                for p1, p2 in zip(f_parts, dep_parts):
                    if p1 == p2:
                        common += 1
                    else:
                        break
                dist = (len(f_parts) - common) + (len(dep_parts) - common)
                distances.append(dist)
            avg_distance = float(np.mean(distances)) if distances else 0.0
            
            # Volatility (tracked dynamically, default 1.0)
            volatility = 1.0
            
            # CBO 3D score
            cbo_val = strength * (1.0 + avg_distance) * volatility
            cbo_scores[f] = cbo_val
            
            # Check refactoring triggers: high CBO (>10) and high RFC (>20)
            rfc_val = rfc_dict.get(f, 0)
            if cbo_val > 10.0 and rfc_val > 20:
                refactoring_alerts.append({
                    "file": os.path.relpath(f, self.root_dir),
                    "cbo": cbo_val,
                    "rfc": rfc_val,
                    "reason": "High coupling (CBO) and response (RFC) detected. High risk of spaghetti code decay."
                })

        return {
            "files_count": n_files,
            "graph_energy": graph_energy,
            "laplacian_energy": laplacian_energy,
            "mean_aicc": float(np.mean(list(aicc_dict.values()))) if aicc_dict else 0.0,
            "mean_cde": float(np.mean(list(cde_dict.values()))) if cde_dict else 0.0,
            "cbo_scores": {os.path.relpath(k, self.root_dir): v for k, v in cbo_scores.items()},
            "refactoring_alerts": refactoring_alerts
        }

# =========================================================================
# Thermodynamic Entropy & Divergence Calculations (2026 Standards)
# =========================================================================

import json

def calculate_tsallis_entropy(probs: Any, q: float = 2.0) -> float:
    """
    Computes Tsallis entropy of a probability distribution.
    S_q = (1 - sum(p_i^q)) / (q - 1)
    """
    p = np.array(probs, dtype=np.float64)
    p = p[p > 0]
    if len(p) == 0:
        return 0.0
    if abs(q - 1.0) < 1e-9:
        # Falls back to Shannon entropy as limit q -> 1
        return float(-np.sum(p * np.log(p)))
    return float((1.0 - np.sum(p ** q)) / (q - 1.0))

def calculate_renyi_entropy(probs: Any, alpha: float = 2.0) -> float:
    """
    Computes Renyi entropy of a probability distribution.
    H_alpha = log2(sum(p_i^alpha)) / (1 - alpha)
    """
    p = np.array(probs, dtype=np.float64)
    p = p[p > 0]
    if len(p) == 0:
        return 0.0
    if abs(alpha - 1.0) < 1e-9:
        return float(-np.sum(p * np.log2(p)))
    return float(np.log2(np.sum(p ** alpha)) / (1.0 - alpha))

def calculate_kl_divergence(p: Any, q: Any) -> float:
    """
    Computes Kullback-Leibler (KL) divergence between two probability distributions.
    D_KL(P || Q) = sum(p_i * log2(p_i / q_i))
    """
    p = np.array(p, dtype=np.float64).copy()
    q = np.array(q, dtype=np.float64).copy()
    
    # Add small epsilon to avoid divide by zero or log of zero
    eps = 1e-12
    p += eps
    q += eps
    
    p /= np.sum(p)
    q /= np.sum(q)
    
    # Avoid zero division and log of zero
    mask = (p > 0) & (q > 0)
    if not np.any(mask):
        return 0.0
    return float(np.sum(p[mask] * np.log2(p[mask] / q[mask])))

def calculate_js_divergence(p: Any, q: Any) -> float:
    """
    Computes Jensen-Shannon (JS) divergence between two probability distributions.
    D_JS(P || Q) = 0.5 * D_KL(P || M) + 0.5 * D_KL(Q || M) where M = 0.5 * (P + Q)
    """
    p = np.array(p, dtype=np.float64)
    q = np.array(q, dtype=np.float64)
    if np.sum(p) > 0: p /= np.sum(p)
    if np.sum(q) > 0: q /= np.sum(q)
    
    m = 0.5 * (p + q)
    return 0.5 * calculate_kl_divergence(p, m) + 0.5 * calculate_kl_divergence(q, m)

def verify_fingerprint_entropy(features: Dict[str, Any], reference: Dict[str, Any], max_kl_threshold: float = 0.5) -> Tuple[bool, float]:
    """
    Validates synthetic fingerprint parameters against an organic reference profile distribution.
    Calculates KL divergence. If it exceeds target threshold, returns False to trigger re-rotation.
    """
    p_arr = vectorize_profile(features)
    q_arr = vectorize_profile(reference)
    kl_div = calculate_kl_divergence(p_arr, q_arr)
    is_valid = kl_div <= max_kl_threshold
    return is_valid, kl_div

# =========================================================================
# Stratified Reference Sampling & Bayesian Estimation (Epic 1)
# =========================================================================

def vectorize_profile(features: Dict[str, Any], n_bins: int = 50) -> np.ndarray:
    """Converts a feature dictionary to a probability distribution vector using stable hashing."""
    keys = sorted(features.keys())
    vals = []
    for k in keys:
        # Combine key and stringified value to create stable feature value hashes
        hash_val = hashlib.sha256(f"{k}:{features[k]}".encode('utf-8')).hexdigest()
        v = int(hash_val, 16) % 100 + 1
        vals.append(v)
    if not vals:
        return np.ones(n_bins) / n_bins
    # pad or truncate to match n_bins
    if len(vals) < n_bins:
        vals += [1] * (n_bins - len(vals))
    else:
        vals = vals[:n_bins]
    arr = np.array(vals, dtype=np.float64)
    arr /= np.sum(arr)
    return arr

def load_telemetry_distributions() -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Loads reference distributions D1, D2, D3, D4 from registry telemetry_base."""
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        path = os.path.join(base_dir, "engine", "registry", "telemetry_base.json")
        with open(path, "r") as f:
            data = json.load(f)
        d1 = np.array(data["D1"], dtype=np.float64)
        d2 = np.array(data["D2"], dtype=np.float64)
        d3 = np.array(data["D3"], dtype=np.float64)
        d4 = np.array(data["D4"], dtype=np.float64)
        return d1, d2, d3, d4
    except Exception:
        # Fallback to dirichlet-generated profiles of length 50
        np.random.seed(42)
        d1 = np.random.dirichlet(np.ones(50))
        d2 = np.random.dirichlet(np.ones(50))
        d3 = np.random.dirichlet(np.ones(50))
        d4 = np.random.dirichlet(np.ones(50))
        return d1, d2, d3, d4

class DirichletBayesianUpdater:
    """
    Manages Dirichlet priors alpha and calculates weights w_i for stratified sampling:
    R = w1*D1 + w2*D2 + w3*D3 + w4*D4.
    Dynamically down-weights strata that correlate with detection blocks.
    """
    def __init__(self, alpha_prior: List[float] = None):
        if alpha_prior is None:
            # Flat uniform prior representing: D1 (public), D2 (lib), D3 (synthetic), D4 (compromised)
            self.alpha = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float64)
        else:
            self.alpha = np.array(alpha_prior, dtype=np.float64)

    def update_weights(self, blocked_strata: List[int], successful_strata: List[int]) -> np.ndarray:
        """
        Applies Bayesian posterior updating. Strata associated with blocks are penalized (-0.2),
        while successful ones are rewarded (+1.0).
        """
        for s in blocked_strata:
            if 0 <= s < len(self.alpha):
                self.alpha[s] = max(0.01, self.alpha[s] - 0.2)
        for s in successful_strata:
            if 0 <= s < len(self.alpha):
                self.alpha[s] += 1.0
        return self.get_weights()

    def get_weights(self) -> np.ndarray:
        total = np.sum(self.alpha)
        if total <= 0:
            return np.array([0.25, 0.25, 0.25, 0.25])
        return self.alpha / total

def generate_stratified_reference(weights: np.ndarray) -> np.ndarray:
    """Generates the weighted stratified reference distribution R = sum(w_i * D_i)."""
    d1, d2, d3, d4 = load_telemetry_distributions()
    R = weights[0] * d1 + weights[1] * d2 + weights[2] * d3 + weights[3] * d4
    return R / np.sum(R)

def check_two_sided_divergence(p_synthetic: np.ndarray, d4: np.ndarray, r_stratified: np.ndarray, 
                               theta_rejection: float = 0.8, epsilon: float = 0.5) -> Tuple[bool, float, float]:
    """
    Enforces two-sided divergence constraints:
    1. Rejection: D_KL(P_synthetic || D4) > theta_rejection (distanced from compromised nodes)
    2. Resemblance: D_KL(P_synthetic || R_stratified) < epsilon (close to general target distribution)
    """
    kl_d4 = calculate_kl_divergence(p_synthetic, d4)
    kl_r = calculate_kl_divergence(p_synthetic, r_stratified)
    is_valid = (kl_d4 > theta_rejection) and (kl_r < epsilon)
    return is_valid, kl_d4, kl_r

class ThresholdCalibration:
    """
    Dynamically fits a logistic regression curve and computes the optimal decision threshold
    maximizing Youden's J statistic with 95% Confidence Intervals via bootstrap resampling.
    """
    @staticmethod
    def calculate_j_statistic(divergences: np.ndarray, labels: np.ndarray, threshold: float) -> float:
        pred = (divergences >= threshold).astype(int)
        tp = np.sum((pred == 1) & (labels == 1))
        fn = np.sum((pred == 0) & (labels == 1))
        fp = np.sum((pred == 1) & (labels == 0))
        tn = np.sum((pred == 0) & (labels == 0))
        
        tpr = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        return tpr - fpr

    @classmethod
    def fit_logistic_regression(cls, X: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
        """Simple gradient descent logit model fit for 1D divergence data."""
        beta_0, beta_1 = 0.0, 0.0
        lr = 0.1
        epochs = 200
        for _ in range(epochs):
            logits = beta_0 + beta_1 * X
            probs = 1.0 / (1.0 + np.exp(-np.clip(logits, -20.0, 20.0)))
            # Gradients
            db0 = np.mean(probs - y)
            db1 = np.mean((probs - y) * X)
            beta_0 -= lr * db0
            beta_1 -= lr * db1
        return beta_0, beta_1

    @classmethod
    def optimize_threshold(cls, divergences: List[float], labels: List[int], bootstrap_runs: int = 100) -> Tuple[float, Tuple[float, float]]:
        divs = np.array(divergences, dtype=np.float64)
        lbls = np.array(labels, dtype=np.int32)
        if len(divs) == 0:
            return 0.5, (0.4, 0.6)
            
        candidate_thresholds = np.linspace(np.min(divs), np.max(divs), 50)
        
        def find_best_tau(d, l):
            best_tau = 0.5
            best_j = -1.0
            for tau in candidate_thresholds:
                j = cls.calculate_j_statistic(d, l, tau)
                if j > best_j:
                    best_j = j
                    best_tau = tau
            return best_tau

        tau_star = find_best_tau(divs, lbls)
        
        # Bootstrap resampling for CI
        bootstrap_taus = []
        n = len(divs)
        for _ in range(bootstrap_runs):
            indices = np.random.choice(n, size=n, replace=True)
            b_tau = find_best_tau(divs[indices], lbls[indices])
            bootstrap_taus.append(b_tau)
            
        bootstrap_taus = np.array(bootstrap_taus)
        ci_lower = float(np.percentile(bootstrap_taus, 2.5))
        ci_upper = float(np.percentile(bootstrap_taus, 97.5))
        
        # If CI width > 0.2, return conservative fallback
        if (ci_upper - ci_lower) > 0.2:
            return 0.5, (ci_lower, ci_upper)
            
        return float(tau_star), (ci_lower, ci_upper)

def synthesize_behavioral_fingerprint(correlation_matrix: np.ndarray = None) -> Dict[str, Any]:
    """
    Synthesizes a correlated behavioral fingerprint via Gaussian Copula.
    Correlates: TLS extensions, TCP window, HTTP/2 frame sizes, and JS latency.
    """
    import scipy.stats as stats
    n_vars = 4
    if correlation_matrix is None:
        correlation_matrix = np.array([
            [1.0, 0.4, 0.3, -0.2],
            [0.4, 1.0, 0.5, -0.1],
            [0.3, 0.5, 1.0, -0.15],
            [-0.2, -0.1, -0.15, 1.0]
        ])
    
    # Regularize if not positive semi-definite
    try:
        L = np.linalg.cholesky(correlation_matrix)
    except np.linalg.LinAlgError:
        eigvals, eigvecs = np.linalg.eigh(correlation_matrix)
        eigvals = np.maximum(eigvals, 1e-8)
        correlation_matrix = eigvecs @ np.diag(eigvals) @ eigvecs.T
        d = np.sqrt(np.diag(correlation_matrix))
        correlation_matrix = correlation_matrix / np.outer(d, d)
        L = np.linalg.cholesky(correlation_matrix)

    Z_raw = np.random.normal(0.0, 1.0, n_vars)
    Z = L @ Z_raw
    U = stats.norm.cdf(Z)
    
    # Transform marginals
    tls_ext_count = int(np.floor(U[0] * 8) + 8) # Uniform between 8 and 15
    tcp_win_size = int(np.floor(stats.lognorm.ppf(U[1], s=0.05, scale=64240)))
    h2_max_frame = int(np.floor(U[2] * (65536 - 16384)) + 16384)
    js_latency = float(stats.lognorm.ppf(U[3], s=0.4, scale=1.5))
    
    return {
        "tls_extension_count": tls_ext_count,
        "tcp_window_size": tcp_win_size,
        "http2_max_frame_size": h2_max_frame,
        "js_timing_latency_ms": js_latency
    }
