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
import dedupe  # noqa: E402
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
    """IEEE DOIs encode the venue: 10.1109/<CONF>... -> conference acronym.

    Returns the canonical (proper-cased) acronym, e.g. "ICCV", "CVPR",
    "IEEE CG&A", "IEEE SPL". The raw segment is lower-cased by the regex, so we
    uppercase and map it through the known set.
    """
    if not doi:
        return None
    m = re.match(r"10\.1109/([A-Za-z]+)", doi)
    if not m:
        return None
    seg = m.group(1).upper()
    return {
        "MCG": "IEEE CG&A", "TVCG": "IEEE TVCG", "TPAMI": "IEEE TPAMI",
        "TRO": "IEEE TRO", "RA-L": "IEEE RA-L", "ACCESS": "IEEE Access",
        "TMM": "IEEE TMM", "TGRS": "IEEE TGRS", "LSP": "IEEE SPL",
        "CVPR": "CVPR", "ICCV": "ICCV", "ECCV": "ECCV", "ICME": "ICME",
        "ICRA": "ICRA", "WACV": "WACV", "IROS": "IROS", "FG": "FG",
        "3DV": "3DV", "BMVC": "BMVC", "IJCNN": "IJCNN", "TNNLS": "IEEE TNNLS",
    }.get(seg, seg)


def _year_from_doi(doi):
    """Extract the publication year encoded in a DOI string.

    IEEE DOIs embed the year as `<code>.<YYYY>.<article>` (e.g.
    `10.1109/iccv51701.2025.00948` -> 2025, `10.1109/lsp.2024.3425283` -> 2024).
    This is more reliable than Crossref's sometimes-online-first `issued` date
    (the IEEE SPL 2024 paper shows issued=2026 while its volume year is 2024).
    Eurographics DOIs embed it as `10.2312/<code>.<YYYY><rest>`.
    """
    if not doi:
        return None
    m = re.search(r"10\.1109/\w+\.(\d{4})\.", doi)
    if m:
        return int(m.group(1))
    m = re.search(r"10\.2312/\w+\.(\d{4})", doi)
    if m:
        return int(m.group(1))
    return None


def _crossref_meta(doi, cache):
    """Best (venue, year) from a DOI via Crossref, resolved TOGETHER.

    Returns a (venue, year) tuple (either may be None). Prefers `event.acronym`
    and the *specific* container-title (Crossref lists the series name first,
    e.g. "Lecture Notes in Computer Science", then the real conference, e.g.
    "Computer Vision - ECCV 2020"). The year comes from the `issued` date-parts;
    for IEEE/Eurographics DOIs we additionally trust the year encoded in the DOI
    string itself (more reliable than Crossref's sometimes-online-first
    `issued` date). Caches by DOI.
    """
    if doi in cache and isinstance(cache[doi], tuple):
        return cache[doi]
    venue = None
    year = _year_from_doi(doi)  # DOI-string year (IEEE/EG), may be None
    try:
        url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
        data = harvest._get(url, timeout=25)
        msg = json.loads(data).get("message", {})
        acr = (msg.get("event") or {}).get("acronym")
        if acr:
            venue = acr
        else:
            cts = msg.get("container-title") or []
            pick = ""
            for c in cts:
                if c and "lecture notes" not in c.lower():
                    pick = c
            if not pick and cts:
                pick = cts[-1]
            venue = pick or None
        dp = (msg.get("issued") or msg.get("published") or {}).get("date-parts")
        cr_year = None
        if dp and dp[0] and dp[0][0]:
            cr_year = int(dp[0][0])
        if year is None:
            year = cr_year
    except Exception:
        pass
    result = (venue, year)
    cache[doi] = result
    return result


