# engine/kernel/math_engine/langevin.py
"""
Fractional Langevin Equation (fLE) and Fractional Brownian Motion (fBm) Motor Control Engine.
Simulates viscoelastic biological noise, muscle damping, and tissue elasticity, validated
via real-time Bubble Entropy (bEn) feedback loops.
"""

import numpy as np
import scipy.linalg
from typing import Tuple, List

def generate_fbm(N: int, H: float, sigma: float = 1.0) -> np.ndarray:
    """
    Generates a Fractional Brownian Motion (fBm) path of length N using Cholesky decomposition.
    Falls back to eigenvalue decomposition (SVD style) if the covariance matrix is close to singular.
    
    Args:
        N: Number of path steps.
        H: Hurst parameter (0.35 to 0.85).
        sigma: Overall scaling variance factor.
    """
    if N <= 1:
        return np.zeros(N)
        
    t = np.linspace(0.01, 1.0, N)
    # Construct fBm covariance matrix
    # C(t, s) = 0.5 * (t^(2H) + s^(2H) - |t - s|^(2H))
    T_mat, S_mat = np.meshgrid(t, t, indexing='ij')
    C = 0.5 * (T_mat**(2*H) + S_mat**(2*H) - np.abs(T_mat - S_mat)**(2*H))
    
    # Scale covariance matrix
    C = (sigma ** 2) * C
    
    # Add a tiny diagonal regularizer (nugget) for numerical stability
    nugget = 1e-9 * np.eye(N)
    C_reg = C + nugget
    
    try:
        L = np.linalg.cholesky(C_reg)
    except np.linalg.LinAlgError:
        # Fallback to Eigen decomposition for semidefinite matrices
        vals, vecs = np.linalg.eigh(C_reg)
        vals = np.maximum(vals, 0.0)
        L = vecs * np.sqrt(vals)
        
    z = np.random.normal(0.0, 1.0, N)
    path = L.dot(z)
    
    # Force starting at 0
    path = path - path[0]
    return path

def bubble_sort_swaps(arr: np.ndarray) -> int:
    """
    Counts the number of swaps required to sort an array using Bubble Sort.
    """
    arr_copy = arr.copy()
    n = len(arr_copy)
    swaps = 0
    for i in range(n):
        for j in range(0, n - i - 1):
            if arr_copy[j] > arr_copy[j + 1]:
                arr_copy[j], arr_copy[j + 1] = arr_copy[j + 1], arr_copy[j]
                swaps += 1
    return swaps

def compute_bubble_entropy(series: np.ndarray, m: int = 2) -> float:
    """
    Computes the Bubble Entropy of a 1D time series.
    Formula:
        bEn = (H_R(m + 1) - H_R(m)) / log((m + 1) / (m - 1))
    where H_R(d) is the Rényi entropy of order 2 (collision entropy)
    of the bubble sort swap distribution of embedding dimension d.
    """
    N = len(series)
    if N < m + 2:
        return 0.0
        
    def renyi_entropy_order_2(dim: int) -> float:
        # Number of vectors of size dim
        num_vecs = N - dim + 1
        if num_vecs <= 0:
            return 0.0
            
        # Extract embedded vectors
        swaps_list = []
        for i in range(num_vecs):
            vec = series[i : i + dim]
            swaps_list.append(bubble_sort_swaps(vec))
            
        # Count frequencies
        swaps_arr = np.array(swaps_list)
        max_swaps = dim * (dim - 1) // 2
        # Bin frequencies
        counts = np.zeros(max_swaps + 1)
        for val in swaps_arr:
            if 0 <= val <= max_swaps:
                counts[val] += 1
                
        probs = counts / num_vecs
        sum_sq_probs = np.sum(probs ** 2)
        if sum_sq_probs <= 0:
            return 0.0
        return -np.log(sum_sq_probs)

    h_m = renyi_entropy_order_2(m)
    h_m_plus_1 = renyi_entropy_order_2(m + 1)
    
    denom = np.log((m + 1) / (m - 1)) if m > 1 else 1.0
    return (h_m_plus_1 - h_m) / denom

