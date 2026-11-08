from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("render_definitions", ROOT / "scripts" / "render_definitions.py")
assert spec and spec.loader
render_definitions = importlib.util.module_from_spec(spec)
sys.modules["render_definitions"] = render_definitions
spec.loader.exec_module(render_definitions)

VALUES = {
    "MM_RABBIT_PUBLISHER_PASSWORD": "plain-publisher",
    "MM_RABBIT_CONSUMER_WAN_PASSWORD": "plain-wan",
    "MM_RABBIT_CONSUMER_VALIDATE_PASSWORD": "plain-validate",
    "MM_RABBIT_MONITOR_PASSWORD": "plain-monitor",
    "MM_RABBIT_CONSUMER_TIMEOUT_MS": "21600000",
}


def assert_owner_only(path: Path) -> None:
    """Native Windows has no POSIX modes. The stack runs there inside WSL2, where they apply and this is checked."""
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def rendered() -> dict:
    return render_definitions.render(render_definitions.TEMPLATE.read_text(encoding="utf-8"), VALUES)


def test_production_has_no_mock_queues_or_accounts() -> None:
    definitions = rendered()
    assert not [queue["name"] for queue in definitions["queues"] if ".mock." in queue["name"]]
    assert not [binding for binding in definitions["bindings"] if ".mock." in binding["destination"]]
    assert {user["name"] for user in definitions["users"]} == {
        "mm-publisher",
        "mm-consumer-wan",
        "mm-consumer-validate",
        "mm-monitor",
    }


def test_the_real_queues_and_their_dead_letters_remain() -> None:
    queues = {queue["name"] for queue in rendered()["queues"]}
    assert {
        "motionmirror.validate.v1",
        "motionmirror.preprocess.v1",
        "motionmirror.generate.wan-animate.v1",
        "motionmirror.dead.validate.v1",
        "motionmirror.dead.preprocess.v1",
        "motionmirror.dead.generate.wan-animate.v1",
    } == queues


def test_passwords_appear_only_as_hashes() -> None:
    text = json.dumps(rendered())
    for value in VALUES.values():
        if value.startswith("plain"):
            assert value not in text


def test_long_gpu_jobs_get_their_timeout_as_a_policy() -> None:
    """Amazon MQ's default is 30 minutes. This policy is what lets a job run for hours."""
    policies = rendered()["policies"]
    timeouts = [policy["definition"]["consumer-timeout"] for policy in policies if "consumer-timeout" in policy["definition"]]
    assert timeouts == [21600000]


def test_a_missing_password_is_a_clear_refusal(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / "production.env"
    env.write_text("MM_RABBIT_CONSUMER_TIMEOUT_MS=1\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--env-file", str(env), "--out", str(tmp_path / "out.json")])
    assert render_definitions.main() == 2
    assert "doesn't set" in capsys.readouterr().err
    assert not (tmp_path / "out.json").exists()


def test_the_output_file_is_private(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / "production.env"
    env.write_text("".join(f"{key}={value}\n" for key, value in VALUES.items()), encoding="utf-8")
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["x", "--env-file", str(env), "--out", str(out)])
    assert render_definitions.main() == 0
    assert_owner_only(out)
