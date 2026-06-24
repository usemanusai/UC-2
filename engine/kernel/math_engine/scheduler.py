# engine/kernel/math_engine/scheduler.py
"""
Earliest Deadline First (EDF) & Stochastic Expected-Utility Maximization Scheduler.
Supports per-domain circuit breakers and LogNormal latency expectation models.
"""

import heapq
import time
import threading
import logging
import numpy as np
from typing import Callable, Any, Tuple, Optional, List, Dict

logger = logging.getLogger(__name__)

class EDFTask:
    """Represents a scheduled task under the EDF paradigm."""
    def __init__(self, deadline: float, name: str, fn: Callable[..., Any], *args, priority: int = 0, **kwargs):
        self.deadline = deadline
        self.name = name
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.priority = priority  # Tie-breaker (lower number runs first if deadlines are equal)
        self.created_at = time.time()

    def __lt__(self, other: 'EDFTask') -> bool:
        # Heap comparison: sort by deadline first, then by priority, then by creation time
        if self.deadline != other.deadline:
            return self.deadline < other.deadline
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.created_at < other.created_at

class EDFScheduler:
    """
    Thread-safe Earliest Deadline First (EDF) scheduler.
    """
    def __init__(self):
        self._heap = []
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._running = False
        self._thread = None

    def schedule(self, relative_deadline: float, name: str, fn: Callable[..., Any], *args, priority: int = 0, **kwargs) -> EDFTask:
        """
        Schedules a task to run before target deadline (current time + relative_deadline).
        """
        deadline = time.time() + relative_deadline
        task = EDFTask(deadline, name, fn, *args, priority=priority, **kwargs)
        with self._lock:
            heapq.heappush(self._heap, task)
            logger.debug(f"[EDFScheduler] Scheduled task '{name}' with deadline in {relative_deadline:.2f}s")
            self._cond.notify_all()
        return task

    def start(self):
        """Starts the background worker thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._worker_loop, name="EDFSchedulerWorker", daemon=True)
            self._thread.start()
            logger.info("[EDFScheduler] Background worker thread started.")

    def stop(self):
        """Stops the scheduler and waits for the worker thread to exit."""
        with self._lock:
            self._running = False
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("[EDFScheduler] Background worker thread stopped.")

    def _worker_loop(self):
        while True:
            task = None
            with self._lock:
                while self._running and not self._heap:
                    self._cond.wait()
                
                if not self._running:
                    break
                    
                # Look at the earliest deadline task
                now = time.time()
                next_task = self._heap[0]
                
                if now >= next_task.deadline:
                    task = heapq.heappop(self._heap)
                else:
                    # Wait until deadline or new task arrival
                    wait_time = next_task.deadline - now
                    self._cond.wait(timeout=wait_time)
                    
                    # Recheck queue
                    if self._heap:
                        next_task = self._heap[0]
                        if time.time() >= next_task.deadline:
                            task = heapq.heappop(self._heap)
            
            if task:
                try:
                    logger.debug(f"[EDFScheduler] Executing task '{task.name}' (deadline delta: {task.deadline - time.time():.2f}s)")
                    task.fn(*task.args, **task.kwargs)
                except Exception as e:
                    logger.error(f"[EDFScheduler] Error executing task '{task.name}': {e}", exc_info=True)


# =========================================================================
# Stochastic Expected-Utility Scheduler (Epic 4)
# =========================================================================

class UtilityTask:
    """Represents a task evaluated under the soft expected-utility framework."""
    def __init__(self, name: str, domain: str, value: float, deadline: float, fn: Callable[..., Any], *args, **kwargs):
        self.name = name
        self.domain = domain
        self.value = value         # v_i value of importance
        self.deadline = deadline   # delta_i absolute deadline
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.created_at = time.time()

    def calculate_expected_utility(self, now: float, mu: float = 0.5, sigma: float = 0.25) -> float:
        """
        Approximates E[U_i(now + T_i)] via Monte Carlo simulation under T_i ~ LogNormal(mu, sigma^2).
        Formula: value * E[ exp( -max(0, (now + T_i - deadline)/deadline ) ) ]
        """
        # Draw 50 samples for expectation approximation
        samples = np.random.lognormal(mean=mu, sigma=sigma, size=50)
        utilities = []
        for t_i in samples:
            arrival_time = now + t_i
            lateness = max(0.0, (arrival_time - self.deadline) / self.deadline) if self.deadline > 0 else 0.0
            utility = np.exp(-lateness)
            utilities.append(utility)
        return float(self.value * np.mean(utilities))

class DomainCircuitBreaker:
    """Domain circuit breaker protecting endpoints against flood & transient failures."""
    def __init__(self, failure_threshold: int = 3, quarantine_duration: float = 300.0):
        self.failure_threshold = failure_threshold
        self.quarantine_duration = quarantine_duration
        self.failures = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF-OPEN
        self.last_state_change = 0.0

    def record_success(self):
        self.failures = 0
        self.state = "CLOSED"

    def record_failure(self):
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.state = "OPEN"
            self.last_state_change = time.time()
            logger.warning(f"[CircuitBreaker] Domain transitioned to OPEN. Quarantine active for {self.quarantine_duration}s.")

    def can_execute(self) -> bool:
        if self.state == "CLOSED":
            return True
        elif self.state == "OPEN":
            if time.time() - self.last_state_change >= self.quarantine_duration:
                self.state = "HALF-OPEN"
                self.last_state_change = time.time()
                logger.info("[CircuitBreaker] Quarantine expired. Transitioning to HALF-OPEN.")
                return True
            return False
        elif self.state == "HALF-OPEN":
            return True
        return False

class StochasticUtilityScheduler:
    """
    Schedules asynchronous tasks prioritizing by dynamic expected-utility maximization.
    Employs per-domain circuit breakers.
    """
    def __init__(self):
        self._tasks: List[UtilityTask] = []
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._running = False
        self._thread = None
        self._circuit_breakers: Dict[str, DomainCircuitBreaker] = {}
        self._domain_mu: Dict[str, float] = {}
        self._domain_sigma: Dict[str, float] = {}

    def get_circuit_breaker(self, domain: str) -> DomainCircuitBreaker:
        if domain not in self._circuit_breakers:
            self._circuit_breakers[domain] = DomainCircuitBreaker()
        return self._circuit_breakers[domain]

    def schedule(self, name: str, domain: str, value: float, relative_deadline: float, fn: Callable[..., Any], *args, **kwargs) -> UtilityTask:
        deadline = time.time() + relative_deadline
        task = UtilityTask(name, domain, value, deadline, fn, *args, **kwargs)
        with self._lock:
            self._tasks.append(task)
            logger.debug(f"[StochasticScheduler] Scheduled task '{name}' for domain '{domain}' (deadline in {relative_deadline:.2f}s)")
            self._cond.notify_all()
        return task

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._worker_loop, name="StochasticSchedulerWorker", daemon=True)
            self._thread.start()
            logger.info("[StochasticScheduler] Stochastic worker thread started.")

    def stop(self):
        with self._lock:
            self._running = False
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("[StochasticScheduler] Stochastic worker thread stopped.")

    def _worker_loop(self):
        while True:
            task = None
            with self._lock:
                while self._running and not self._tasks:
                    self._cond.wait()
                
                if not self._running:
                    break
                    
                now = time.time()
                best_idx = -1
                max_utility = -1e9
                
                for idx, t in enumerate(self._tasks):
                    cb = self.get_circuit_breaker(t.domain)
                    if cb.can_execute():
                        mu = self._domain_mu.get(t.domain, 0.5)
                        sigma = self._domain_sigma.get(t.domain, 0.25)
                        utility = t.calculate_expected_utility(now, mu, sigma)
                        if utility > max_utility:
                            max_utility = utility
                            best_idx = idx
                            
                if best_idx != -1:
                    task = self._tasks.pop(best_idx)
                else:
                    self._cond.wait(timeout=1.0)
                    
            if task:
                cb = self.get_circuit_breaker(task.domain)
                try:
                    start_time = time.time()
                    task.fn(*task.args, **task.kwargs)
                    duration = time.time() - start_time
                    
                    with self._lock:
                        # LogNormal parameters estimation adjustment
                        mu_old = self._domain_mu.get(task.domain, 0.5)
                        log_dur = np.log(max(duration, 0.001))
                        self._domain_mu[task.domain] = 0.9 * mu_old + 0.1 * log_dur
                        self._domain_sigma[task.domain] = 0.25
                        
                    cb.record_success()
                except Exception as e:
                    logger.error(f"[StochasticScheduler] Task '{task.name}' execution failed: {e}", exc_info=True)
                    cb.record_failure()
