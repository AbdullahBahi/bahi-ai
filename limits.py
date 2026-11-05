# -*- coding: utf-8 -*-
"""In-memory sliding-window limits that cap abuse and LLM spend.

Per-browser limits stop one visitor from hammering the advisor; the global limits are the
hard ceiling on what the advisor can cost in a minute and in a day, whatever happens.
(Single process: the app runs one gunicorn worker, see render.yaml.)
"""
import threading, time
from collections import defaultdict, deque

import config

_lock = threading.Lock()
_hits = defaultdict(deque)  # key -> timestamps


def _count(key, window, now):
    q = _hits[key]
    while q and q[0] <= now - window:
        q.popleft()
    return len(q)


def check_and_record(client_key):
    """Returns None if allowed (and records the hit), else a reason string."""
    now = time.time()
    rules = [
        ("global:min", 60, config.GLOBAL_PER_MIN, "busy"),
        ("global:day", 86400, config.GLOBAL_PER_DAY, "busy"),
        (f"c:{client_key}:10m", 600, config.PER_CLIENT_10MIN, "client"),
        (f"c:{client_key}:day", 86400, config.PER_CLIENT_DAY, "client"),
    ]
    with _lock:
        for key, window, limit, reason in rules:
            if _count(key, window, now) >= limit:
                return reason
        for key, *_ in rules:
            _hits[key].append(now)
        if len(_hits) > 20000:  # drop idle per-client keys
            for k in [k for k, q in _hits.items() if not q or q[-1] < now - 86400]:
                del _hits[k]
    return None
