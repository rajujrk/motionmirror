"""Production settings can't be run here, so these catch what would otherwise only surface on the real deployment."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROD = yaml.safe_load((ROOT / "infra" / "compose" / "compose.prod.yaml").read_text())
SERVICES = PROD["services"]
APPLICATION = (ROOT / "apps" / "api" / "src" / "main" / "resources" / "application.yaml").read_text()

spec = importlib.util.spec_from_file_location("render_realm", ROOT / "scripts" / "render_realm.py")
assert spec and spec.loader
render_realm = importlib.util.module_from_spec(spec)
sys.modules["render_realm"] = render_realm
spec.loader.exec_module(render_realm)


def test_the_api_reads_every_variable_production_sets() -> None:
    """A misspelt name fails silently: the default wins, and a check that looks enforced isn't."""
    known = set(re.findall(r"MM_[A-Z0-9_]+", APPLICATION))
    assert not set(SERVICES["api"]["environment"]) - known


def test_production_requires_tls_to_the_broker_and_cache() -> None:
    environment = SERVICES["api"]["environment"]
    assert environment["MM_RABBIT_TLS"] == "true"
    assert environment["MM_REDIS_TLS"] == "true"
    assert environment["MM_DEPLOYMENT"] == "production"
    assert environment["MM_COOKIE_SECURE"] == "true"


def test_real_backends_and_no_mock_in_production() -> None:
    environment = SERVICES["api"]["environment"]
    assert environment["MM_GENERATE_BACKEND"] == "wan-animate"
    assert environment["MM_PREPROCESS_BACKEND"] == "real-preprocess"
    assert "worker-mock" not in SERVICES


def test_every_service_gives_up_what_it_doesnt_need() -> None:
    for name, service in SERVICES.items():
        assert service["cap_drop"] == ["ALL"], name
        assert not {"SYS_ADMIN", "NET_ADMIN", "ALL"} & set(service.get("cap_add", [])), name
        assert "no-new-privileges:true" in service["security_opt"], name
        assert service["logging"]["options"]["max-size"], f"{name} needs a log limit, or logs can fill the disk"


def test_only_identity_may_write_to_its_filesystem() -> None:
    for name, service in SERVICES.items():
        if name not in {"keycloak", "clamav"}:
            assert service["read_only"] is True, name


def test_only_the_proxy_publishes_ports() -> None:
    published = {name for name, service in SERVICES.items() if service.get("ports")}
    assert published == {"caddy"}


def test_images_are_named_by_digest_or_required_from_the_release() -> None:
    for name, service in SERVICES.items():
        image = service["image"]
        assert re.search(r"@sha256:[0-9a-f]{64}$", image) or image.startswith("${MM_"), f"{name}: {image}"


def test_no_development_values_reach_production() -> None:
    text = (ROOT / "infra" / "compose" / "compose.prod.yaml").read_text()
    assert "MM_DEV_" not in text
    assert "start-dev" not in text


def caddy_blocks() -> dict[str, str]:
    text = (ROOT / "infra" / "proxy" / "Caddyfile.prod").read_text()
    return dict(re.findall(r"\{\$(MM_\w+)\} \{\n(.*?)\n\}", text, flags=re.S))


def test_the_public_site_refuses_worker_and_operator_routes() -> None:
    site = caddy_blocks()["MM_APP_DOMAIN"]
    for path in ("/internal/*", "/actuator/*", "/auth/admin/*"):
        assert re.search(rf"handle {re.escape(path)} \{{\s*respond \"Not found\" 404", site), path
    assert "/v3/api-docs" not in site


def test_the_worker_host_serves_only_the_worker_api_and_token_endpoint() -> None:
    routes = re.findall(r"handle (\S+) \{", caddy_blocks()["MM_WORKER_DOMAIN"])
    assert routes == ["/internal/*", "/auth/realms/motionmirror/protocol/openid-connect/token"]
    assert "reverse_proxy web" not in caddy_blocks()["MM_WORKER_DOMAIN"]


def test_the_production_realm_has_no_test_accounts_or_mock_worker() -> None:
    template = json.loads((ROOT / "infra" / "identity" / "realm-template.json").read_text())
    realm = render_realm.production_realm(template)
    assert "users" not in realm
    assert {client["clientId"] for client in realm["clients"]} == {
        "motionmirror-web",
        "motionmirror-cli",
        "motionmirror-worker-wan",
        "motionmirror-worker-validate",
    }
    assert realm["sslRequired"] == "all"
    assert realm["registrationAllowed"] is False
    assert "MM_DEV_" not in json.dumps(realm)


