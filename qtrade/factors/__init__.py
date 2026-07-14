"""Cross-sectional factor zoo, ported from Vibe-Trading (MIT, 2026-07-14).

Provenance (see each subpackage's LICENSE.md for full attribution):
  - zoo/alpha101     Kakushadze "101 Formulaic Alphas" (2016)
  - zoo/gtja191      国泰君安 "191 个短周期交易型 alpha 因子" 研报 (2014)
  - zoo/qlib158      clean-room re-expression of Microsoft qlib Alpha158
  - zoo/academic     classic published factors (Fama-French, Carhart, BAB, ...)
  - zoo/fundamental  standard fundamental ratios

Every factor module exposes ``__alpha_meta__`` (id/theme/columns_required/...)
and ``compute(panel) -> DataFrame``, where ``panel`` maps column name to a
wide frame (index=date, columns=codes). Discovery goes through
:class:`~qtrade.factors.registry.FactorRegistry`.

Sole sanctioned use (E56 prereg): feature candidates for the E47 LightGBM
pipeline, gated by train-window prescreen with same-universe random controls.
These are public, heavily-arbitraged formulas — per E45's verdict they are
NOT tradeable as standalone signals at retail costs.
"""

from .registry import FactorRegistry

__all__ = ["FactorRegistry"]
