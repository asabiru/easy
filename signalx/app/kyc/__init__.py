"""KYC / AML module.

Architecture:

  app.kyc.providers.base.KycProvider     ← abstract interface
  app.kyc.providers.mock.MockProvider    ← default for tests + local dev
  app.kyc.providers.sumsub.SumsubAdapter ← real provider (used when
                                            settings.kyc_provider == 'sumsub'
                                            and SUMSUB_APP_TOKEN/SECRET are set)

  app.kyc.geo                            ← OFAC / EU / UN block list
  app.kyc.sanctions                      ← sanctions / PEP screening helpers
  app.kyc.deps.require_kyc()             ← FastAPI dependency that 403s
                                           if the caller is not 'approved'

Hard rule (CLAUDE.md G6):
  Any state-mutating endpoint that moves real money or unlocks live
  trading MUST go through `require_kyc()`. The default for new
  endpoints is to require KYC; opt out only with explicit comment.
"""
from app.kyc.deps import require_kyc

__all__ = ["require_kyc"]
