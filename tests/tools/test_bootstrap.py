import importlib.util
import json
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts" / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
sys.modules["bootstrap"] = bootstrap
spec.loader.exec_module(bootstrap)

SECRETS = {
    "MM_RABBIT_PROVISION_PASSWORD": "plain-provision",
    "MM_RABBIT_PUBLISHER_PASSWORD": "plain-publisher",
    "MM_RABBIT_CONSUMER_MOCK_PASSWORD": "plain-mock",
    "MM_RABBIT_CONSUMER_VALIDATE_PASSWORD": "plain-validate",
    "MM_RABBIT_CONSUMER_WAN_PASSWORD": "plain-wan",
    "MM_RABBIT_MONITOR_PASSWORD": "plain-monitor",
    "MM_RABBIT_CONSUMER_TIMEOUT_MS": "21600000",
}


def assert_owner_only(path: Path) -> None:
    """Native Windows has no POSIX modes. The stack runs there inside WSL2, where they apply and this is checked."""
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_env_file_is_created_owner_only(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    bootstrap.write_private(target, "SECRET=value\n")
    assert_owner_only(target)
    assert target.read_text(encoding="utf-8") == "SECRET=value\n"


def test_write_private_tightens_an_existing_file(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o644)
    bootstrap.write_private(target, "new")
    assert_owner_only(target)
    assert target.read_text(encoding="utf-8") == "new"


def test_rabbit_definitions_hold_hashes_not_plaintext() -> None:
    template = (ROOT / "configs/rabbitmq/definitions.template.json").read_text(encoding="utf-8")
    rendered = bootstrap.render_template(template, SECRETS)
    assert not any(value in rendered for key, value in SECRETS.items() if "PASSWORD" in key)
    assert json.loads(rendered)["users"][0]["hashing_algorithm"] == "rabbit_password_hashing_sha256"


def test_no_other_template_is_rendered_to_disk() -> None:
    assert [source for source, _ in bootstrap.DISK_RENDERED] == ["configs/rabbitmq/definitions.template.json"]


def test_an_existing_env_gains_new_settings_and_keeps_its_secrets() -> None:
    example = "# comment\nKEEP=__GENERATE__\nNEW_SECRET=__GENERATE__\nNEW_PLAIN=value\n"
    added = bootstrap.missing_entries(example, "KEEP=my-existing-secret\n", "linux/arm64")
    names = [line.partition("=")[0] for line in added.splitlines()]
    assert names == ["NEW_SECRET", "NEW_PLAIN"]
    assert "my-existing-secret" not in added
    assert "__GENERATE__" not in added, "a new secret must be generated, not left as the placeholder"
    assert "NEW_PLAIN=value" in added


def test_an_up_to_date_env_gets_nothing_added() -> None:
    assert bootstrap.missing_entries("A=1\nB=2\n", "A=9\nB=8\n", "linux/arm64") == ""
