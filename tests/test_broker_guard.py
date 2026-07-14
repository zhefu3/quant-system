"""Structural account guard: fail-closed on every send path."""

import pytest

from qtrade.live.broker import check_account_uid


def test_send_without_pin_refuses():
    with pytest.raises(RuntimeError, match="QTRADE_OKX_ACCOUNT_UID"):
        check_account_uid(None, "12345", send=True)


def test_send_with_unreadable_uid_refuses():
    with pytest.raises(RuntimeError, match="did not report"):
        check_account_uid("12345", "", send=True)


def test_send_with_mismatch_refuses():
    with pytest.raises(RuntimeError, match="mismatch"):
        check_account_uid("12345", "99999", send=True)


def test_send_with_match_passes():
    assert "verified" in check_account_uid("12345", "12345", send=True)


def test_dry_run_allowed_unpinned():
    assert "dry-run allowed" in check_account_uid(None, "12345", send=False)
    assert "dry-run allowed" in check_account_uid(None, "", send=False)


def test_type_coercion_int_vs_str():
    assert "verified" in check_account_uid(12345, "12345", send=True)
