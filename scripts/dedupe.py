# -*- coding: utf-8 -*-
"""Consolidate duplicate papers in papers.json.

Why this exists
---------------
The weekly auto-update harvests from four sources (arXiv, DBLP, Semantic
Scholar, Crossref). One paper therefore arrives several times, e.g.

    "Nonisotropic Gaussian Diffusion ...", Curreli,      arXiv 2501.06035
    "Nonisotropic Gaussian Diffusion ...", C. Curreli,   CVPR 2025 (via S2)

Same work, different author formatting and different link. The old
`pipeline.dedup_key()` appended the url to the key, so these produced
different keys and both were kept.

What this module does
---------------------
1. Groups papers with a union-find over three signals:
     * canonical title key   (case/punctuation/leading-article stripped)
     * normalized arXiv id   (also matches the 10.48550/arXiv.* DOI form)
     * fuzzy title           (jaccard >= 0.85, or containment >= 0.95 when
                              both titles have >= 6 content tokens)
2. Picks the most complete record of each group as the survivor and merges
   missing fields in from the others, so we keep the arXiv link AND the
   proper venue rather than arbitrarily dropping one.
3. Returns the consolidated list plus a removal report.

Run standalone to rewrite data/papers.json:
    python scripts/dedupe.py
"""
import os
import re
import sys
import json
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from lib_common import (  # noqa: E402
    load_json, save_json, title_key, arxiv_key, fix_year, tidy_title,
)

PAPERS = os.path.join(ROOT, "data", "papers.json")

STOP = {
    "a", "an", "the", "for", "of", "and", "or", "to", "via", "with", "on",
    "in", "by", "using", "towards", "toward", "from", "is", "are", "be",
}


def _stem(w):
    """Crude singular/plural fold so 'Network' and 'Networks' unify."""
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def _tokens(title):
    words = "".join(
        c if c.isalnum() or c.isspace() else " " for c in (title or "").lower()
    ).split()
    return {_stem(w) for w in words if w not in STOP and len(w) > 2}


def _surnames(p):
    """Lower-cased last names of a paper's authors, for identity checks."""
    out = set()
    for a in (p.get("authors") or []):
        parts = re.split(r"\s+", (a or "").strip().lower())
        if parts:
            out.add(parts[-1])
    return out


# Containment is a WEAK signal: "A Spatio-temporal Transformer for 3D Human
# Motion Prediction" (Aksan 2020) is a subset of "Toward Realistic 3D Human
# Motion Prediction With a Spatio-Temporal Cross-Transformer Approach"
# (Yu 2023) -- two different papers. So a containment match must ALSO show
# corroborating evidence before we merge.
_MIN_JACCARD_CONTAINED = 0.70
_MAX_YEAR_GAP = 2


def _similar(a, b, pa=None, pb=None):
    """True when two token sets almost certainly denote the same paper.

    `pa` / `pb` are the source records; when supplied they let us require a
    second signal (shared author surname, close year) for the risky
    containment branch.
    """
    if not a or not b:
        return False
    inter = len(a & b)
    union = len(a | b)
    if union and inter / union >= 0.85:
        return True

    # Acronym-prefix case: "STCN: A Spatio-temporal ... Network ..." vs
    # "A Spatio-Temporal ... Network ..." -- the shorter title is contained in
    # the longer one. Require a decent length so "Human Motion Prediction"
    # does NOT swallow "3D Human Motion Prediction: A Survey".
    smallest = min(len(a), len(b))
    if smallest < 6 or inter / smallest < 0.95:
        return False

    if pa is not None and pb is not None:
        ya, yb = pa.get("year"), pb.get("year")
        if isinstance(ya, int) and isinstance(yb, int) and abs(ya - yb) > _MAX_YEAR_GAP:
            return False
        sa, sb = _surnames(pa), _surnames(pb)
        if sa and sb and not (sa & sb):
            return False
    return union and inter / union >= _MIN_JACCARD_CONTAINED


# --------------------------------------------------------------------------
# Record quality: used to decide which duplicate survives.
# --------------------------------------------------------------------------
_BAD_VENUE_MARKERS = (
    "conference on", "proceedings of", "international conference",
    "transactions on", "advances in", "workshop", "corr",
)


def _venue_quality(v):
    """2 = proper short acronym, 1 = something, 0 = empty/publisher noise."""
    v = (v or "").strip()
    if not v:
        return 0
    low = v.lower()
    if low in ("arxiv", "ieee", "acm", "springer", "openreview", "lncs", "corr"):
        return 0
    if len(v) <= 22 and not any(m in low for m in _BAD_VENUE_MARKERS):
        return 2
    return 1


