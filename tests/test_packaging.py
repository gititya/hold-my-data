import os
import subprocess
import sys
import tomllib
from pathlib import Path

from holdmydata import paths
from holdmydata import model_fetch


ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_exposes_cli_and_pins_python():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert project["requires-python"] == ">=3.13,<3.14"
    assert project["scripts"]["hold-my-data"] == "holdmydata.cli:main"
    assert "presidio-analyzer==2.2.362" in project["dependencies"]
    assert "presidio-anonymizer==2.2.362" in project["dependencies"]
    assert "pyyaml==6.0.2" in project["dependencies"]
    assert "rich==15.0.0" in project["dependencies"]


def test_normal_cli_use_does_not_force_identity_setup(monkeypatch):
    from holdmydata import cli

    monkeypatch.setattr(cli.identity, "exists", lambda: False)
    monkeypatch.setattr(cli.identity, "run_onboarding", lambda: (_ for _ in ()).throw(
        AssertionError("normal redaction must not start identity setup")
    ))
    monkeypatch.setattr(cli, "cmd_text", lambda _args: 0)

    assert cli.main(["text", "--who", "everyone"]) == 0


def test_download_models_is_an_explicit_network_enabled_child(monkeypatch):
    from holdmydata import cli

    seen = {}

    class Result:
        returncode = 0

    def fake_run(command, env):
        seen["command"] = command
        seen["env"] = env
        return Result()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.main(["download-models", "--images-only"]) == 0
    assert seen["command"][-1] == "--images-only"
    assert seen["env"]["HOLDMYDATA_ALLOW_NETWORK"] == "1"


def test_default_model_dir_is_stable_across_working_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv(paths.MODEL_DIR_ENV, raising=False)

    assert paths.model_dir() == tmp_path / ".holdmydata" / "models"


def test_model_fetch_refuses_implicit_network_access(monkeypatch, capsys):
    monkeypatch.delenv(model_fetch.ALLOW, raising=False)

    assert model_fetch.main(["--text-only"]) == 2
    assert "refusing to download" in capsys.readouterr().err


def test_repository_fetch_script_uses_installed_module():
    env = dict(os.environ)
    env.pop(model_fetch.ALLOW, None)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fetch_models.py"), "--text-only"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "refusing to download" in result.stderr

