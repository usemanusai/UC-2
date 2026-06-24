# run_entropy_check.py
"""
Structural Entropy and Spectral Graph Complexity Dashboard.
Scans the project, calculates CDE/AICC, computes Graph and Laplacian energy,
and prints Couplings and Refactoring alerts.
"""

import os
import sys
import numpy as np

# Add parent directory to path to support imports if needed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.kernel.math_engine.entropy import StructuralEntropyMonitor

def main():
    print("=" * 70)
    print(" STRUCTURAL ENTROPY & SPECTRAL COMPLEXITY MONITOR")
    print("=" * 70)
    
    root_dir = os.path.dirname(os.path.abspath(__file__))
    monitor = StructuralEntropyMonitor(root_dir)
    
    print(f"[Monitor] Scanning codebase root: {root_dir}")
    try:
        report = monitor.scan_project()
    except Exception as e:
        print(f"[ERROR] Scan failed: {e}")
        sys.exit(1)
        
    if not report:
        print("[Warning] No Python files scanned.")
        sys.exit(0)
        
    print("\n" + "-" * 50)
    print(" CODEBASE COMPLEXITY INDEX")
    print("-" * 50)
    print(f"Total Python Files Scanned : {report['files_count']}")
    print(f"Mean AICC (Information)   : {report['mean_aicc']:.4f}")
    print(f"Mean CDE (Class Entropy)  : {report['mean_cde']:.4f}")
    
    print("\n" + "-" * 50)
    print(" SPECTRAL GRAPH ANALYSIS")
    print("-" * 50)
    print(f"Graph Adjacency Energy     : {report['graph_energy']:.4f}")
    print(f"Laplacian Graph Energy     : {report['laplacian_energy']:.4f}")
    
    print("\n" + "-" * 50)
    print(" HIGHEST COUPLING BETWEEN OBJECTS (CBO)")
    print("-" * 50)
    # Sort files by CBO score (descending)
    sorted_cbo = sorted(report["cbo_scores"].items(), key=lambda x: x[1], reverse=True)
    for path, score in sorted_cbo[:10]:
        print(f"CBO: {score:6.2f} | {path}")
        
    print("\n" + "=" * 70)
    alerts = report.get("refactoring_alerts", [])
    if alerts:
        print(f" WARNING: REFACTORING ALERTS TRIGGERED ({len(alerts)} items)")
        print("=" * 70)
        for alert in alerts:
            print(f"\n[ALERT] File: {alert['file']}")
            print(f"  * Coupling Between Objects (CBO) : {alert['cbo']:.2f} (Threshold > 10.0)")
            print(f"  * Response For Class (RFC)       : {alert['rfc']} (Threshold > 20)")
            print(f"  * Action Recommendation          : {alert['reason']}")
    else:
        print(" SUCCESS: CODEBASE HEALTH OK: No refactoring alerts triggered.")
        print("=" * 70)

if __name__ == "__main__":
    main()