def _score(p):
    s = 0
    if arxiv_key(p):
        s += 4
    s += 2 * _venue_quality(p.get("venue"))
    y = p.get("year")
    if isinstance(y, int) and 1980 <= y <= 2035:
        s += 2
    if p.get("authors"):
        s += 1
    if p.get("source") == "seed":      # human-curated entries win ties
        s += 1
    s += min(len(p.get("title") or ""), 120) / 1000.0   # richer title wins
    return s


def _merge(primary, others):
    """Fill primary's missing fields from the other group members."""
    out = dict(primary)
    for o in others:
        if not out.get("arxiv_id") and o.get("arxiv_id"):
            out["arxiv_id"] = o["arxiv_id"]
        if not out.get("year") and o.get("year"):
            out["year"] = o["year"]
        if _venue_quality(out.get("venue")) == 0 and _venue_quality(o.get("venue")):
            out["venue"] = o["venue"]
        if not out.get("authors") and o.get("authors"):
            out["authors"] = o["authors"]
        if not out.get("category") or out.get("category") is None:
            if o.get("category"):
                out["category"] = o["category"]
    # Prefer an arXiv link: generate_readme renders arxiv_id first, but keep
    # url consistent with whatever we ended up with.
    if out.get("arxiv_id"):
        out["url"] = "https://arxiv.org/abs/%s" % out["arxiv_id"]
    return out


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def consolidate(papers, fuzzy=True):
    """Return (deduped_papers, removed_pairs).

    removed_pairs is a list of (survivor_title, dropped_title, reason) so the
    caller can print/verify what was merged.
    """
    n = len(papers)
    uf = _UF(n)

    by_title = {}
    by_arxiv = {}
    for i, p in enumerate(papers):
        tk = title_key(p.get("title", ""))
        if tk:
            if tk in by_title:
                uf.union(by_title[tk], i)
            else:
                by_title[tk] = i
        ak = arxiv_key(p)
        if ak:
            if ak in by_arxiv:
                uf.union(by_arxiv[ak], i)
            else:
                by_arxiv[ak] = i

    if fuzzy:
        toks = [_tokens(p.get("title", "")) for p in papers]
        for i in range(n):
            for j in range(i + 1, n):
                if uf.find(i) != uf.find(j) and _similar(
                    toks[i], toks[j], papers[i], papers[j]
                ):
                    uf.union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    removed = []
    kept = []
    for members in groups.values():
        if len(members) == 1:
            kept.append(papers[members[0]])
            continue
        ranked = sorted(members, key=lambda i: -_score(papers[i]))
        survivor = papers[ranked[0]]
        others = [papers[i] for i in ranked[1:]]
        for o in others:
            removed.append((
                survivor.get("title", ""),
                o.get("title", ""),
                _reason(survivor, o),
            ))
        kept.append(_merge(survivor, others))

    # Preserve the original ordering of the survivors.
    order = {id(p): i for i, p in enumerate(papers)}
    kept.sort(key=lambda p: order.get(id(p), 1 << 30))

    # Last-mile cleanup on every survivor: DBLP's trailing full stop and the
    # "year scraped out of an arXiv id" bug (2005.05751 -> 2005, should be 2020).
    kept = [_tidy(p) for p in kept]
    return kept, removed


def _tidy(p):
    p = fix_year(dict(p))
    t = tidy_title(p.get("title", ""))
    if t and t != p.get("title"):
        p["title"] = t
    return p


def _reason(a, b):
    if title_key(a.get("title", "")) == title_key(b.get("title", "")):
        return "same title"
    if arxiv_key(a) and arxiv_key(a) == arxiv_key(b):
        return "same arXiv id %s" % arxiv_key(a)
    return "fuzzy title"


def main():
    store = load_json(PAPERS, {"papers": []})
    papers = store.get("papers", [])
    kept, removed = consolidate(papers)
    print(f"[dedupe] {len(papers)} -> {len(kept)} ({len(removed)} duplicates merged)")
    for surv, dropped, why in removed:
        print(f"   - drop {dropped[:70]!r}")
        print(f"     keep {surv[:70]!r}  [{why}]")
    if removed:
        save_json(PAPERS, {"papers": kept})
        print(f"[dedupe] wrote {PAPERS}")
    return kept


if __name__ == "__main__":
    main()
