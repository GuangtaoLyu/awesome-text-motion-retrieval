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
        m = re.match(r"^#{1,2}\s+(\d{4})\b", line.strip())
        if m:
            year = int(m.group(1))
            continue
        m = re.search(r"\*\*(.+?)\*\*", line)
        if not m:
            continue
        title = " ".join(m.group(1).split())
        if len(title) < 10:  # skip short headings / badges
            continue
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
        papers.append({
            "title": title,
            "authors": [auth] if auth else [],
            "year": year,
            "venue": venue,
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
