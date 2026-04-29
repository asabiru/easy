"""Company mapper — finds the company referenced in a news text.

Strategy: keyword scoring per company. Each match contributes weight based on
keyword length (longer = more specific). Ties → highest match count wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.companies.universe import Company, load_universe


@dataclass
class CompanyMatch:
    company: Company
    score: float
    matched_keywords: list[str]


def _kw_pattern(keyword: str) -> re.Pattern[str]:
    # Word boundaries; case-insensitive. For multi-word keywords we use \b on edges.
    return re.compile(r"\b" + re.escape(keyword.lower()) + r"\b", re.IGNORECASE)


def find_company(normalized_text: str, universe: list[Company] | None = None) -> CompanyMatch | None:
    """Return the best-matching Company or None."""
    if not normalized_text:
        return None
    universe = universe or load_universe()

    best: CompanyMatch | None = None
    text = normalized_text.lower()

    for company in universe:
        score = 0.0
        matched: list[str] = []
        for kw in company.keywords:
            if not kw:
                continue
            if _kw_pattern(kw).search(text):
                # weight: longer keywords are more specific
                score += max(1.0, len(kw) / 4.0)
                matched.append(kw)
        # extra weight if the ticker appears as standalone token
        if _kw_pattern(company.ticker).search(text):
            score += 3.0

        if score > 0 and (best is None or score > best.score):
            best = CompanyMatch(company=company, score=score, matched_keywords=matched)

    return best