def test_rendering_refuses_to_write_development_values(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bad = tmp_path / "template.json"
    bad.write_text(json.dumps({"clients": [], "smtpServer": {"password": "${MM_DEV_USER_PASSWORD}"}}))
    monkeypatch.setattr(render_realm, "TEMPLATE", bad)
    monkeypatch.setattr(sys, "argv", ["render_realm", "--out", str(tmp_path / "out.json")])
    assert render_realm.main() == 1
    assert not (tmp_path / "out.json").exists()


def test_the_dead_letter_alert_matches_the_real_queue_names() -> None:
    """An alert on a name that doesn't exist looks like monitoring and never fires."""
    alerts = yaml.safe_load((ROOT / "infra" / "observability" / "prometheus" / "alerts.yml").read_text())
    rule = next(rule for group in alerts["groups"] for rule in group["rules"] if rule["alert"] == "DeadLetterQueueNotEmpty")
    pattern = re.search(r'queue=~"(.+?)"', rule["expr"]).group(1).replace("\\\\", "\\")  # type: ignore[union-attr]
    definitions = re.sub(r"\$\{\w+\}", "0", (ROOT / "configs" / "rabbitmq" / "definitions.template.json").read_text())
    queues = [queue["name"] for queue in json.loads(definitions)["queues"]]
    dead = [name for name in queues if re.fullmatch(pattern, name)]
    assert dead, f"{pattern} matches none of {queues}"


def test_metrics_named_in_alerts_and_dashboards_are_ones_the_api_publishes() -> None:
    source = "".join(path.read_text() for path in (ROOT / "apps" / "api" / "src" / "main" / "java").rglob("*.java"))
    published = {name.replace(".", "_") for name in re.findall(r'"(mm\.[a-z.]+)"', source)}
    observability = ROOT / "infra" / "observability"
    used = set(re.findall(r"\bmm_[a-z_]+", (observability / "prometheus" / "alerts.yml").read_text()))
    used |= set(re.findall(r"\bmm_[a-z_]+", (observability / "grafana" / "dashboards" / "motionmirror.json").read_text()))
    for name in used:
        assert name.removesuffix("_total") in published, f"{name} isn't published by anything"


def test_uploads_are_scanned_on_the_cpu_host_in_production() -> None:
    worker = SERVICES["worker-validate"]["environment"]
    assert worker["MM_CLAMD_HOST"] == "clamav"
    assert worker["MM_QUEUES"] == "motionmirror.validate.v1"
    assert worker["MM_AMQP_URL"].startswith("amqps://")


def test_the_scanner_accepts_uploads_as_large_as_the_api_allows() -> None:
    """clamd's default stream limit is 25 MB. Past it clamd errors, and every larger upload would stay quarantined."""
    config = (ROOT / "infra" / "clamav" / "clamd.conf").read_text()
    limit = int(re.search(r"StreamMaxLength (\d+)M", config).group(1)) * 1024**2  # type: ignore[union-attr]
    default = re.search(r"MM_MAX_SOURCE_BYTES:(\d+)", APPLICATION)
    assert default, "the upload limit moved, so this check needs updating"
    assert limit >= int(default.group(1))


def test_the_validation_broker_account_can_only_touch_its_own_queue() -> None:
    definitions = json.loads(re.sub(r"\$\{\w+\}", "0", (ROOT / "configs" / "rabbitmq" / "definitions.template.json").read_text()))
    account = next(item for item in definitions["permissions"] if item["user"] == "mm-consumer-validate")
    assert account["configure"] == "^$"
    assert re.fullmatch(account["read"], "motionmirror.validate.v1")
    assert not re.fullmatch(account["read"], "motionmirror.preprocess.v1")
    assert not re.fullmatch(account["read"], "motionmirror.generate.wan-animate.v1")
    queues = {queue["name"] for queue in definitions["queues"]}
    assert {"motionmirror.validate.v1", "motionmirror.dead.validate.v1"} <= queues


def test_tool_state_stays_out_of_images_at_any_depth() -> None:
    """Editor and agent plugins write folders, including command history, into whichever directory they run in.

    In .dockerignore a bare name only matches at the top of the build context, so each needs the **/ form.
    """
    patterns = (ROOT / ".dockerignore").read_text().split()
    for folder in (".claude-flow", ".claude", ".remember"):
        assert f"**/{folder}" in patterns, folder
