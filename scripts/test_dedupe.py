# -*- coding: utf-8 -*-
"""Regression tests for the duplicate-consolidation logic.

The motivating bug: the 2026-08-31 auto-update added ~140 duplicate entries
because `pipeline.dedup_key()` included the url, so the same paper harvested
from arXiv / DBLP / Semantic Scholar / Crossref looked like four new papers.
These tests pin the canonical keying AND the guard rails that stop the fuzzy
matcher from eating genuinely different papers.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib_common import title_key, arxiv_key, arxiv_year, fix_year, tidy_title  # noqa: E402
import dedupe  # noqa: E402


def P(title, **kw):
    p = {
        "title": title,
        "authors": [],
        "year": 2024,
        "venue": "arXiv",
        "url": "https://example.org/x",
        "arxiv_id": None,
        "source": "s2",
    }
    p.update(kw)
    return p


class TestCanonicalKeys(unittest.TestCase):

    def test_title_key_ignores_case_punct_and_leading_article(self):
        self.assertEqual(
            title_key("A Semantic Belief-State World Model for 3D HMP"),
            title_key("Semantic Belief-State World Model for 3D HMP"),
        )

    def test_arxiv_key_strips_version(self):
        self.assertEqual(arxiv_key(P("t", arxiv_id="2501.06035v3")), "2501.06035")

    def test_arxiv_key_from_abs_url(self):
        self.assertEqual(
            arxiv_key(P("t", url="https://arxiv.org/abs/2501.06035v2")),
            "2501.06035",
        )

    def test_arxiv_key_from_pdf_url(self):
        self.assertEqual(
            arxiv_key(P("t", url="https://arxiv.org/pdf/2501.06035")), "2501.06035")

    def test_arxiv_key_from_crossref_doi(self):
        # Crossref writes arXiv preprints as 10.48550/arXiv.NNNN.NNNNN.
        self.assertEqual(
            arxiv_key(P("t", url="https://doi.org/10.48550/arXiv.2501.06035")),
            "2501.06035",
        )

    def test_no_arxiv(self):
        self.assertEqual(arxiv_key(P("t")), "")


class TestMergeDuplicates(unittest.TestCase):

    def test_same_title_different_links_merges(self):
        ps = [
            P("Nonisotropic Gaussian Diffusion", arxiv_id="2501.06035",
              url="https://arxiv.org/abs/2501.06035", source="seed"),
            P("Nonisotropic Gaussian Diffusion", venue="CVPR",
              url="https://www.semanticscholar.org/paper/abc", source="s2"),
        ]
        kept, removed = dedupe.consolidate(ps)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(removed), 1)
        # metadata merged: arXiv link survives, real venue survives
        self.assertEqual(kept[0]["arxiv_id"], "2501.06035")
        self.assertEqual(kept[0]["venue"], "CVPR")

    def test_same_arxiv_id_merges(self):
        ps = [
            P("Alpha Beta", arxiv_id="2501.06035"),
            P("Alpha Beta", arxiv_id="2501.06035v2", venue="ICML"),
        ]
        kept, _ = dedupe.consolidate(ps)
        self.assertEqual(len(kept), 1)

    def test_leading_article_merges(self):
        ps = [
            P("A Human-Following Motion Planning Scheme", authors=["Khawaja"]),
            P("Human-Following Motion Planning Scheme", authors=["Khawaja"]),
        ]
        kept, _ = dedupe.consolidate(ps)
        self.assertEqual(len(kept), 1)

    def test_dblp_trailing_period_merges(self):
        ps = [
            P("MoCHA: Denoising Caption Supervision", arxiv_id="2603.23684"),
            P("MoCHA: Denoising Caption Supervision.", venue="CoRR",
              arxiv_id="2603.23684"),
        ]
        kept, _ = dedupe.consolidate(ps)
        self.assertEqual(len(kept), 1)
        self.assertFalse(kept[0]["title"].endswith("."))


class TestNoFalsePositives(unittest.TestCase):
    """Different papers that the fuzzy matcher must NOT merge."""

    def test_subset_title_different_authors_and_year(self):
        # Real case: Aksan 2020 (3DV) vs Yu 2023 (IEEE TCSVT). The shorter
        # title's tokens are a strict subset of the longer one, so pure
        # containment would merge them. The author/year guard must block it.
        ps = [
            P("A Spatio-temporal Transformer for 3D Human Motion Prediction",
              authors=["Emre Aksan", "Peng Cao"], year=2020),
            P("Toward Realistic 3D Human Motion Prediction With a "
              "Spatio-Temporal Cross-Transformer Approach",
              authors=["Hua Yu", "Xuanzhe Fan"], year=2023),
        ]
        kept, _ = dedupe.consolidate(ps)
        self.assertEqual(len(kept), 2)

    def test_survey_not_swallowed(self):
        ps = [
            P("3D Human Motion Prediction: A Survey", authors=["A"], year=2023),
            P("Human Motion Prediction", authors=["B"], year=2023),
        ]
        kept, _ = dedupe.consolidate(ps)
        self.assertEqual(len(kept), 2)

    def test_year_gap_blocks_containment(self):
        ps = [
            P("Learning Graph Networks for Motion", authors=["A"], year=2018),
            P("Deep Learning Graph Networks for Motion Modelling",
              authors=["A"], year=2026),
        ]
        kept, _ = dedupe.consolidate(ps)
        self.assertEqual(len(kept), 2)


class TestYearRepair(unittest.TestCase):
    """arXiv ids are YYMM.NNNNN -- '2005.05751' is 2020-05, not the year 2005."""

    def test_arxiv_year(self):
        self.assertEqual(arxiv_year(P("t", arxiv_id="2005.05751")), 2020)
        self.assertEqual(arxiv_year(P("t", arxiv_id="2501.06035")), 2025)
        self.assertEqual(arxiv_year(P("t", arxiv_id="9107.00001")), 1991)

    def test_fix_year_repairs_scraped_year(self):
        p = fix_year(P("Unpaired Motion Style Transfer", arxiv_id="2005.05751",
                       year=2005))
        self.assertEqual(p["year"], 2020)

    def test_fix_year_leaves_good_year(self):
        p = fix_year(P("t", arxiv_id="2501.06035", year=2026))
        self.assertEqual(p["year"], 2026)

    def test_fix_year_noop_without_arxiv(self):
        p = fix_year(P("Style Translation for Human Motion", year=2005))
        self.assertEqual(p["year"], 2005)   # legitimately old, must survive


class TestTidyTitle(unittest.TestCase):

    def test_strips_trailing_period(self):
        self.assertEqual(tidy_title("Some Title."), "Some Title")

    def test_keeps_internal_periods(self):
        self.assertEqual(tidy_title("MoCHA 2.0: A Study"), "MoCHA 2.0: A Study")

    def test_empty(self):
        self.assertEqual(tidy_title(""), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
