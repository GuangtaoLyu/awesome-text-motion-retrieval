# -*- coding: utf-8 -*-
"""Render README.md from papers.json + config taxonomy.

Groups papers by category (in the taxonomy's order), numbers them within each
category (so adding a paper never forces a global renumber), and sorts by year
desc then title. This is the auto-reorganized view the user asked for.
"""
import datetime


def _anchor(cat):
    return cat.lower().replace(" & ", " and ").replace(" ", "-").replace("&", "and")


def format_entry(idx, p):
    title = p.get("title", "")
    authors = p.get("authors") or []
    if authors:
        first = authors[0].split()[-1]
        auth = f"{first} et al." if len(authors) > 1 else authors[0]
    else:
        auth = ""
    link = ""
    if p.get("arxiv_id"):
        link = f"[arXiv](https://arxiv.org/abs/{p['arxiv_id']})"
    elif p.get("url"):
        label = p.get("venue") or "Paper"
        link = f"[{label}]({p['url']})"
    parts = [f"{idx}. **{title}**"]
    if auth:
        parts.append(f", {auth}")
    if link:
        parts.append(f", {link}")
    return "".join(parts)


def generate(papers, config, readme_path):
    taxonomy = config.get("taxonomy", [])
    cats = {c: [] for c in taxonomy}
    for p in papers:
        c = p.get("category") or config.get("default_category")
        if c not in cats:
            c = config.get("default_category")
        cats.setdefault(c, []).append(p)

    lines = []
    lines.append(f"# {config.get('display_name', 'Awesome List')}")
    lines.append("")
    if config.get("description"):
        lines.append(config["description"])
        lines.append("")
    lines.append(
        f"*Last updated: {datetime.date.today().isoformat()} | "
        f"Total papers: {len(papers)}*"
    )
    lines.append("")
    lines.append(
        "> \U0001F916 Auto-updated weekly by GitHub Actions "
        "(multi-source: arXiv + DBLP + Semantic Scholar + Crossref). "
        "Entries are auto-categorized by topic."
    )
    lines.append("")

    # Contents TOC (only non-empty categories)
    lines.append("## Contents")
    for c in taxonomy:
        if cats.get(c):
            lines.append(f"- [{c}](#{_anchor(c)})")
    lines.append("")

    for c in taxonomy:
        items = cats.get(c, [])
        if not items:
            continue
        items.sort(key=lambda p: (-(p.get("year") or 0), p["title"].lower()))
        lines.append(f"## {c}")
        lines.append("")
        for i, p in enumerate(items, 1):
            lines.append(format_entry(i, p))
        lines.append("")

    lines.append("## Contributing")
    lines.append("")
    lines.append(
        "Found a missing or mis-categorized paper? Open an issue or pull "
        "request — the list is regenerated automatically, so manual README "
        "edits will be overwritten on the next run."
    )
    lines.append("")

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
