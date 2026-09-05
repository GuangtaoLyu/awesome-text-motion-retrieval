# -*- coding: utf-8 -*-
"""One-time bootstrap: parse the EXISTING curated README into papers.json.

Run once (locally or as the pipeline's first-run fallback) so the 40-60 already
curated papers are not lost when we switch to the generated README. Best-effort
regex parse of the `N. **Title** , Author et al. , [link]` format; the
classifier then assigns each seeded paper a category automatically.
"""
import re
import json
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_common import save_json


def parse(readme_path):
    """Return a list of paper dicts parsed from the existing README."""
    with open(readme_path, encoding="utf-8") as f:
        text = f.read()
    year = None
    papers = []
    for line in text.splitlines():
        # Year headings come in two shapes across the repos:
        #   style/prediction: `## 2026`        (h2)
        #   retrieval:        `### 2026`        (h3 subsection)
        #   retrieval range:  `### 2022 and Earlier` (leading year recovered)
        # Match h1–h3 so all of the above set the current `year` context.
        m = re.match(r"^#{1,3}\s+(\d{4})\b", line.strip())
        if m:
            year = int(m.group(1))
            continue
        # Only treat numbered list items as papers:
        #   `1. **Title** , Author et al. , [link]`
        # Skip section intros / headings that contain bold description
        # phrases (e.g. "**cross-modal retrieval** between human motion").
        m = re.match(r"^\s*\d+\.\s+\*\*(.+?)\*\*", line)
        if not m:
            continue
        title = " ".join(m.group(1).split())
        auth = ""
        am = re.search(r"\*\*\s*.+?\*\*\s*,\s*([^,]+?)\s*et al", line)
        if am:
            auth = am.group(1).strip()
        # Collect every markdown link on the line. Entries may be:
        #   `[Paper](real-url)`  OR  `[![arXiv](badge)](real-url)`
        #   OR a badge-only link `[Paper](https://img.shields.io/badge/arXiv-<id>-...)`
        # The canonical paper link is the one that is NOT a shields.io image.
        # The arXiv id may live in the real link OR inside the badge itself.
        urls = re.findall(r"\]\((https?://[^)]+)\)", line)
        real = next((u for u in urls if "img.shields.io" not in u), None)
        if real is None and urls:
            real = urls[-1]
        arxiv = None
        for u in urls:
            km = re.search(r"arxiv\.org/abs/([0-9]+\.[0-9]+)", u)
            if km:
                arxiv = km.group(1)
                break
            km = re.search(r"arXiv-([0-9]+\.[0-9]+)", u)
            if km:
                arxiv = km.group(1)
        if arxiv:
            url = f"https://arxiv.org/abs/{arxiv}"
        else:
            url = real or ""
        venue = "arXiv" if arxiv else ""
        # Inline venue/year written by the curator on the entry line, e.g.
        # ", NeurIPS 2025," or ", ICLR 2026,". Prefer these when present
        # (more specific than the section heading year, and authoritative).
        #
        # IMPORTANT: search only the VISIBLE text. The line also carries the
        # arXiv badge URL (.../arXiv-2005.05751-...), whose "2005" is a YYMM
        # prefix, not a year -- scanning the raw line used to record
        # "Unpaired Motion Style Transfer" as a 2005 paper instead of 2020.
        plain = re.sub(r"\]\([^)]*\)", " ", line)   # markdown link targets
        plain = re.sub(r"!\[[^\]]*\]", " ", plain)  # badge image labels
        inline_year = None
        iy = re.search(r"\b((?:19|20)\d{2})\b", plain)
        if iy:
            inline_year = int(iy.group(1))
        inline_venue = ""
        iv = re.search(
            r"\b(NeurIPS|ICLR|ICML|CVPR|ICCV|ECCV|AAAI|SIGIR|SIGGRAPH|ACM\s*MM|"
            r"Displays|ICME|IEEE|TPAMI|TVCG|IJCV|TOG|MM|IJCAI|BMVC|WACV|3DV|"
            r"TMM|RA-L)\b[ ,]+((?:19|20)\d{2})",
            line, re.IGNORECASE)
        if iv:
            inline_venue = iv.group(1).strip()
        eff_year = inline_year if inline_year else year
        eff_venue = inline_venue or venue
        papers.append({
            "title": title,
            "authors": [auth] if auth else [],
            "year": eff_year,
            "venue": eff_venue,
            "url": url,
            "arxiv_id": arxiv,
            "source": "seed",
        })
    return papers


def run(config, readme_path, papers_path):
    import classify  # per-repo classification script
    papers = parse(readme_path)
    for p in papers:
        try:
            p["category"] = classify.classify(p, config)
        except Exception:
            p["category"] = config.get("default_category")
        p["added"] = datetime.date.today().isoformat()
    save_json(papers_path, {"papers": papers})
    return papers
