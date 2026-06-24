"""
engine/core/event_bus.py
==========================
Lightweight synchronous publish-subscribe event bus for intra-process
communication between the browser kernel, captcha subsystem, GUI, and
reporting layer.

Design
------
- Zero external dependencies (stdlib only).
- Thread-safe: subscribers and publications both acquire the same RLock.
- Supports wildcard topic subscriptions via ``*`` prefix.
- Delivers events synchronously on the publisher's thread; subscribers must
  NOT block indefinitely.

Usage
-----
    from engine.core.event_bus import EventBus

    bus = EventBus.instance()

    # Subscribe
    bus.subscribe("captcha.solved", lambda event: print(event))

    # Publish
    bus.publish("captcha.solved", {"service": "rektCaptcha", "time": 0.4})

    # Unsubscribe
    bus.unsubscribe("captcha.solved", handler)
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Type alias for subscriber callables
Subscriber = Callable[[Dict[str, Any]], None]


class EventBus:
    """
    Thread-safe synchronous publish-subscribe event bus.

    Instantiate directly or use ``EventBus.instance()`` to share a single
    process-wide bus.
    """

    _global_instance: Optional["EventBus"] = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Map: topic -> list of subscriber callables
        self._subscribers: Dict[str, List[Subscriber]] = {}

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def instance(cls) -> "EventBus":
        """Return the process-wide singleton EventBus."""
        if cls._global_instance is None:
            with cls._instance_lock:
                if cls._global_instance is None:
                    cls._global_instance = cls()
        return cls._global_instance

    # ── Subscription API ──────────────────────────────────────────────────────

    def subscribe(self, topic: str, handler: Subscriber) -> None:
        """
        Register ``handler`` to be called whenever ``topic`` is published.

        Parameters
        ----------
        topic : str
            Event topic string (e.g. ``"captcha.solved"``).
            Use ``"*"`` to subscribe to ALL events.
        handler : callable
            Function accepting a single ``dict`` payload argument.
        """
        with self._lock:
            if topic not in self._subscribers:
                self._subscribers[topic] = []
            if handler not in self._subscribers[topic]:
                self._subscribers[topic].append(handler)
                logger.debug("[EventBus] Subscribed %r to topic %r", handler, topic)

    def unsubscribe(self, topic: str, handler: Subscriber) -> None:
        """Remove ``handler`` from ``topic``.  No-op if not subscribed."""
        with self._lock:
            listeners = self._subscribers.get(topic, [])
            if handler in listeners:
                listeners.remove(handler)
                logger.debug("[EventBus] Unsubscribed %r from topic %r", handler, topic)

    def unsubscribe_all(self, topic: Optional[str] = None) -> None:
        """
        Remove all subscribers.

        If ``topic`` is given, clears only that topic's subscriber list.
        If ``topic`` is ``None``, clears the entire bus.
        """
        with self._lock:
            if topic:
                self._subscribers.pop(topic, None)
            else:
                self._subscribers.clear()

    # ── Publication API ───────────────────────────────────────────────────────

    def publish(self, topic: str, payload: Optional[Dict[str, Any]] = None) -> int:
        """
        Publish an event to ``topic``.

        All registered handlers for the topic (and any ``"*"`` wildcard
        subscribers) are called synchronously in registration order.

        Parameters
        ----------
        topic : str
            Event topic string.
        payload : dict, optional
            Event data.  A ``"_topic"`` key is injected automatically.

        Returns
        -------
        int
            Number of handlers that were called.
        """
        if payload is None:
            payload = {}
        payload = dict(payload)
        payload["_topic"] = topic

        with self._lock:
            handlers: List[Subscriber] = []
            handlers.extend(self._subscribers.get(topic, []))
            handlers.extend(self._subscribers.get("*", []))
            # Deduplicate while preserving order
            seen: set = set()
            unique_handlers: List[Subscriber] = []
            for h in handlers:
                hid = id(h)
                if hid not in seen:
                    seen.add(hid)
                    unique_handlers.append(h)

        called = 0
        for handler in unique_handlers:
            try:
                handler(payload)
                called += 1
            except Exception as exc:
                logger.error(
                    "[EventBus] Handler %r raised on topic %r: %s",
                    handler,
                    topic,
                    exc,
                    exc_info=True,
                )

        return called

    # ── Convenience topics ────────────────────────────────────────────────────
    # Well-known topic constants used across the codebase.

    CAPTCHA_SOLVED   = "captcha.solved"
    CAPTCHA_FAILED   = "captcha.failed"
    SESSION_STARTED  = "session.started"
    SESSION_FINISHED = "session.finished"
    RESULT_VALID     = "result.valid"
    RESULT_INVALID   = "result.invalid"
    RESULT_FREE      = "result.free"
    RESULT_ERROR     = "result.error"
    DISCOVERY_DONE   = "discovery.done"
    DISCOVERY_FAILED = "discovery.failed"
    APP_SHUTDOWN     = "app.shutdown"
