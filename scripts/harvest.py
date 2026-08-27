# -*- coding: utf-8 -*-
"""Multi-source paper harvester for small awesome-lists.

These niche motion directions publish MORE in conferences (CVPR/ICCV/ECCV/
NeurIPS/ICML...) than on arXiv, so we search several no-auth sources:
  - arXiv        (export.arxiv.org API)
  - DBLP         (dblp.org API, strong conference coverage)
  - Semantic Scholar (broad, includes arXiv + venue + DOI)
  - Crossref     (published DOI / venue papers)

Each source is isolated in try/except so one flaky source never fails the run.
Returns a flat list of candidate dicts (no dedup against the existing list yet).
"""
import re
import json
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from lib_common import norm_title

NS = {"atom": "http://www.w3.org/2005/Atom"}


def _get(url, timeout=30, retries=2):
    """GET with 429 backoff so rate-limited sources (S2/DBLP) get a 2nd chance."""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "motion-awesome-bot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429 and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        except Exception as e:  # noqa: BLE001
            raise
    raise last


def _norm_authors(authors):
    return [a.strip() for a in authors if a and a.strip()]


# --------------------------------------------------------------------------
# arXiv
# --------------------------------------------------------------------------
def harvest_arxiv(queries, max_each=40, year_from=2015):
    out, seen = [], set()
    for q in queries:
        try:
            url = (
                "http://export.arxiv.org/api/query?search_query="
                + urllib.parse.quote(q)
                + f"&sortBy=submittedDate&sortOrder=descending&max_results={max_each}"
            )
            data = _get(url)
            root = ET.fromstring(data)
            for e in root.findall("atom:entry", NS):
                title = " ".join(
                    e.findtext("atom:title", default="", namespaces=NS).split()
                )
                y = e.findtext("atom:published", default="", namespaces=NS)[:4]
                url_id = e.findtext("atom:id", default="", namespaces=NS)
                authors = [
                    a.findtext("atom:name", default="", namespaces=NS)
                    for a in e.findall("atom:author", namespaces=NS)
                ]
                m = re.search(r"abs/([0-9]+\.[0-9]+)", url_id or "")
                arxiv_id = m.group(1) if m else None
                year = int(y) if y.isdigit() else None
                if not title or norm_title(title) in seen:
                    continue
                seen.add(norm_title(title))
                out.append({
                    "title": title,
                    "authors": _norm_authors(authors),
                    "year": year,
                    "venue": "arXiv",
                    "url": url_id,
                    "arxiv_id": arxiv_id,
                    "source": "arxiv",
                })
        except Exception as ex:
            print(f"[harvest:arxiv] {q!r} failed: {ex!r}")
    return out


# --------------------------------------------------------------------------
# DBLP
# --------------------------------------------------------------------------
def harvest_dblp(queries, max_each=50, year_from=2015):
    out, seen = [], set()
    for q in queries:
        try:
            url = (
                "https://dblp.org/search/publ/api?q="
                + urllib.parse.quote(q)
                + f"&format=json&h={max_each}"
            )
            data = json.loads(_get(url))
            hits = data.get("result", {}).get("hits", {}).get("hit", [])
            for h in hits:
                info = h.get("info", {})
                title = " ".join(str(info.get("title", "")).split())
                if not title or norm_title(title) in seen:
                    continue
                authors_raw = info.get("authors", {}).get("author", [])
                if isinstance(authors_raw, dict):
                    authors_raw = [authors_raw]
                authors = [a.get("text", "") for a in authors_raw]
                year = info.get("year")
                if isinstance(year, str) and year.isdigit():
                    year = int(year)
                else:
                    year = None
                venue = info.get("venue", "") or ""
                ee = info.get("ee") or info.get("url") or ""
                m = re.search(r"arxiv\.org/abs/([0-9]+\.[0-9]+)", str(ee))
                arxiv_id = m.group(1) if m else None
                if year and year < int(year_from):
                    continue
                seen.add(norm_title(title))
                out.append({
                    "title": title,
                    "authors": _norm_authors(authors),
                    "year": year,
                    "venue": venue,
                    "url": ee,
                    "arxiv_id": arxiv_id,
                    "source": "dblp",
                })
        except Exception as ex:
            print(f"[harvest:dblp] {q!r} failed: {ex!r}")
    return out


