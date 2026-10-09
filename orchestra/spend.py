"""Spend over the run: a running total from every API call, the main session and its agents apart.

The calls come from orchestra.waste (one row per API message, the latest usage winning when Claude
Code writes the same message twice). With a price table each call is priced by its model; without
one it counts fresh tokens (input, output and cache writes), as the rest of the dashboard does. A
budget's warning and limit are placed at the call that passed them.
"""

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

BINS = 120
WINDOW_S = 300.0        # "pace now" and the most expensive stretch are five-minute windows
MAX_MODELS = 4          # then "other"


def _short(model: str) -> str:
    """A model as the front end shows it: claude-sonnet-5-5-20260101 becomes sonnet-5-5."""
    return re.sub(r"-\d{8}$", "", re.sub(r"^claude-", "", model or "")) or "unknown"


def _value(call: Sequence[Any], table: Any) -> Tuple[float, Optional[str]]:
    """(what the call cost, or its fresh tokens; the model when it has no price)."""
    created, read, fresh = call[2], call[3], call[4]
    output = call[5] if len(call) > 5 else 0
    if table is None:
        return float(fresh + output + created), None
    prices = table.price_for(call[1])
    if prices is None:
        return 0.0, ((call[1] or "(unknown model)") if created or read or fresh or output else None)
    return (fresh * prices["input"] + output * prices["output"] + created * prices["cache_create"]
            + read * prices["cache_read"]) / 1_000_000.0, None


def _peak(events: List[Tuple[float, float, str, str]]) -> Optional[Dict[str, Any]]:
    """The five minutes, starting at a call, that cost the most, and who spent it."""
    best, best_i, total, j = 0.0, -1, 0.0, 0
    for i, (start, _, _, _) in enumerate(events):
        while j < len(events) and events[j][0] < start + WINDOW_S:
            total += events[j][1]
            j += 1
        if total > best:
            best, best_i = total, i
        total -= events[i][1]
    if best_i < 0:
        return None
    start = events[best_i][0]
    who: Dict[Tuple[str, str], float] = {}
    for at, value, owner, label in events[best_i:]:
        if at >= start + WINDOW_S:
            break
        who[(owner, label)] = who.get((owner, label), 0.0) + value
    ranked = sorted(who.items(), key=lambda kv: -kv[1])[:3]
    return {"start": start, "end": start + WINDOW_S, "value": best,
            "who": [{"agent_id": owner, "label": label, "value": v} for (owner, label), v in ranked]}


def summary(sources: List[Tuple[str, str, Any]], table: Any, now: float, live: bool,
            budget: float = 0.0, warn_ratio: float = 0.8, bins: int = BINS) -> Optional[Dict[str, Any]]:
    """`sources` is (agent id, "" for the main session; label; its WasteLog). None when no call
    recorded anything worth counting."""
    events: List[Tuple[float, float, str, str]] = []
    models: Dict[str, List[Tuple[float, float]]] = {}
    unpriced = set()
    for owner, label, log in sources:
        if log is None:
            continue
        for call in log.ordered_calls():
            value, missing = _value(call, table)
            if missing:
                unpriced.add(missing)
            if value > 0:
                events.append((call[0], value, owner, label))
                models.setdefault(_short(call[1]), []).append((call[0], value))
    if not events:
        return None
    events.sort(key=lambda e: e[0])
    start = events[0][0]
    end = max(now, events[-1][0]) if live else events[-1][0]
    end = max(end, start + 1.0)
    step = (end - start) / bins

    def running(points: List[Tuple[float, float]]) -> List[float]:
        per_bin = [0.0] * bins
        for at, value in points:
            per_bin[min(bins - 1, int((at - start) / step))] += value
        out, total = [], 0.0
        for v in per_bin:
            total += v
            out.append(total)
        return out

    ranked = sorted(models.items(), key=lambda kv: -sum(v for _, v in kv[1]))
    by_model = [{"model": name, "values": running(points)} for name, points in ranked[:MAX_MODELS]]
    if len(ranked) > MAX_MODELS:
        rest = [p for _, points in ranked[MAX_MODELS:] for p in points]
        by_model.append({"model": "other", "values": running(rest)})

    money = table is not None
    limits = None
    if money and budget > 0:
        limits = {"limit": budget, "warn_at": budget * warn_ratio, "warn_t": None, "limit_t": None}
        total = 0.0
        for at, value, _, _ in events:
            total += value
            if limits["warn_t"] is None and total >= limits["warn_at"]:
                limits["warn_t"] = at
            if limits["limit_t"] is None and total >= budget:
                limits["limit_t"] = at
                break

    main_total = sum(v for _, v, owner, _ in events if owner == "")
    total_all = sum(v for _, v, _, _ in events)
    return {
        "unit": "money" if money else "tokens",
        "currency": table.currency if money else None,
        "start": start, "end": end,
        "t": [start + step * (i + 1) for i in range(bins - 1)] + [end],
        "main": running([(at, v) for at, v, owner, _ in events if owner == ""]),
        "agents": running([(at, v) for at, v, owner, _ in events if owner != ""]),
        "models": by_model,
        "total": total_all, "main_total": main_total, "agents_total": total_all - main_total,
        "budget": limits,
        "rate_now": (sum(v for at, v, _, _ in events if at > now - WINDOW_S) / (WINDOW_S / 60.0)) if live else None,
        "peak": _peak(events),
        "unpriced_models": sorted(unpriced),
    }
