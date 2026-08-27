# -*- coding: utf-8 -*-
"""Shared helpers: title normalization, json load/save, rule-based classifier."""
import re
import json
import os


def norm_title(t):
    """Aggressive normalization so near-identical titles dedupe reliably."""
    if not t:
        return ""
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def classify_by_rules(paper, rules, default):
    """rules = list of (category, [keywords]); first match wins.
    Matches against title + venue (lower-cased)."""
    text = " ".join([
        paper.get("title", "") or "",
        paper.get("venue", "") or "",
    ]).lower()
    for cat, kws in rules:
        for kw in kws:
            if kw.lower() in text:
                return cat
    return default