# --------------------------------------------------------------------------
# Semantic Scholar
# --------------------------------------------------------------------------
def harvest_s2(queries, max_each=50, year_from=2015):
    out, seen = [], set()
    for q in queries:
        try:
            url = (
                "https://api.semanticscholar.org/graph/v1/paper/search"
                "?query=" + urllib.parse.quote(q)
                + "&fields=title,authors,year,venue,externalIds,url"
                + f"&limit={max_each}"
            )
            data = json.loads(_get(url))
            for p in data.get("data", []):
                title = " ".join(str(p.get("title", "")).split())
                if not title or norm_title(title) in seen:
                    continue
                authors = [a.get("name", "") for a in p.get("authors", [])]
                year = p.get("year")
                venue = p.get("venue") or ""
                ext = p.get("externalIds") or {}
                arxiv_id = ext.get("ArXiv")
                doi = ext.get("DOI")
                url = p.get("url") or (
                    f"https://doi.org/{doi}" if doi else (
                        f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "")
                )
                if year and year < int(year_from):
                    continue
                seen.add(norm_title(title))
                out.append({
                    "title": title,
                    "authors": _norm_authors(authors),
                    "year": year,
                    "venue": venue,
                    "url": url,
                    "arxiv_id": arxiv_id,
                    "source": "s2",
                })
        except Exception as ex:
            print(f"[harvest:s2] {q!r} failed: {ex!r}")
    return out


# --------------------------------------------------------------------------
# Crossref
# --------------------------------------------------------------------------
def harvest_crossref(queries, max_each=30, year_from=2015):
    out, seen = [], set()
    for q in queries:
        try:
            url = (
                "https://api.crossref.org/works?query="
                + urllib.parse.quote(q)
                + f"&filter=from-pub-date:{year_from}-01-01&rows={max_each}"
            )
            data = json.loads(_get(url))
            for item in data.get("message", {}).get("items", []):
                title = item.get("title")
                if not title:
                    continue
                title = " ".join(str(title[0]).split())
                if not title or norm_title(title) in seen:
                    continue
                authors = [
                    (a.get("given", "") + " " + a.get("family", "")).strip()
                    for a in item.get("author", [])
                ]
                year = None
                for fld in ("published", "issued", "published-print"):
                    parts = item.get(fld, {}).get("date-parts", [[None]])
                    if parts and parts[0] and parts[0][0]:
                        year = parts[0][0]
                        break
                venue = " ".join(item.get("container-title", [])) if item.get("container-title") else ""
                doi = item.get("DOI")
                url = f"https://doi.org/{doi}" if doi else item.get("URL", "")
                m = re.search(r"arxiv\.org/abs/([0-9]+\.[0-9]+)", url or "")
                arxiv_id = m.group(1) if m else None
                if year and isinstance(year, int) and year < int(year_from):
                    continue
                seen.add(norm_title(title))
                out.append({
                    "title": title,
                    "authors": _norm_authors(authors),
                    "year": year,
                    "venue": venue,
                    "url": url,
                    "arxiv_id": arxiv_id,
                    "source": "crossref",
                })
        except Exception as ex:
            print(f"[harvest:crossref] {q!r} failed: {ex!r}")
    return out


def harvest(config):
    q = config.get("queries", {})
    yf = config.get("year_from", 2015)
    out = []
    out += harvest_arxiv(q.get("arxiv", []), year_from=yf)
    out += harvest_dblp(q.get("dblp", []), year_from=yf)
    out += harvest_s2(q.get("s2", []), year_from=yf)
    out += harvest_crossref(q.get("crossref", []), year_from=yf)
    return out
