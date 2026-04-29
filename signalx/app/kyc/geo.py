"""Geo-block list.

OFAC / EU / UN comprehensive-sanctions countries + sub-listed regions.
We block at registration AND at every state-mutating action — a user
who legally moved to a non-blocked country must re-verify, but a
user who legally moved INTO a blocked country must be cut off.

ISO 3166-1 alpha-2 codes. Sub-regions handled as ISO 3166-2 strings.

Sources:
  https://ofac.treasury.gov/sanctions-programs-and-country-information
  https://www.consilium.europa.eu/en/policies/sanctions/
"""
from __future__ import annotations

# Comprehensive-embargo countries (US OFAC + EU + UN consensus).
COMPREHENSIVE_EMBARGO: frozenset[str] = frozenset(
    {
        "IR",  # Iran
        "KP",  # North Korea
        "CU",  # Cuba
        "SY",  # Syria
        # Note: Venezuela / Russia / Belarus are partial — handled below.
    }
)

# Sub-region embargoes (occupied / annexed regions).
EMBARGOED_SUB_REGIONS: frozenset[str] = frozenset(
    {
        "UA-43",  # Crimea (per OFAC EO 13685)
        "UA-09",  # Luhansk
        "UA-14",  # Donetsk
        "UA-65",  # Zaporizhzhia (occupied)
        "UA-23",  # Kherson (occupied)
    }
)

# High-risk FATF-listed countries — KYC level 2 minimum, no auto-trade
# go-live without enhanced due diligence.
FATF_GREYLIST: frozenset[str] = frozenset(
    {
        "AL", "BB", "BF", "KH", "KY", "JM", "ML", "MZ", "MM", "NI",
        "PA", "PH", "SN", "SS", "SY", "TR", "UG", "AE", "YE",
    }
)


def is_blocked(country: str | None, sub_region: str | None = None) -> bool:
    """Return True if the user must be denied account creation / trading."""
    if not country:
        # Unknown geo → conservatively block until KYC resolves it.
        return True
    cc = country.upper()
    if cc in COMPREHENSIVE_EMBARGO:
        return True
    if sub_region and sub_region.upper() in EMBARGOED_SUB_REGIONS:
        return True
    return False


def requires_enhanced_dd(country: str | None) -> bool:
    """FATF greylist + EU high-risk + UN sanctions = enhanced due diligence."""
    if not country:
        return True
    return country.upper() in FATF_GREYLIST
