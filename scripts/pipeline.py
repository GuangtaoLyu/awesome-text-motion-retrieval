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

from lib_common import load_json, save_json, norm_title  # noqa: E402
import harvest  # noqa: E402
import generate_readme  # noqa: E402
import seed_from_readme  # noqa: E402

DATA = os.path.join(ROOT, "data")
PAPERS = os.path.join(DATA, "papers.json")
CONFIG = os.path.join(ROOT, "config.json")
README = os.path.join(ROOT, "README.md")


def dedup_key(p):
    """Key used to skip already-listed papers."""
    k = norm_title(p.get("title", ""))
    if p.get("arxiv_id"):
        k += "|" + p["arxiv_id"]
    if p.get("url"):
        k += "|" + p["url"]
    return k


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

    existing = {dedup_key(p) for p in papers}

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
        if dedup_key(c) in existing:
            continue
        try:
            c["category"] = classify.classify(c, config)
        except Exception:
            c["category"] = config.get("default_category")
        c["added"] = datetime.date.today().isoformat()
        papers.append(c)
        existing.add(dedup_key(c))
        added += 1

    save_json(PAPERS, {"papers": papers})
    generate_readme.generate(papers, config, README)
    print(f"[pipeline] +{added} new paper(s); total {len(papers)}")
    git_commit(added)


def git_commit(n):
    if n == 0:
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
        f"auto-update: +{n} paper(s) via multi-source scan "
        f"({datetime.date.today().isoformat()})"
    )
    subprocess.call(["git", "commit", "-q", "-m", msg], cwd=ROOT)


if __name__ == "__main__":
    main()
