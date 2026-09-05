# -*- coding: utf-8 -*-
"""Orchestrator (runs automatically on schedule — no human review).

1. If papers.json is empty, bootstrap from the existing README (seed).
2. Harvest candidates from all sources (arXiv/DBLP/S2/Crossref).
3. Dedup against existing papers (by normalized title; also by arXiv id/DOI).
4. Classify each new paper with the per-repo classify.py.
5. Regenerate README.md from papers.json.
6. Auto-commit (the workflow pushes). Never pushes itself.

Run:  python scripts/pipeline.py
"""
import os
import sys
import json
import datetime
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from lib_common import (  # noqa: E402
    load_json, save_json, norm_title, title_key, arxiv_key,
)
import harvest  # noqa: E402
import generate_readme  # noqa: E402
import seed_from_readme  # noqa: E402
import dedupe  # noqa: E402

DATA = os.path.join(ROOT, "data")
PAPERS = os.path.join(DATA, "papers.json")
CONFIG = os.path.join(ROOT, "config.json")
README = os.path.join(ROOT, "README.md")


def dedup_key(p):
    """Canonical title key used to skip already-listed papers.

    IMPORTANT: this must NOT include the url or arxiv_id. Multi-source
    harvesting (arXiv + DBLP + Semantic Scholar + Crossref) returns the SAME
    paper several times with different links (arxiv.org/abs/... vs
    semanticscholar.org/paper/... vs doi.org/...). Appending the link to the
    key made every variant look like a brand-new paper, which is exactly how
    the 2026-08-31 auto-update added ~140 duplicate entries.
    """
    return title_key(p.get("title", ""))


def main():
    config = load_json(CONFIG, {})
    store = load_json(PAPERS, {"papers": []})
    papers = store.get("papers", [])

    # First run: import the existing curated README so we don't lose it.
    if not papers:
        print("[pipeline] papers.json empty -> seeding from existing README")
        try:
            papers = seed_from_readme.run(config, README, PAPERS)
            print(f"[pipeline] seeded {len(papers)} papers")
        except Exception as ex:
            print(f"[pipeline] seed failed: {ex!r}")

    # Index BOTH the canonical title and the arXiv id: the same paper can come
    # back from different sources under slightly different titles, but it will
    # always carry the same arXiv id (and vice versa).
    existing_titles = {dedup_key(p) for p in papers}
    existing_arxiv = {a for a in (arxiv_key(p) for p in papers) if a}

    cands = harvest.harvest(config)
    # Relevance gate: for fully-automated lists with no human review, only keep
    # candidates whose TITLE contains a core term for this topic. This drops the
    # huge volume of loose keyword matches (e.g. image style transfer) that a
    # broad arXiv/Crossref search otherwise pulls in.
    must = [m.lower() for m in config.get("must_include", [])]
    if must:
        before = len(cands)
        cands = [
            c for c in cands
            if any(k in (c.get("title", "") or "").lower() for k in must)
        ]
        print(f"[pipeline] relevance filter: {before} -> {len(cands)} candidates")
    import classify
    added = 0
    for c in cands:
        ck = dedup_key(c)
        ca = arxiv_key(c)
        if ck in existing_titles or (ca and ca in existing_arxiv):
            continue
        try:
            c["category"] = classify.classify(c, config)
        except Exception:
            c["category"] = config.get("default_category")
        c["added"] = datetime.date.today().isoformat()
        papers.append(c)
        existing_titles.add(ck)
        if ca:
            existing_arxiv.add(ca)
        added += 1

    # Safety net: even if a duplicate slips past the index above (e.g. the same
    # paper harvested under two different arXiv ids), consolidate before saving
    # so papers.json never accumulates duplicates.
    papers, merged = dedupe.consolidate(papers)
    if merged:
        print(f"[pipeline] consolidated {merged} duplicate(s) before saving")

    save_json(PAPERS, {"papers": papers})
    generate_readme.generate(papers, config, README)
    print(f"[pipeline] +{added} new paper(s); total {len(papers)}")
    git_commit(added, len(merged))


def git_commit(added, merged=0):
    """Commit only when the list actually changed.

    `merged` matters as much as `added`: a run that only collapses duplicates
    produces no new papers but still needs to be committed, otherwise the
    duplicates just stay on GitHub.
    """
    if not added and not merged:
        print("[pipeline] nothing changed, skip commit")
        return
    try:
        subprocess.check_output(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=ROOT, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return
    subprocess.call(["git", "add", "-A"], cwd=ROOT)
    msg = (
        f"auto-update: +{added} new, -{merged} duplicate(s) "
        f"({datetime.date.today().isoformat()})"
    )
    subprocess.call(["git", "commit", "-q", "-m", msg], cwd=ROOT)


if __name__ == "__main__":
    main()
