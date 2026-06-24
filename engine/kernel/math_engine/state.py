# engine/kernel/math_engine/state.py
"""
Lock-Free Distributed State Management.
Implements optimistic concurrency using SQLite WAL + BEGIN CONCURRENT,
Vector Clocks for logical causal ordering, and M/G/1 queueing theory self-throttling.
"""

import sqlite3
import time
import numpy as np
from typing import Dict, Any, Tuple, Optional, Callable

class VectorClock:
    """
    Vector Clock for managing distributed causal ordering.
    Clock representation: D(<S1, v1>, <S2, v2>, ...)
    """
    def __init__(self, node_id: str, clock_dict: Optional[Dict[str, int]] = None):
        self.node_id = node_id
        self.clock = dict(clock_dict) if clock_dict else {node_id: 0}

    def increment(self) -> None:
        """Increments the local logical clock counter."""
        self.clock[self.node_id] = self.clock.get(self.node_id, 0) + 1

    def clone(self) -> 'VectorClock':
        """Returns a copy of this Vector Clock."""
        return VectorClock(self.node_id, self.clock)

    def update(self, other_clock: Dict[str, int]) -> None:
        """Merges another vector clock by taking the element-wise maximum."""
        for nid, val in other_clock.items():
            self.clock[nid] = max(self.clock.get(nid, 0), val)
        # Ensure our node is present
        self.clock[self.node_id] = self.clock.get(self.node_id, 0)

    def serialize(self) -> Dict[str, int]:
        """Serializes the clock to a dictionary."""
        return dict(self.clock)

    @staticmethod
    def compare(clock_a: Dict[str, int], clock_b: Dict[str, int]) -> str:
        """
        Compares two vector clocks.
        Returns:
            'A_BEFORE_B', 'B_BEFORE_A', 'EQUAL', or 'CONCURRENT'
        """
        all_keys = set(clock_a.keys()).union(clock_b.keys())
        
        a_less_or_equal = True
        b_less_or_equal = True
        
        for k in all_keys:
            v_a = clock_a.get(k, 0)
            v_b = clock_b.get(k, 0)
            if v_a > v_b:
                a_less_or_equal = False
            if v_b > v_a:
                b_less_or_equal = False
                
        if a_less_or_equal and b_less_or_equal:
            return 'EQUAL'
        if a_less_or_equal:
            return 'A_BEFORE_B'
        if b_less_or_equal:
            return 'B_BEFORE_A'
        return 'CONCURRENT'

class MG1QueueMonitor:
    """
    M/G/1 Queueing theory controller to prevent concurrency latch-up.
    Optimizes service rate (mu) vs arrival rate (lambda).
    """
    def __init__(self, target_utilization: float = 0.80):
        self.target_utilization = target_utilization
        self.arrival_times = []
        self.service_times = []
        self.window_size = 50

    def record_arrival(self) -> None:
        """Logs a transaction arrival timestamp."""
        self.arrival_times.append(time.time())
        if len(self.arrival_times) > self.window_size:
            self.arrival_times.pop(0)

    def record_service(self, duration: float) -> None:
        """Logs the execution duration of a database transaction."""
        self.service_times.append(duration)
        if len(self.service_times) > self.window_size:
            self.service_times.pop(0)

    def calculate_throttle_delay(self) -> float:
        """
        Computes the self-throttling backoff time using Pollaczek-Khinchine formula.
        If utilization rho = lambda / mu >= target_utilization, back off to avoid queue blowup.
        """
        if len(self.arrival_times) < 5 or len(self.service_times) < 5:
            return 0.0
            
        # Compute arrival rate lambda (requests / second)
        dt_arrival = self.arrival_times[-1] - self.arrival_times[0]
        if dt_arrival <= 0:
            return 0.0
        lam = (len(self.arrival_times) - 1) / dt_arrival
        
        # Compute service rate mu (1 / mean_duration)
        mean_service = np.mean(self.service_times)
        if mean_service <= 0:
            return 0.0
        mu = 1.0 / mean_service
        
        # Utilization rho
        rho = lam / mu
        
        if rho >= self.target_utilization:
            # Server is overloaded (near or above threshold)
            # Add artificial delay to reduce arrival rate lambda
            # Target delay scales with overflow gap
            overflow = rho - self.target_utilization
            # Add delay proportional to the mean service time scaled by overflow factor
            delay = mean_service * (overflow / max(1.0 - rho, 0.01))
            return min(delay, 2.0) # Cap at 2 seconds
            
        return 0.0

class LockFreeStateDB:
    """
    SQLite WAL mode and optimistic page-locking database coordinator.
    Uses BEGIN CONCURRENT transactions to minimize locks.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.queue_monitor = MG1QueueMonitor()
        self._initialize_db()

    def _initialize_db(self) -> None:
        """Initializes database tables and enables WAL mode."""
        conn = sqlite3.connect(self.db_path)
        try:
            # Enable Write-Ahead Logging (WAL) for lock-free reads and concurrent writes
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            
            # Setup clock & state synchronization tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS state_registry (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    clock_json TEXT,
                    last_node TEXT,
                    updated_at REAL
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def run_concurrent_write(self, write_fn: Callable[[sqlite3.Connection], Any], max_retries: int = 6) -> Any:
        """
        Executes a transaction using BEGIN CONCURRENT, retrying with exponential backoff on collision.
        Applies M/G/1 queue self-throttling.
        """
        # Record arrival & calculate throttling
        self.queue_monitor.record_arrival()
        delay = self.queue_monitor.calculate_throttle_delay()
        if delay > 0:
            time.sleep(delay)
            
        t_start = time.time()
        for attempt in range(max_retries):
            # Connect with deferred isolation to allow manual transaction scoping
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.isolation_level = None
            try:
                # Try BEGIN CONCURRENT (if supported by sqlite3 build, falls back to normal transaction)
                try:
                    conn.execute("BEGIN CONCURRENT;")
                except sqlite3.OperationalError:
                    # Fallback to standard deferred transaction
                    conn.execute("BEGIN;")
                    
                result = write_fn(conn)
                conn.execute("COMMIT;")
                
                # Record successful transaction duration
                self.queue_monitor.record_service(time.time() - t_start)
                return result
            except sqlite3.OperationalError as e:
                # Rollback and back off if database is busy/locked
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    try:
                        conn.execute("ROLLBACK;")
                    except sqlite3.OperationalError:
                        pass
                    # Exponential backoff with jitter
                    sleep_time = (0.005 * (2 ** attempt)) + np.random.uniform(0, 0.01)
                    time.sleep(sleep_time)
                else:
                    raise e
            finally:
                conn.close()
                
        raise sqlite3.OperationalError("BEGIN CONCURRENT transaction failed: max retries exceeded.")
