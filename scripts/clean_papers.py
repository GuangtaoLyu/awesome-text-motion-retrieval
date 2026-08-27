# -*- coding: utf-8 -*-
"""One-shot cleaner for an already-seeded papers.json.

Why this exists: the curated awesome lists were polluted with (a) exact/near
duplicates and (b) off-topic papers pulled in by broad keyword searches
(e.g. "brownian motion", "molecular foundation model", astronomy, finance).

Design (the FIX for the earlier bug):
  * We do NOT keep papers by a loose substring *whitelist* (that wrongly kept
    garbage: "pur-pose" matched 'pose', "brownian motion" matched 'motion').
  * Instead we DROP only high-confidence OFF-TOPIC items via a curated
    *blocklist* of precise, word-boundary regexes. Everything else (the
    genuine curation) is preserved. This is conservative: it never deletes a
    paper just because it lacks a keyword.
  * Duplicates are removed by normalized-title (plus arxiv_id / url).

Run:  python scripts/clean_papers.py
"""
import os
import re
import sys
import json
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from lib_common import load_json, save_json, norm_title  # noqa: E402
import generate_readme  # noqa: E402
import seed_from_readme  # noqa: E402

DATA = os.path.join(ROOT, "data")
PAPERS = os.path.join(DATA, "papers.json")
CONFIG = os.path.join(ROOT, "config.json")
README = os.path.join(ROOT, "README.md")

# ---------------------------------------------------------------------------
# Off-topic blocklist. Each entry is a regex (matched case-insensitively).
# Word boundaries (\b) are used so "pose" does NOT match "purpose", "motion"
# does NOT match "brownian motion" on its own, etc.
# ---------------------------------------------------------------------------
EXCLUDE = [
    # Finance / economics
    r"stock price", r"s&p", r"s&amp;p", r"\btasi\b", r"box office",
    r"\bgarch\b", r"\barima\b", r"e-grocery", r"\bportfolio\b", r"\btrading\b",
    r"geometric brownian", r"brownian motion", r"fractional brownian",
    # Abstract maths / physics
    r"mean motion", r"bounded mean", r"\bvortex\b", r"two-torus", r"\btqft\b",
    r"reshetikhin", r"fractal brownian", r"\brigidity\b", r"quantum-inspired",
    r"ring motion",
    # Astronomy / geophysics / remote sensing
    r"trappist", r"geostationary", r"atmospheric motion", r"planet-host",
    r"rebels-", r"redshift", r"landslide", r"ground motion", r"spaceborne",
    r"grounding line", r"neutral wire", r"\bz\s*=\s*\d",
    # Chemistry / biology / agriculture
    r"molecular foundation", r"olfactory", r"crop-stress", r"\bndre\b",
    r"metallic alloys", r"electric propulsion",
    # Medical imaging (non-motion-domain)
    r"medical image", r"cine mri", r"radiotherapy", r"respiratory motion",
    r"wall motion", r"\bcardiac\b", r"coronary", r"visual cortex",
    r"motion artifact",
    # Video / graphics editing
    r"motion graphics", r"appearance editing", r"ai-generated images",
    r"video understanding", r"visual grounding", r"video multi-modal",
    r"kv cache", r"motion pictures",
    # Maritime / warehouse robotics
    r"vessel motion", r"ship motion", r"ship bottom", r"shuttle vehicles",
    r"storage/retrieval", r"robotic compact", r"\boffshore\b",
    # Non-human robotics
    r"motion planning", r"embodied manipulation", r"embodied learning",
    r"multi-arm", r"humanoid robot motion controller",
    r"robot motion in natural language", r"language movement primitives",
    r"elephant-inspired", r"tc-idm",
    # NLP / ML off-topic
    r"topic embedding", r"topic evolution", r"tree-of-thought", r"socratic",
    r"human-agent planning", r"traceml", r"joint analysis of text",
    r"structured data", r"document retrieval", r"dual-brain",
    r"machine learning development", r"human reading", r"referential disruptions",
    r"game-theoretic", r"crime prediction", r"object detector",
    r"object recognition", r"physics reasoning", r"grounding graphs",
    r"natural language commands", r"vehicle retrieval", r"vehicle appearance",
    r"traffic video", r"grounding mat", r"patient preferences",
    # Solar
    r"photovoltaic", r"solar forecasting", r"cloud motion",
    # Misc noise
    r"the value of human expertise", r"retracted", r"retraction notice",
    r"decision letter", r"^review for", r"supplementary material",
    r"visual general intelligence", r"white paper", r"defense against",
    r"jailbreak",
]

EXCLUDE_RE = [re.compile(p, re.IGNORECASE) for p in EXCLUDE]


def is_offtopic(title):
    t = title or ""
    hits = [p.pattern for p in EXCLUDE_RE if p.search(t)]
    return hits


def main():
    config = load_json(CONFIG, {})
    store = load_json(PAPERS, {"papers": []})
    papers = store.get("papers", [])

    # First run: seed from the existing curated README if empty.
    if not papers:
        print("[clean] papers.json empty -> seeding from README")
        papers = seed_from_readme.run(config, README, PAPERS)
        print(f"[clean] seeded {len(papers)} papers")

    # --- 1) de-duplicate ------------------------------------------------
    seen = {}
    dup_keys = set()
    deduped = []
    for p in papers:
        nt = norm_title(p.get("title", ""))
        url = (p.get("url") or "").lower().replace("https://", "").replace("http://", "").rstrip("/")
        aix = p.get("arxiv_id") or ""
        key = nt
        if key in seen or (url and url in seen) or (aix and aix in seen):
            dup_keys.add(nt)
            continue
        seen[nt] = True
        if url:
            seen[url] = True
        if aix:
            seen[aix] = True
        deduped.append(p)
    n_dup = len(papers) - len(deduped)
    print(f"[clean] dedup: {len(papers)} -> {len(deduped)} ({n_dup} duplicates)")

    # --- 2) drop off-topic ---------------------------------------------
    kept = []
    removed = []
    for p in deduped:
        hits = is_offtopic(p.get("title", ""))
        if hits:
            p = dict(p)
            p["_drop_reason"] = "; ".join(hits)
            removed.append(p)
        else:
            kept.append(p)
    print(f"[clean] off-topic filter: {len(deduped)} -> {len(kept)} ({len(removed)} removed)")

    # --- 3) regenerate --------------------------------------------------
    save_json(PAPERS, {"papers": kept})
    generate_readme.generate(kept, config, README)

    # --- 4) report ------------------------------------------------------
    report = []
    report.append(f"# Cleanup report ({datetime.date.today().isoformat()})")
    report.append(f"before={len(papers)}  after_dedup={len(deduped)}  after_offtopic={len(kept)}")
    report.append(f"duplicates_removed={n_dup}  offtopic_removed={len(removed)}")
    report.append("")
    report.append("## Off-topic removed")
    for p in removed:
        report.append(f"- {p.get('title','')}   [{p.get('_drop_reason','')}]")
    out = os.path.join(ROOT, "cleanup_report.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    print(f"[clean] wrote {out}")
    print(f"[clean] FINAL: {len(kept)} papers")


if __name__ == "__main__":
    main()