def _crossref_meta_by_title(title, cache):
    """Fallback: resolve (venue, year) from the paper title via Crossref."""
    if not title:
        return (None, None)
    key = "title:" + norm_title(title)
    if key in cache:
        return cache[key]
    venue = None
    year = None
    try:
        q = ("https://api.crossref.org/works?query.bibliographic="
             + urllib.parse.quote(title) + "&rows=1")
        data = harvest._get(q, timeout=25)
        items = json.loads(data).get("message", {}).get("items", [])
        if items:
            m = items[0]
            acr = (m.get("event") or {}).get("acronym")
            if acr:
                venue = acr
            else:
                cts = m.get("container-title") or []
                pick = ""
                for c in cts:
                    if c and "lecture notes" not in c.lower():
                        pick = c
                if not pick and cts:
                    pick = cts[-1]
                venue = pick or None
            dp = (m.get("issued") or m.get("published") or {}).get("date-parts")
            if dp and dp[0] and dp[0][0]:
                year = int(dp[0][0])
    except Exception:
        pass
    result = (venue, year)
    cache[key] = result
    return result


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
        (r"Computer Vision\s*[–-]\s*ECCV", "ECCV"),
        (r"European Conference on Computer Vision", "ECCV"),
        (r"International Conference on Machine Learning", "ICML"),
        (r"Conference on Neural Information Processing Systems", "NeurIPS"),
        (r"International Conference on Learning Representations", "ICLR"),
        (r"International Conference on Computational Linguistics", "ACL"),
        (r"Conference on Empirical Methods in Natural Language Processing", "EMNLP"),
        (r"North American Chapter of the ACL.*Human Language Technologies", "NAACL"),
        (r"International Conference on Artificial Intelligence and Statistics", "AISTATS"),
        (r"Conference on Language Modeling", "COLM"),
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
    """Fill `venue` AND `year` from authoritative sources, resolved TOGETHER
    from the same source so they can never disagree.

    Resolution order:
      existing real venue+year (from an inline `Venue YYYY` annotation) -> kept
      arXiv id                 -> venue "arXiv" (year left as-is)
      DOI 10.1109/<CONF>       -> IEEE acronym + DOI-string year
      DOI 10.2312/...          -> Eurographics + DOI-string year
      DOI (other)              -> Crossref (event.acronym / container-title,
                                  issued year)
      any other / no link      -> Crossref by title (venue AND year)
    IMPORTANT: a paper's URL (including openreview.net) is NEVER used to infer
    its venue or year. OpenReview hosts ICLR/NeurIPS/ICML/COLM/AISTATS/workshops
    alike, so the link alone says nothing about the venue; only Crossref (or an
    explicit inline annotation) decides. Publisher/series names (IEEE, ACM,
    Springer, OpenReview, LNCS, arXiv) are never emitted as a venue.
    """
    cache = {}
    for p in papers:
        cur = p.get("venue")
        have_venue = bool(cur) and str(cur).lower() not in _PUBLISHER
        have_year = bool(p.get("year"))
        # Already fully populated -> leave untouched (preserves curator fixes).
        if have_venue and have_year:
            continue
        if p.get("arxiv_id"):
            if not have_venue:
                p["venue"] = "arXiv"
            continue
        url = p.get("url") or ""
        doi = _doi_from_url(url)
        v = None
        y = None
        if doi:
            ieee = _ieee_from_doi(doi)
            if ieee:
                v = ieee
                y = _year_from_doi(doi)
            elif doi.startswith("10.2312/"):
                v = "Eurographics"
                y = _year_from_doi(doi)
            else:
                v, y = _crossref_meta(doi, cache)
                if v is None:
                    v = _year_from_doi(doi)
                if y is None:
                    y = _year_from_doi(doi)
        if (v is None or y is None) and not doi:
            # OpenReview / IEEE-doc / no-link: resolve by title (URL ignored).
            tv, ty = _crossref_meta_by_title(p.get("title"), cache)
            if v is None:
                v = tv
            if y is None:
                y = ty
        if v:
            v = _short_venue(v)
            v = re.sub(r"\s+(?:19|20)\d{2}$", "", v)
            if str(v).lower() not in _PUBLISHER:
                p["venue"] = v
        if y and not have_year:
            p["year"] = y
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
    # Use the shared consolidator so duplicates are MERGED (arXiv link + real
    # venue both survive) instead of one variant being silently dropped, and so
    # the matching rules match pipeline.py's. The old loop keyed on the url,
    # which is exactly why multi-source harvests created duplicates.
    papers, dups = dedupe.consolidate(papers)
    n_dup = len(dups)
    deduped = papers
    for surv, dropped, why in dups:
        print(f"[clean]   - {dropped[:64]!r} -> {surv[:48]!r} [{why}]")
    print(f"[clean] dedup: {len(deduped) + n_dup} -> {len(deduped)} ({n_dup} duplicates)")

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
