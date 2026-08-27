# -*- coding: utf-8 -*-
"""Offline unit tests for clean_papers.py (no network; Crossref is mocked).

Run from a repo root, e.g.:
    python scripts/test_clean_papers.py
"""
import os
import sys
import json
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import clean_papers as cp  # noqa: E402


def fake_get(payload):
    """Return a callable standing in for harvest._get that yields `payload`."""
    def _get(url, timeout=25):
        return json.dumps(payload)
    return _get


class TestYearFromDoi(unittest.TestCase):
    def test_ieee_conference(self):
        self.assertEqual(cp._year_from_doi("10.1109/iccv51701.2025.00948"), 2025)

    def test_ieee_journal_online_first(self):
        # IEEE SPL 2024: Crossref `issued` can be 2026 (online-first) but the
        # volume year embedded in the DOI is 2024.
        self.assertEqual(cp._year_from_doi("10.1109/lsp.2024.3425283"), 2024)

    def test_eurographics(self):
        self.assertEqual(cp._year_from_doi("10.2312/egs.20191002"), 2019)

    def test_no_year_in_doi(self):
        self.assertIsNone(cp._year_from_doi("10.1007/978-3-031-72825-4_20"))

    def test_none(self):
        self.assertIsNone(cp._year_from_doi(None))


class TestIeeeFromDoi(unittest.TestCase):
    def test_uppercases_conference(self):
        self.assertEqual(cp._ieee_from_doi("10.1109/iccv51701.2025.00948"), "ICCV")

    def test_journal_maps_to_ieee_spl(self):
        self.assertEqual(cp._ieee_from_doi("10.1109/lsp.2024.3425283"), "IEEE SPL")

    def test_none(self):
        self.assertIsNone(cp._ieee_from_doi("10.2312/egs.20191002"))


class TestShortVenue(unittest.TestCase):
    def test_iclr(self):
        self.assertEqual(
            cp._short_venue("International Conference on Learning Representations"),
            "ICLR")

    def test_acl(self):
        self.assertEqual(
            cp._short_venue("International Conference on Computational Linguistics"),
            "ACL")

    def test_strips_trailing_year(self):
        # Year-stripping happens in enrich_venues after _short_venue.
        import re
        self.assertEqual(
            re.sub(r"\s+(?:19|20)\d{2}$", "", cp._short_venue("NeurIPS 2025")),
            "NeurIPS")

    def test_eccv_hyphen_container_title(self):
        # Crossref returns "Computer Vision - ECCV 2024" (hyphen), not en-dash.
        self.assertEqual(cp._short_venue("Computer Vision - ECCV 2024"), "ECCV")


class TestCrossrefMeta(unittest.TestCase):
    def test_eccv_lncs(self):
        payload = {"message": {
            "container-title": [
                "Lecture Notes in Computer Science",
                "Computer Vision - ECCV 2024",
            ],
            "issued": {"date-parts": [[2024, 9, 1]]},
        }}
        cp.harvest._get = fake_get(payload)
        v, y = cp._crossref_meta("10.1007/978-3-031-72825-4_20", {})
        self.assertEqual(cp._short_venue(v), "ECCV")
        self.assertEqual(y, 2024)

    def test_aaai_event_acronym(self):
        payload = {"message": {
            "event": {"acronym": "AAAI-26"},
            "issued": {"date-parts": [[2026]]},
        }}
        cp.harvest._get = fake_get(payload)
        v, y = cp._crossref_meta("10.1609/aaai.v40i21.38865", {})
        self.assertEqual(v, "AAAI-26")
        self.assertEqual(y, 2026)


class TestEnrichVenues(unittest.TestCase):
    def test_preserves_manual_fix(self):
        papers = [{
            "title": "X", "venue": "CVPR", "year": 2025,
            "url": "https://arxiv.org/abs/1234.5678", "arxiv_id": "1234.5678",
        }]
        # No network: paper is fully populated -> skipped.
        cp.enrich_venues(papers)
        self.assertEqual(papers[0]["venue"], "CVPR")
        self.assertEqual(papers[0]["year"], 2025)

    def test_fills_missing_year_from_ieee_doi(self):
        papers = [{
            "title": "X", "venue": "", "year": None,
            "url": "https://doi.org/10.1109/iccv51701.2025.00948",
        }]
        cp.enrich_venues(papers)
        self.assertEqual(papers[0]["venue"], "ICCV")
        self.assertEqual(papers[0]["year"], 2025)

    def test_openreview_url_never_becomes_venue(self):
        # A paper whose ONLY link is OpenReview and which has no DOI.
        papers = [{
            "title": "Some ICLR Paper",
            "url": "https://openreview.net/forum?id=abc123",
            "venue": "", "year": None,
        }]
        payload = {"message": {"items": [{
            "container-title": [
                "International Conference on Learning Representations"],
            "issued": {"date-parts": [[2025]]},
        }]}}
        cp.harvest._get = fake_get(payload)
        cp.enrich_venues(papers)
        # Venue must come from Crossref title match, never from the URL host.
        self.assertNotEqual(papers[0]["venue"].lower(), "openreview")
        self.assertEqual(papers[0]["venue"], "ICLR")
        self.assertEqual(papers[0]["year"], 2025)


if __name__ == "__main__":
    unittest.main(verbosity=2)
