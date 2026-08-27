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


def _ieee_from_doi(doi):
    """IEEE DOIs encode the venue: 10.1109/<CONF>... -> conference acronym."""
    if not doi:
        return None
    m = re.match(r"10\.1109/([A-Za-z]+)", doi)
    if not m:
        return None
    seg = m.group(1)
    return {
        "MCG": "IEEE CG&A", "TVCG": "IEEE TVCG", "TPAMI": "IEEE TPAMI",
        "TRO": "IEEE TRO", "RA-L": "IEEE RA-L", "ACCESS": "IEEE Access",
        "TMM": "IEEE TMM", "TGRS": "IEEE TGRS",
    }.get(seg, seg)


def _crossref_venue(doi, cache):
    """Best conference/journal name from a DOI via Crossref.

    Prefers `event.acronym` (e.g. "MM '24") and the *specific* container-title
    (Crossref lists the series name first, e.g. "Lecture Notes in Computer
    Science", then the real conference, e.g. "Computer Vision - ECCV 2020").
    Returns None on failure so the caller can try other sources.
    """
    if doi in cache:
        return cache[doi]
    try:
        url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
        data = harvest._get(url, timeout=25)
        msg = json.loads(data).get("message", {})
        acr = (msg.get("event") or {}).get("acronym")
        if acr:
            cache[doi] = acr
            return acr
        cts = msg.get("container-title") or []
        pick = ""
        for c in cts:
            if c and "lecture notes" not in c.lower():
                pick = c
        if not pick and cts:
            pick = cts[-1]
        cache[doi] = pick or None
        return pick or None
    except Exception:
        cache[doi] = None
        return None


def _crossref_by_title(title, cache):
    """Fallback: resolve venue from the paper title via Crossref."""
    if not title:
        return None
    key = norm_title(title)
    if key in cache:
        return cache[key]
    try:
        q = ("https://api.crossref.org/works?query.bibliographic="
             + urllib.parse.quote(title) + "&rows=1")
        data = harvest._get(q, timeout=25)
        items = json.loads(data).get("message", {}).get("items", [])
        if not items:
            cache[key] = None
            return None
        m = items[0]
        acr = (m.get("event") or {}).get("acronym")
        if acr:
            cache[key] = acr
            return acr
        cts = m.get("container-title") or []
        pick = ""
        for c in cts:
            if c and "lecture notes" not in c.lower():
                pick = c
        if not pick and cts:
            pick = cts[-1]
        cache[key] = pick or None
        return pick or None
    except Exception:
        cache[key] = None
        return None


def _short_venue(v):
    """Collapse verbose Crossref container-titles into familiar acronyms."""
    if not v:
        return v
    table = [
        (r"MM '?\d+", "ACM MM"),
        (r"International Conference on Multimedia and Expo", "ICME"),
        (r"International Conference on Multimedia", "ACM MM"),
        (r"SIGIR Conference", "SIGIR"),
        (r"International Conference on Computer Vision and Pattern Recognition", "CVPR"),
        (r"Conference on Computer Vision and Pattern Recognition", "CVPR"),
        (r"International Conference on Computer Vision", "ICCV"),
        (r"Computer Vision – ECCV", "ECCV"),
        (r"European Conference on Computer Vision", "ECCV"),
        (r"International Conference on Machine Learning", "ICML"),
        (r"Conference on Neural Information Processing Systems", "NeurIPS"),
        (r"AAAI Conference on Artificial Intelligence", "AAAI"),
        (r"International Conference on Acoustics", "ICASSP"),
        (r"International Conference on Robotics and Automation", "ICRA"),
        (r"International Conference on Intelligent Robots and Systems", "IROS"),
        (r"International Joint Conference on Artificial Intelligence", "IJCAI"),
        (r"British Machine Vision Conference", "BMVC"),
        (r"Winter Conference on Applications of Computer Vision", "WACV"),
        (r"International Conference on 3D Vision", "3DV"),
        (r"Computer Graphics Forum", "Comput. Graph. Forum"),
        (r"Comput\. Grap\. Appl\.", "IEEE CG&A"),
        (r"Proc\. ACM Comput\. Graph\. Interact\. Tech\.", "PACMCGIT"),
        (r"ACM Transactions on Graphics", "ACM TOG"),
        (r"International Journal of Computer Vision", "IJCV"),
        (r"IEEE Transactions on Pattern Analysis and Machine Intelligence", "IEEE TPAMI"),
        (r"IEEE Transactions on Visualization and Computer Graphics", "IEEE TVCG"),
        (r"IEEE Transactions on Robotics", "IEEE TRO"),
        (r"IEEE Robotics and Automation Letters", "IEEE RA-L"),
        (r"Lecture Notes in Computer Science", "LNCS"),
        (r"SIGGRAPH Asia", "SIGGRAPH Asia"),
        (r"Eurographics", "Eurographics"),
        (r"Proceedings of the ACM on Computer Graphics and Interactive Techniques", "PACMCGIT"),
        (r"SA '?\d+", "SIGGRAPH Asia"),
        (r"IEEE Signal Processing Letters", "IEEE SPL"),
        (r"PLoS One", "PLOS ONE"),
        (r"Displays", "Displays"),
    ]
    for pat, abbr in table:
        if re.search(pat, v, re.IGNORECASE):
            return abbr
    return v


# Publisher/series names are NOT venues -- never emit these as the venue.
_PUBLISHER = {"ieee", "acm", "springer", "openreview", "lncs", "arxiv", "", None}


def enrich_venues(papers):
    """Fill `venue` with the *conference/journal* name (CVPR, ECCV, NeurIPS,
    ACM MM, ...). Resolution order:
      arXiv id            -> "arXiv"
      DOI 10.1109/<CONF>  -> IEEE conference acronym
      DOI 10.2312/...     -> Eurographics
      DOI (other)         -> Crossref (event.acronym / specific container-title)
      any other / no link -> Crossref by title
    Already-good venues (e.g. captured inline from the README) are kept.
    """
    cache = {}
    for p in papers:
        cur = p.get("venue")
        if cur and str(cur).lower() not in _PUBLISHER:
            continue  # already a real venue (incl. inline from README)
        if p.get("arxiv_id"):
            p["venue"] = "arXiv"
            continue
        url = p.get("url") or ""
        doi = _doi_from_url(url)
        v = None
        if doi:
            ieee = _ieee_from_doi(doi)
            if ieee:
                v = ieee
            elif doi.startswith("10.2312/"):
                v = "Eurographics"
            else:
                v = _crossref_venue(doi, cache)
        if not v:
            # OpenReview / IEEE-document / no-link: resolve by title.
            v = _crossref_by_title(p.get("title"), cache)
        if v:
            v = _short_venue(v)
            v = re.sub(r"\s+(?:19|20)\d{2}$", "", v)
            if str(v).lower() not in _PUBLISHER:
                p["venue"] = v
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
