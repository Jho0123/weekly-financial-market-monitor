"""CLI entry points (spec section 22) and scheduler wiring (spec section 21)."""


from market_monitor.cli import main
from market_monitor.config import Config
from market_monitor.scheduler import build_scheduler

from .test_config import REPO_ROOT

SHIPPED = str(REPO_ROOT / "config" / "config.yaml")


def test_validate_config_succeeds_on_the_shipped_file(capsys):
    assert main(["--config", SHIPPED, "validate-config"]) == 0

    out = capsys.readouterr().out
    assert "Config OK" in out
    assert "America/Toronto" in out
    assert "sunday at 10:00" in out
    assert "NVDA" in out


def test_validate_config_reports_a_bad_file(tmp_path, capsys):
    bad = tmp_path / "config.yaml"
    bad.write_text('schedule:\n  time: "10am"\n', encoding="utf-8")

    assert main(["--config", str(bad), "validate-config"]) == 2
    assert "Invalid config" in capsys.readouterr().err


def test_missing_config_is_reported_not_raised(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "nope.yaml"), "validate-config"]) == 2
    assert "not found" in capsys.readouterr().err


def test_summary_without_a_stored_report(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(
        'storage:\n  path: "{}"\nearnings:\n  symbols: ["MU"]\n'
        "logging:\n  file: null\n".format(tmp_path / "db.sqlite"),
        encoding="utf-8",
    )
    assert main(["--config", str(config), "summary"]) == 1
    assert "No report stored yet" in capsys.readouterr().err


def test_every_documented_subcommand_is_registered():
    from market_monitor.cli import build_parser

    parser = build_parser()
    actions = [
        action
        for action in parser._actions
        if getattr(action, "choices", None) and hasattr(action.choices, "keys")
    ]
    commands = set(actions[0].choices)

    assert {
        "refresh",
        "fetch-macro",
        "fetch-earnings",
        "dashboard",
        "validate-config",
        "schedule",
        "summary",
    } <= commands


# ------------------------------------------------------------- scheduler

def test_scheduler_uses_the_configured_day_and_time():
    config = Config()
    scheduler = build_scheduler(config)

    job = scheduler.get_job("weekly_refresh")
    fields = {field.name: str(field) for field in job.trigger.fields}

    assert fields["day_of_week"] == "sun"
    assert fields["hour"] == "10"
    assert fields["minute"] == "0"
    assert str(job.trigger.timezone) == "America/Toronto"



def test_scheduler_follows_a_yaml_change():
    config = Config.model_validate(
        {
            "app": {"timezone": "America/New_York"},
            "schedule": {"day_of_week": "wednesday", "time": "06:15"},
            "earnings": {"symbols": ["MU"]},
        }
    )
    scheduler = build_scheduler(config)
    job = scheduler.get_job("weekly_refresh")
    fields = {field.name: str(field) for field in job.trigger.fields}

    assert fields["day_of_week"] == "wed"
    assert fields["hour"] == "6"
    assert fields["minute"] == "15"
    assert str(job.trigger.timezone) == "America/New_York"



def test_disabled_schedule_does_not_start(caplog):
    from market_monitor.scheduler import run_scheduler

    config = Config.model_validate(
        {"schedule": {"enabled": False}, "earnings": {"symbols": ["MU"]}}
    )
    run_scheduler(config)  # returns immediately rather than blocking

    assert any("nothing will run" in record.message for record in caplog.records)
