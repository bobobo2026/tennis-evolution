from __future__ import annotations

import pytest

from tennis_evolution import license as lic
from tennis_evolution.cli import free_tier_top, main

TEST_PUBLIC = "95c73731471fdd1ea261f94462e2b14eb53ad86beed139bf8acb2f1bf76427e1"
TEST_KEY = (
    "te1.eyJ2IjoxLCJwbGFuIjoicHJvIiwibGlkIjoidGVzdC0wMDAxIiwic3JjIjoidGVzdCIsImlhdCI6MTc5Mz"
    "AwMDAwMCwibmFtZSI6IkNJIn0.h20AFo8FcoL_Ny3dvgKdWNPP78Du802hUj3g4FnSd54V64wOa8pW_6wk8B6D"
    "flHcxZkD6abG4QdcoED-EoqVCA"
)


@pytest.fixture
def test_issuer(monkeypatch, tmp_path):
    monkeypatch.setattr(lic, "PUBLIC_KEY_HEX", TEST_PUBLIC)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("TENNIS_EVOLUTION_LICENSE", raising=False)


def test_rfc8032_vector() -> None:
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
        "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert lic.ed25519_verify(public, b"", signature)
    assert not lic.ed25519_verify(public, b"x", signature)


def test_parse_valid_key(test_issuer) -> None:
    parsed = lic.parse_key(TEST_KEY)
    assert parsed.plan == "pro"
    assert parsed.license_id == "test-0001"
    assert parsed.name == "CI"


@pytest.mark.parametrize(
    "key",
    ["", "hello", "te1.abc", TEST_KEY.replace("te1.", "te2."), TEST_KEY[:-4] + "AAAA"],
)
def test_parse_rejects_garbage(test_issuer, key: str) -> None:
    with pytest.raises(lic.LicenseError):
        lic.parse_key(key)


def test_real_issuer_rejects_test_key(monkeypatch) -> None:
    with pytest.raises(lic.LicenseError):
        lic.parse_key(TEST_KEY)


def test_activate_status_deactivate(test_issuer, capsys) -> None:
    assert lic.current() is None
    assert main(["license", "activate", TEST_KEY]) == 0
    assert lic.is_pro()
    assert main(["license"]) == 0
    assert '"tier": "pro"' in capsys.readouterr().out
    assert main(["license", "deactivate"]) == 0
    assert lic.current() is None


def test_env_var_licence(test_issuer, monkeypatch) -> None:
    monkeypatch.setenv("TENNIS_EVOLUTION_LICENSE", TEST_KEY)
    assert lic.is_pro()


def test_activate_rejects_bad_key(test_issuer) -> None:
    assert main(["license", "activate", "te1.nope.nope"]) == 1
    assert lic.current() is None


def test_free_tier_clamps_top() -> None:
    assert free_tier_top(3, pro=False) == 3
    assert free_tier_top(5, pro=False) == 5
    assert free_tier_top(0, pro=False) == lic.FREE_MAX_RALLIES
    assert free_tier_top(12, pro=False) == lic.FREE_MAX_RALLIES
    assert free_tier_top(0, pro=True) == 0
    assert free_tier_top(12, pro=True) == 12
