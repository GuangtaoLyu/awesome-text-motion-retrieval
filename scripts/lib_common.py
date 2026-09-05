# -*- coding: utf-8 -*-
"""Shared helpers: title normalization, json load/save, rule-based classifier."""
import re
import json
import os


_LEADING_ARTICLE = re.compile(r"^(a|an|the)\s+")


def norm_title(t):
    """Aggressive normalization so near-identical titles dedupe reliably."""
    if not t:
        return ""
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def title_key(t):
    """Canonical dedup key for a title.

    Like norm_title() but ALSO strips a leading article, so
    "A semantic belief-state world model ..." and
    "Semantic Belief-State World Model ..." collapse to one key.
    """
    return _LEADING_ARTICLE.sub("", norm_title(t)).strip()


def arxiv_key(p):
    """Normalized arXiv id (version suffix removed), or '' if none.

    Recognises all the shapes the harvesters produce:
      arxiv_id field      -> "2501.06035"
      url .../abs/2501.06035
      url .../pdf/2501.06035
      DOI 10.48550/arXiv.2501.06035   (Crossref's form for arXiv preprints)
    """
    a = (p.get("arxiv_id") or "").strip().lower()
    if not a:
        u = p.get("url") or ""
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]+\.[0-9]+)", u)
        if not m:
            m = re.search(r"10\.48550/arxiv\.([0-9]+\.[0-9]+)", u, re.I)
        if m:
            a = m.group(1)
    return re.sub(r"v\d+$", "", a)


def arxiv_year(p):
    """Submission year encoded in an arXiv id's YYMM prefix, or None.

    New-style ids are YYMM.NNNNN -> "2005.05751" was submitted 2020-05.
    """
    a = arxiv_key(p)
    m = re.match(r"^(\d{2})(\d{2})\.", a)
    if not m:
        return None
    yy, mm = int(m.group(1)), int(m.group(2))
    if not 1 <= mm <= 12:
        return None
    return 1900 + yy if yy >= 91 else 2000 + yy


def fix_year(p):
    """Repair a year that was scraped out of an arXiv id.

    arXiv ids are YYMM.NNNNN, so "2005.05751" means 2020-05 -- NOT the year
    2005. `seed_from_readme` used to run its inline-year regex over the whole
    markdown line, including the arXiv badge URL, and stored that "2005".
    Any implausibly old year on a paper that HAS an arXiv id is this bug, so
    re-derive the year from the id.
    """
    y = p.get("year")
    ay = arxiv_year(p)
    if ay is None:
        return p
    if not isinstance(y, int) or y < 2007:
        p = dict(p)
        p["year"] = ay
    return p


def tidy_title(t):
    """Drop the trailing full stop DBLP appends to every title."""
    if not t:
        return t
    return re.sub(r"\s*[.]\s*$", "", t.strip()).strip()


def dedup_keys(p):
    """Every canonical key for a paper. Two papers are the SAME if any key matches.

    NOTE: the url must NEVER be part of a dedup key. Multi-source harvesting
    (arXiv + DBLP + Semantic Scholar + Crossref) returns one paper several
    times with different links; keying on the link made each variant look new,
    which is how the 2026-08-31 auto-update added ~140 duplicate entries.
    """
    ks = set()
    tk = title_key(p.get("title", ""))
    if tk:
        ks.add("t:" + tk)
    ak = arxiv_key(p)
    if ak:
        ks.add("a:" + ak)
    return ks


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
