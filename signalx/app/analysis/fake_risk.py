"""fake_risk: 0..100 score expressing how likely a piece of news is fake or
manipulative. Higher = riskier. Risk engine consumes this:

  fake_risk >= 50  → action override SKIP
  fake_risk >= 30  → action override WATCH

Inputs come from `AuthorMeta` (X / Twitter / RSS source metadata) and the
already-computed market snapshot. The function is deliberately conservative —
when metadata is missing, neutral defaults are used so the score doesn't
amplify noise. All weights are tunable knobs."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Linguistic markers of "rumor without source" — when present in isolation
# (i.e. without an authoritative link) they push fake_risk up.
_RUMOR_PATTERNS = re.compile(
    r"\b(breaking|leaked|insider|exclusive rumor|unconfirmed|allegedly|unverified|sources tell us)\b",
    flags=re.IGNORECASE,
)

# Spam / pump markers that immediately push the score very high.
_PUMP_PATTERNS = re.compile(
    r"\b(100x|10x guaranteed|pump|to the moon|secret tip|insider tip|guaranteed return)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class AuthorMeta:
    """Reputation signal for the news author / source. All fields optional;
    plain RSS / press releases pass an empty AuthorMeta and get a neutral
    baseline."""

    handle: str | None = None
    verified: bool | None = None
    followers: int | None = None
    account_age_days: int | None = None
    has_authoritative_link: bool = False
    is_known_official: bool = False  # Came from data/x_sources.json tier=official
    is_known_press: bool = False     # tier=press / wire


def compute_fake_risk(
    *,
    text: str,
    author: AuthorMeta | None,
    confirmation_count: int = 1,
    pre_tweet_price_change_pct: float | None = None,
) -> int:
    """Return a fake_risk score in [0, 100].

    Args:
        text: normalized news text.
        author: author / source metadata (may be None for plain RSS).
        confirmation_count: how many distinct sources have published the same
            event/ticker pair within the cross-source window. 1 means single
            source (no confirmation), 2+ means corroborated.
        pre_tweet_price_change_pct: signed % move of the underlying instrument
            in the 30s window BEFORE the news was received. A large positive
            move into bullish news (or negative into bearish) suggests the
            market saw the news first → potential pump-and-dump.
    """

    score = 30  # neutral baseline; everything >0 means "non-zero suspicion"
    a = author or AuthorMeta()

    # Source reputation -------------------------------------------------------
    if a.is_known_official:
        score -= 15
    if a.is_known_press:
        score -= 12
    if a.verified is True:
        score -= 5
    if a.verified is False:
        score += 5

    # Account quality ---------------------------------------------------------
    if a.followers is not None:
        if a.followers < 500:
            score += 25
        elif a.followers < 5_000:
            score += 12
        elif a.followers < 50_000:
            score += 3
        else:
            score -= 3

    if a.account_age_days is not None:
        if a.account_age_days < 30:
            score += 20
        elif a.account_age_days < 180:
            score += 8

    # Linguistic markers ------------------------------------------------------
    if _PUMP_PATTERNS.search(text or ""):
        score += 50

    rumor_hit = bool(_RUMOR_PATTERNS.search(text or ""))
    if rumor_hit and not a.has_authoritative_link and not a.is_known_press:
        # "BREAKING ..." with no source link from a non-press handle is
        # the classic shape of a fake post.
        score += 18
    elif rumor_hit and a.has_authoritative_link:
        # Same urgency wording but with a citation is *less* suspicious than
        # the unsourced version, but still slightly above baseline.
        score += 5

    if a.has_authoritative_link:
        score -= 8

    # Cross-source confirmation ----------------------------------------------
    # Two or more independent sources reporting the same event meaningfully
    # reduce fake risk. We cap the discount so a single bot network can't
    # easily fake "many sources".
    if confirmation_count >= 3:
        score -= 25
    elif confirmation_count == 2:
        score -= 15

    # Pre-news price action --------------------------------------------------
    # If the price has already ripped >2% in the 30s window before the news
    # arrives, somebody likely traded the news ahead of the publication —
    # treat the post itself as suspect.
    if pre_tweet_price_change_pct is not None:
        if abs(pre_tweet_price_change_pct) >= 2.0:
            score += 20
        elif abs(pre_tweet_price_change_pct) >= 1.0:
            score += 8

    # Clamp -------------------------------------------------------------------
    return max(0, min(100, score))
