"""cb_double_low (E63): eligibility reuse and preset wiring, no network."""

import pandas as pd

from qtrade.presets import PRESETS


def test_preset_registered_observation_only():
    p = PRESETS["cb_double_low"]
    assert p.build is None and p.rules.market == "cn_cb"
    from qtrade.live.allocate import SLEEVES

    assert "cb_double_low" not in SLEEVES


def test_eligibility_is_research_frozen_spec():
    # live book must consume the research module's filter verbatim
    from qtrade.live.cb_book import R

    master = pd.DataFrame({
        "上市时间": ["2020-01-01", "2026-07-01", "2020-01-01", "2020-01-01"],
        "发行规模": [10.0, 10.0, 1.0, 10.0],
        "信用评级": ["AA", "AA", "AA", "BBB"],
    }, index=["ok", "too_new", "too_small", "bad_rating"])
    d = pd.Timestamp("2026-07-15")
    assert R.eligible(master, "ok", d, 110.0)
    assert not R.eligible(master, "too_new", d, 110.0)
    assert not R.eligible(master, "too_small", d, 110.0)
    assert not R.eligible(master, "bad_rating", d, 110.0)
    assert not R.eligible(master, "ok", d, 140.0)  # redemption-risk proxy
