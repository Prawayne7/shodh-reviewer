"""Independent ground truth for the dataset.

Hand-written routing policy plus the dense ticket grid used for brute-force
comparison. NEVER imported by ``reviewer/``; it shares no code with it.
"""
from __future__ import annotations

from typing import Iterator

CATEGORIES = [None, "billing", "spam", "other"]
TIERS = ["standard", "vip"]
# Dense: every minute 0..140 plus values around 30 hours (1800 min) so unit
# mistakes (hours read as minutes) are visible.
WAITS = list(range(0, 141)) + [1799, 1800, 1801]


def support_v1(t: dict, wait_threshold: int) -> str:
    """Intended effective policy (written by hand, from the policy text).

    R1 missing category -> manual_review
    R2 otherwise urgent -> priority_queue, unless category == spam (skip R2)
    R3 otherwise wait >= threshold -> escalation, but vip -> priority_queue
    R4 everything else -> normal_queue
    """
    if t["category"] is None:
        return "manual_review"
    if t["urgent"] and t["category"] != "spam":
        return "priority_queue"
    if t["wait_minutes"] >= wait_threshold:
        if t["tier"] == "vip":
            return "priority_queue"
        return "escalation"
    return "normal_queue"


def grid() -> Iterator[dict]:
    for w in WAITS:
        for c in CATEGORIES:
            for u in (False, True):
                for tier in TIERS:
                    yield {"category": c, "urgent": u, "tier": tier, "wait_minutes": w}


def key(t: dict) -> tuple:
    return (t["wait_minutes"], t["urgent"], t["tier"], "" if t["category"] is None else t["category"])
