"""Config loading and validation (spec section 51)."""

from pathlib import Path

import pytest

from market_monitor.config import Config, ConfigError, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_shipped_config_is_valid():
    config = load_config(REPO_ROOT / "config" / "config.yaml")
    assert config.app.timezone == "America/Toronto"
    assert config.schedule.day_of_week == "sunday"
    assert config.schedule.time == "10:00"
    assert config.earnings.lookahead_days == 30
    assert config.macro.events["CPI"].importance == 5


def test_valid_yaml_loads(tmp_path):
    path = write(
        tmp_path,
        """
app:
  timezone: "America/New_York"
schedule:
  day_of_week: "friday"
  time: "07:45"
earnings:
  symbols: ["nvda"]
""",
    )
    config = load_config(path)
    assert config.app.timezone == "America/New_York"
    assert config.schedule.cron_day_of_week == "fri"
    assert config.schedule.hour == 7
    assert config.schedule.minute == 45


def test_invalid_timezone_rejected(tmp_path):
    path = write(tmp_path, 'app:\n  timezone: "Mars/Olympus"\nearnings:\n  symbols: ["X"]\n')
    with pytest.raises(ConfigError, match="timezone"):
        load_config(path)


@pytest.mark.parametrize("value", ["10am", "25:00", "10:60", "1000", ""])
def test_invalid_time_rejected(tmp_path, value):
    path = write(
        tmp_path,
        'schedule:\n  time: "{}"\nearnings:\n  symbols: ["X"]\n'.format(value),
    )
    with pytest.raises(ConfigError, match="schedule.time"):
        load_config(path)


def test_invalid_day_rejected(tmp_path):
    path = write(
        tmp_path, 'schedule:\n  day_of_week: "someday"\nearnings:\n  symbols: ["X"]\n'
    )
    with pytest.raises(ConfigError, match="day_of_week"):
        load_config(path)


@pytest.mark.parametrize("importance", [0, 6, -1])
def test_invalid_importance_rejected(tmp_path, importance):
    path = write(
        tmp_path,
        "macro:\n  events:\n    CPI:\n      importance: {}\n"
        'earnings:\n  symbols: ["X"]\n'.format(importance),
    )
    with pytest.raises(ConfigError, match="importance"):
        load_config(path)


@pytest.mark.parametrize("days", [0, -5])
def test_non_positive_lookahead_rejected(tmp_path, days):
    path = write(
        tmp_path,
        'earnings:\n  symbols: ["X"]\n  lookahead_days: {}\n'.format(days),
    )
    with pytest.raises(ConfigError, match="lookahead_days"):
        load_config(path)


def test_lowercase_tickers_are_normalized(tmp_path):
    path = write(
        tmp_path, 'earnings:\n  symbols: ["nvda", " mu ", "Msft"]\n'
    )
    assert load_config(path).earnings.symbols == ["NVDA", "MU", "MSFT"]


def test_duplicate_tickers_collapse(tmp_path):
    path = write(tmp_path, 'earnings:\n  symbols: ["nvda", "NVDA", "nVdA"]\n')
    assert load_config(path).earnings.symbols == ["NVDA"]


def test_empty_symbols_rejected_when_earnings_enabled(tmp_path):
    path = write(tmp_path, "earnings:\n  enabled: true\n  symbols: []\n")
    with pytest.raises(ConfigError, match="symbols"):
        load_config(path)


def test_empty_symbols_allowed_when_earnings_disabled(tmp_path):
    path = write(tmp_path, "earnings:\n  enabled: false\n  symbols: []\n")
    assert load_config(path).earnings.symbols == []


def test_unknown_key_is_rejected(tmp_path):
    # A typo in YAML should be a loud error, not a silently ignored setting.
    path = write(
        tmp_path, 'earnings:\n  symbols: ["X"]\n  lookahead_dayz: 30\n'
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_file_reports_path(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_malformed_yaml_reports_clearly(tmp_path):
    path = write(tmp_path, "app:\n  timezone: [unclosed\n")
    with pytest.raises(ConfigError, match="Could not parse YAML"):
        load_config(path)


def test_defaults_match_the_spec():
    config = Config()
    assert config.app.timezone == "America/Toronto"
    assert config.schedule.day_of_week == "sunday"
    assert config.schedule.time == "10:00"
    assert config.earnings.lookahead_days == 30
    assert config.macro.minimum_importance == 3
