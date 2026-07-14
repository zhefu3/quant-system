"""Minimal factor-zoo registry: discover, filter, compute with sanity gates.

Deliberately smaller than the upstream Vibe-Trading registry (no config
system, no caching layers): walk the zoo subpackages, import each module,
collect ``(__alpha_meta__, compute)``. Computation output is gated — wrong
shape, +/-inf, or >95% NaN raises instead of silently feeding garbage to
downstream research.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

ZOO_PACKAGES = ["alpha101", "gtja191", "qlib158", "academic", "fundamental"]
MAX_NAN_FRAC = 0.95


@dataclass(frozen=True)
class Alpha:
    id: str
    meta: dict
    compute: Callable[[dict], pd.DataFrame]


class FactorRegistry:
    def __init__(self, packages: list[str] | None = None):
        self._alphas: dict[str, Alpha] = {}
        for pkg_name in packages or ZOO_PACKAGES:
            pkg = importlib.import_module(f"qtrade.factors.zoo.{pkg_name}")
            for info in pkgutil.iter_modules(pkg.__path__):
                mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
                meta = getattr(mod, "__alpha_meta__", None)
                fn = getattr(mod, "compute", None)
                if meta is None or fn is None:
                    continue
                self._alphas[meta["id"]] = Alpha(meta["id"], dict(meta), fn)

    def __len__(self) -> int:
        return len(self._alphas)

    def list(self, theme: str | None = None, universe: str | None = None,
             max_extras: int | None = 0) -> list[str]:
        """Alpha ids, optionally filtered. ``max_extras=0`` (default) keeps
        only OHLCV-computable alphas — no fundamental/sector extras needed."""
        out = []
        for a in self._alphas.values():
            if theme is not None and theme not in a.meta.get("theme", []):
                continue
            if universe is not None and universe not in a.meta.get("universe", []):
                continue
            if max_extras is not None:
                if len(a.meta.get("extras_required", [])) > max_extras:
                    continue
                if a.meta.get("requires_sector", False):
                    continue
            out.append(a.id)
        return sorted(out)

    def meta(self, alpha_id: str) -> dict:
        return dict(self._alphas[alpha_id].meta)

    def compute(self, alpha_id: str, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        a = self._alphas[alpha_id]
        missing = [c for c in a.meta.get("columns_required", []) if c not in panel]
        if missing:
            raise KeyError(f"{alpha_id}: panel missing columns {missing}")
        out = a.compute(panel)
        ref = panel["close"]
        if out.shape != ref.shape or not out.index.equals(ref.index):
            raise ValueError(f"{alpha_id}: output shape {out.shape} != close {ref.shape}")
        vals = out.to_numpy(dtype="float64", na_value=np.nan)
        if np.isinf(vals).any():
            raise ValueError(f"{alpha_id}: output contains +/-inf")
        nan_frac = float(np.isnan(vals).mean())
        if nan_frac > MAX_NAN_FRAC:
            raise ValueError(f"{alpha_id}: {nan_frac:.0%} NaN output")
        return out
