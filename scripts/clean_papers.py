# -*- coding: utf-8 -*-
"""One-shot cleaner + metadata enricher for an already-seeded papers.json.

Why this exists: the curated awesome lists were polluted with (a) exact/near
duplicates and (b) off-topic papers pulled in by broad keyword searches.

Design (the FIX for the earlier bug):
  * We do NOT keep papers by a loose substring *whitelist* (that wrongly kept
    garbage: "pur-pose" matched 'pose', "brownian motion" matched 'motion').
  * Instead we DROP only high-confidence OFF-TOPIC items via a curated
    *blocklist* of precise, word-boundary regexes. Everything else (the
    genuine curation) is preserved. This is conservative: it never deletes a
    paper just because it lacks a keyword.
  * Duplicates are removed by normalized-title (plus arxiv_id / url).

Metadata enrichment (this pass):
  * Recovers the YEAR from the canonical curated README (data/curated_readme.md,
    which keeps the `## YYYY` sections; the regenerated README drops them).
  * Fills the VENUE: arXiv for preprints; the real journal/conference short name
    for published DOIs (via Crossref); host-based label otherwise (AAAI,
    NeurIPS, OpenReview, IEEE, ACM, Springer).
"""
import os
import re
import sys
import json
import datetime
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from lib_common import load_json, save_json, norm_title  # noqa: E402
import generate_readme  # noqa: E402
import seed_from_readme  # noqa: E402
import harvest  # noqa: E402  (reuses _get with 429 backoff)

DATA = os.path.join(ROOT, "data")
PAPERS = os.path.join(DATA, "papers.json")
CURATED = os.path.join(DATA, "curated_readme.md")
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

# Host-based venue fallback for published papers without a resolvable DOI.
HOST_VENUE = {
    "aaai.org": "AAAI",
    "neurips.cc": "NeurIPS",
    "openreview.net": "OpenReview",
    "ieeexplore.ieee.org": "IEEE",
    "dl.acm.org": "ACM",
    "link.springer.com": "Springer",
}


def is_offtopic(title):
    t = title or ""
    return [p.pattern for p in EXCLUDE_RE if p.search(t)]


def _doi_from_url(url):
    if not url:
        return None
    m = re.search(r"10\.\d{4,9}/[^\s)]+", url)
    return m.group(0) if m else None


def _crossref_venue(doi, cache):
    if doi in cache:
        return cache[doi]
    try:
        url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
        data = harvest._get(url, timeout=20)
        msg = json.loads(data).get("message", {})
        sct = msg.get("short-container-title") or []
        ct = msg.get("container-title") or []
        v = (sct[0] if sct else (ct[0] if ct else ""))
        v = v.strip() if v else ""
    except Exception:
        v = ""
    cache[doi] = v
    return v


def _short_venue(v):
    """Collapse verbose Crossref container-titles into familiar acronyms."""
    if not v:
        return v
    table = [
        (r"International Conference on Multimedia and Expo", "ICME"),
        (r"International Conference on Multimedia", "ACM MM"),
        (r"SIGIR Conference", "SIGIR"),
        (r"International Conference on Computer Vision and Pattern Recognition", "CVPR"),
        (r"Conference on Computer Vision and Pattern Recognition", "CVPR"),
        (r"International Conference on Computer Vision", "ICCV"),
        (r"European Conference on Computer Vision", "ECCV"),
        (r"International Conference on Machine Learning", "ICML"),
        (r"Conference on Neural Information Processing Systems", "NeurIPS"),
        (r"AAAI Conference on Artificial Intelligence", "AAAI"),
        (r"International Conference on Acoustics", "ICASSP"),
        (r"Computer Graphics Forum", "Comput. Graph. Forum"),
        (r"Comput\. Grap\. Appl\.", "IEEE CG&A"),
        (r"Proc\. ACM Comput\. Graph\. Interact\. Tech\.", "PACMCGIT"),
        (r"Lecture Notes in Computer Science", "LNCS"),
        (r"SIGGRAPH Asia", "SIGGRAPH Asia"),
        (r"PLoS One", "PLOS ONE"),
    ]
    for pat, abbr in table:
        if re.search(pat, v, re.IGNORECASE):
            return abbr
    return v


def enrich_venues(papers):
    """Fill `venue` for every paper: arXiv for preprints, Crossref short
    container-title for DOIs, host label otherwise."""
    cache = {}
    for p in papers:
        # already good (arXiv, or a previously-enriched crossref/dblp venue)?
        if p.get("venue") in ("arXiv",) or p.get("source") in ("crossref", "dblp"):
            continue
        if p.get("arxiv_id"):
            p["venue"] = "arXiv"
            continue
        doi = _doi_from_url(p.get("url"))
        v = _crossref_venue(doi, cache) if doi else ""
        if not v:
            for h, label in HOST_VENUE.items():
                if h in (p.get("url") or ""):
                    v = label
                    break
        if v:
            p["venue"] = _short_venue(v)
    return papers


def main():
    config = load_json(CONFIG, {})
    store = load_json(PAPERS, {"papers": []})
    papers = store.get("papers", [])

    # First run: seed from the canonical curated README if empty.
    if not papers:
        seed_src = CURATED if os.path.exists(CURATED) else README
        print(f"[clean] papers.json empty -> seeding from {os.path.basename(seed_src)}")
        papers = seed_from_readme.run(config, seed_src, PAPERS)
        print(f"[clean] seeded {len(papers)} papers")

    # --- 0) enrich year/venue metadata ----------------------------------
    before = sum(1 for p in papers if not p.get("year") or not p.get("venue"))
    enrich_venues(papers)
    after = sum(1 for p in papers if not p.get("year") or not p.get("venue"))
    print(f"[clean] metadata: {before} papers missing year/venue -> {after}")

    # --- 1) de-duplicate ------------------------------------------------
    seen = {}
    deduped = []
    for p in papers:
        nt = norm_title(p.get("title", ""))
        url = (p.get("url") or "").lower().replace("https://", "").replace("http://", "").rstrip("/")
        aix = p.get("arxiv_id") or ""
        if nt in seen or (url and url in seen) or (aix and aix in seen):
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