class FLEMotorControl:
    """
    Stochastic cursor trajectory generator solving the discretized Fractional Langevin Equation (fLE)
    and modulating Hurst dynamics with real-time Bubble Entropy calibration.
    """
    def __init__(self, H_range: Tuple[float, float] = (0.35, 0.85)):
        self.H_range = H_range
        
    def generate_path(
        self, 
        start: Tuple[float, float], 
        target: Tuple[float, float], 
        steps: int = 100, 
        dt: float = 0.005,
        mass: float = 1.2, 
        eta: float = 0.4, 
        k_p: float = 2.0, 
        k_d: float = 0.25
    ) -> List[Tuple[float, float]]:
        """
        Generates a 2D cursor trajectory from start to target.
        Integrates fLE with dynamic H modulation and Bubble Entropy calibration loop.
        """
        start_x, start_y = float(start[0]), float(start[1])
        target_x, target_y = float(target[0]), float(target[1])
        
        # Calculate dynamic Hurst parameter based on target distance
        dist = np.hypot(target_x - start_x, target_y - start_y)
        # Larger distances -> lower Hurst (more ballistic, higher chaos)
        # Shorter distances -> higher Hurst (more subdiffusive, finer adjustment)
        h_min, h_max = self.H_range
        # Normalize distance to [0, 1] for mapping (arbitrary scale 800px)
        norm_dist = min(dist / 800.0, 1.0)
        H = h_max - norm_dist * (h_max - h_min)
        H = np.clip(H, h_min, h_max)
        
        # Viscoelastic memory kernel power-law decay: alpha = 2 - 2H
        alpha = 2.0 - 2.0 * H
        
        # Expected Bubble Entropy: bEn_expected = H/2 + 3/4
        expected_ben = H / 2.0 + 0.75
        
        # Initial noise floor
        noise_scale = 15.0
        max_retries = 3
        
        for retry in range(max_retries):
            # Generate fBm paths for noise
            fbm_x = generate_fbm(steps, H, sigma=1.0)
            fbm_y = generate_fbm(steps, H, sigma=1.0)
            
            # Compute fractional Gaussian noise (derivatives of fBm)
            fgn_x = np.diff(fbm_x) / dt
            fgn_y = np.diff(fbm_y) / dt
            # Pad to match steps
            fgn_x = np.append(fgn_x, fgn_x[-1])
            fgn_y = np.append(fgn_y, fgn_y[-1])
            
            # Initialize kinematics
            x = np.zeros(steps)
            y = np.zeros(steps)
            v_x = np.zeros(steps)
            v_y = np.zeros(steps)
            
            x[0], y[0] = start_x, start_y
            
            # Solve discrete fLE with memory kernel
            for k in range(steps - 1):
                # Viscoelastic memory sum: damping is history dependent
                damping_x = 0.0
                damping_y = 0.0
                for j in range(k + 1):
                    # Kernel gamma_d = eta * (d + 1)^(-alpha)
                    gamma_j = eta * ((k - j + 1) ** (-alpha))
                    damping_x += gamma_j * v_x[j]
                    damping_y += gamma_j * v_y[j]
                
                # Active control force (Proportional-Derivative control towards target)
                force_x = k_p * (target_x - x[k]) - k_d * v_x[k]
                force_y = k_p * (target_y - y[k]) - k_d * v_y[k]
                
                # Velocity update
                v_x[k + 1] = v_x[k] + (dt / mass) * (-damping_x + force_x + noise_scale * fgn_x[k])
                v_y[k + 1] = v_y[k] + (dt / mass) * (-damping_y + force_y + noise_scale * fgn_y[k])
                
                # Position update
                x[k + 1] = x[k] + dt * v_x[k]
                y[k + 1] = y[k] + dt * v_y[k]
            
            # Compute velocities and speeds for Bubble Entropy analysis
            speeds = np.hypot(v_x, v_y)
            actual_ben = compute_bubble_entropy(speeds, m=2)
            
            # Check deviation from expected Bubble Entropy
            deviation = np.abs(actual_ben - expected_ben)
            if deviation <= 0.05:
                # Valid trajectory found
                break
            else:
                # Recalibrate noise scale based on entropy discrepancy
                # If actual entropy is too low, increase noise; if too high, decrease noise.
                scale_ratio = expected_ben / max(actual_ben, 1e-4)
                # Bound scaling factor to prevent instability
                scale_ratio = np.clip(scale_ratio, 0.5, 2.0)
                noise_scale *= scale_ratio
                
        # Format trajectory as a list of coordinate pairs
        trajectory = []
        for i in range(steps):
            trajectory.append((float(x[i]), float(y[i])))
            
        return trajectory
