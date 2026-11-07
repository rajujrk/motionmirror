import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("doctor", ROOT / "scripts" / "doctor.py")
doctor = importlib.util.module_from_spec(spec)
sys.modules["doctor"] = doctor
spec.loader.exec_module(doctor)


def test_native_image_passes() -> None:
    assert doctor.emulation_problem("arm64", "arm64", "api") is None


def test_amd64_image_on_arm_host_is_reported_as_emulation() -> None:
    problem = doctor.emulation_problem("arm64", "amd64", "keycloak")
    assert problem is not None
    assert "keycloak" in problem
    assert "emulation" in problem


def test_normalize_arch_treats_aarch64_as_arm64() -> None:
    assert doctor.normalize_arch("aarch64") == "arm64"
    assert doctor.normalize_arch("x86_64") == "amd64"


def test_missing_ffmpeg_codec_is_reported() -> None:
    encoders = " V..... libx264   H.264\n A..... aac  AAC\n"
    assert doctor.missing_codecs(encoders) == []
    assert doctor.missing_codecs(" A..... aac  AAC\n") == ["libx264"]


def test_redaction_hides_credentials_in_urls() -> None:
    redacted = doctor.redact("amqp://mm-consumer-mock:s3cret@rabbitmq:5672/motionmirror")
    assert "s3cret" not in redacted
    assert "rabbitmq:5672" in redacted


def test_short_consumer_timeout_is_a_problem() -> None:
    assert doctor.deadline_problem(21_600_000, max_attempt_ms=14_400_000) is None
    assert "consumer" in doctor.deadline_problem(1_800_000, max_attempt_ms=14_400_000)


def test_report_fails_only_on_failures() -> None:
    report = doctor.Report()
    report.ok("a", "fine")
    report.warn("b", "not required")
    assert report.exit_code == 0
    report.fail("c", "broken")
    assert report.exit_code == 1


def test_native_service_on_a_host_port_is_a_conflict() -> None:
    owners = {55432: "postgres", 8088: "OrbStack", 55672: None}
    ports = {"postgres": 55432, "http": 8088, "rabbitmq": 55672}
    problems = doctor.port_conflicts(ports, owners.get)
    assert problems == ["postgres port 55432 is held by postgres"]
