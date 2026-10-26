"""Tests for YAML config loading and validation."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from agent_estimate.adapters import config_loader
from agent_estimate.adapters.config_loader import load_config, load_default_config

VALID_CONFIG = """\
agents:
  - name: Claude
    capabilities:
      - planning
      - implementation
    parallelism: 2
    cost_per_turn: 0.12
    model_tier: frontier
settings:
  friction_multiplier: 1.1
  inter_wave_overhead: 0.25
  metr_fallback_threshold: 40.0
"""


@pytest.fixture(autouse=True)
def _disable_real_entry_point_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_loader, "_iter_agent_entry_points", list)


def _write(tmp_path: Path, filename: str, content: str) -> Path:
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    return path


def test_load_config_valid_file_returns_estimation_config(tmp_path: Path) -> None:
    config_path = _write(tmp_path, "config.yaml", VALID_CONFIG)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        config = load_config(config_path)

    assert not captured
    assert config.settings.friction_multiplier == pytest.approx(1.1)
    assert "review_overhead" not in type(config.settings).model_fields
    assert len(config.agents) == 1
    assert config.agents[0].name == "Claude"
    assert config.agents[0].capabilities == ["planning", "implementation"]


@pytest.mark.parametrize("value", ["0", "0.15", "null", "false", "bad", "[]", "{}"])
def test_load_config_rejects_review_overhead_before_validation(tmp_path: Path, value: str) -> None:
    deprecated_config = VALID_CONFIG.replace(
        "  metr_fallback_threshold: 40.0\n",
        f"  review_overhead: {value}\n  metr_fallback_threshold: 40.0\n",
    )
    # An unrelated invalid field must not obscure the migration instruction.
    deprecated_config = deprecated_config.replace("friction_multiplier: 1.1", "friction_multiplier: -1")
    config_path = _write(tmp_path, "deprecated.yaml", deprecated_config)

    with pytest.raises(ValueError, match=r"settings\.review_overhead was removed; delete.*--review-mode"):
        load_config(config_path)


def test_load_config_malformed_yaml_has_parse_error(tmp_path: Path) -> None:
    malformed = """\
agents:
  - name: Claude
    capabilities: [planning, implementation
"""
    config_path = _write(tmp_path, "malformed.yaml", malformed)

    with pytest.raises(ValueError, match="Failed to parse YAML config"):
        load_config(config_path)


def test_load_config_unknown_key_is_rejected(tmp_path: Path) -> None:
    with_unknown_key = """\
agents:
  - name: Claude
    capabilities: [planning]
    parallelism: 2
    cost_per_turn: 0.12
    model_tier: frontier
settings:
  friction_multiplier: 1.1
  inter_wave_overhead: 0.25
  metr_fallback_threshold: 40.0
  extra_setting: true
"""
    config_path = _write(tmp_path, "unknown-key.yaml", with_unknown_key)

    with pytest.raises(ValueError, match="settings.extra_setting: Extra inputs are not permitted"):
        load_config(config_path)


def test_load_config_accepts_yaml_int_values_for_float_fields(tmp_path: Path) -> None:
    int_values = """\
agents:
  - name: Claude
    capabilities: [planning]
    parallelism: 2
    cost_per_turn: 0
    model_tier: frontier
settings:
  friction_multiplier: 1
  inter_wave_overhead: 0
  metr_fallback_threshold: 30
"""
    config_path = _write(tmp_path, "int-values.yaml", int_values)

    config = load_config(config_path)

    assert config.agents[0].cost_per_turn == pytest.approx(0.0)
    assert config.settings.friction_multiplier == pytest.approx(1.0)
    assert config.settings.metr_fallback_threshold == pytest.approx(30.0)


def test_load_default_config_has_expected_profiles() -> None:
    config = load_default_config()

    assert [agent.name for agent in config.agents] == ["Claude", "Codex"]


def test_entry_point_group_is_stable() -> None:
    assert config_loader.ENTRY_POINT_GROUP == "agent_estimate.agents"


class _FakeEntryPoint:
    def __init__(self, name: str, loaded: object) -> None:
        self.name = name
        self._loaded = loaded

    def load(self) -> object:
        return self._loaded


class _CodexPluginProfile:
    name = "Codex"
    capabilities = ("implementation", "debugging", "testing", "profiling")
    parallelism = 5
    cost_per_turn = 0.2
    model_tier = "gpt-5.3"

    def adjust_estimate(self, minutes: float) -> float:
        return minutes * 0.95


def test_load_config_merges_plugin_profiles_with_plugin_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_codex = """\
agents:
  - name: Codex
    capabilities: [implementation]
    parallelism: 1
    cost_per_turn: 0.08
    model_tier: production
settings:
  friction_multiplier: 1.1
  inter_wave_overhead: 0.25
  metr_fallback_threshold: 40.0
"""
    config_path = _write(tmp_path, "with-codex.yaml", with_codex)
    monkeypatch.setattr(
        config_loader,
        "_iter_agent_entry_points",
        lambda: [_FakeEntryPoint("codex_plugin", _CodexPluginProfile())],
    )

    config = load_config(config_path)

    assert len(config.agents) == 1
    assert config.agents[0].name == "Codex"
    assert config.agents[0].parallelism == 5
    assert config.agents[0].capabilities == [
        "implementation",
        "debugging",
        "testing",
        "profiling",
    ]
    assert config.agents[0].model_tier == "gpt-5.3"
    assert config.agents[0].adjust_estimate(100) == pytest.approx(95)
    copied = config.agents[0].model_copy(update={"model_tier": "other"})
    assert copied.adjust_estimate(100) == pytest.approx(95)
    assert "_adjustment_hook" not in copied.model_dump()


def test_load_config_discovers_callable_entry_point_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _plugin_profile_factory() -> dict[str, object]:
        return {
            "name": "Plugin",
            "capabilities": ["research", "synthesis", "review"],
            "parallelism": 2,
            "cost_per_turn": 0.09,
            "model_tier": "plugin-tier",
        }

    config_path = _write(tmp_path, "config.yaml", VALID_CONFIG)
    monkeypatch.setattr(
        config_loader,
        "_iter_agent_entry_points",
        lambda: [_FakeEntryPoint("plugin_agent", _plugin_profile_factory)],
    )

    config = load_config(config_path)

    assert [agent.name for agent in config.agents] == ["Claude", "Plugin"]
    assert config.agents[1].model_tier == "plugin-tier"


def test_load_config_skips_broken_entry_point_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _broken_profile_factory() -> dict[str, object]:
        raise RuntimeError("plugin exploded")

    config_path = _write(tmp_path, "config.yaml", VALID_CONFIG)
    monkeypatch.setattr(
        config_loader,
        "_iter_agent_entry_points",
        lambda: [_FakeEntryPoint("broken_plugin", _broken_profile_factory)],
    )

    with pytest.warns(RuntimeWarning, match="broken_plugin"):
        config = load_config(config_path)

    assert [agent.name for agent in config.agents] == ["Claude"]
