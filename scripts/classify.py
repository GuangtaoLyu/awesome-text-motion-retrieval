# -*- coding: utf-8 -*-
"""Per-repo classifier for awesome-text-motion-retrieval.

Rule order matters. Falls back to config default (Text-Motion Retrieval).
"""
from lib_common import classify_by_rules

RULES = [
    ("Surveys", ["survey", "review of", "a review"]),
    ("Datasets & Benchmarks", ["dataset", "benchmark", "benchmarking"]),
    ("Motion Grounding",
     ["grounding", "temporal grounding", "moment retrieval", "localization",
      "text-guided localization", "text-based localization", "spatio-temporal grounding"]),
    ("Retrieval-Augmented & Generation-Oriented",
     ["retrieval-augmented", "retrieval augmented", "retrieval for generation",
      "retrieval-based generation", "retrieval enhanced", "retrieval-enhanced",
      "retrieval-conditioned", "rag"]),
    ("Text-Motion Retrieval",
     ["retrieval", "text-to-motion", "text motion", "cross-modal retrieval",
      "text-motion matching", "text-motion alignment", "motion-text"]),
]

DEFAULT = "Text-Motion Retrieval"


def classify(paper, config=None):
    return classify_by_rules(paper, RULES, DEFAULT)
