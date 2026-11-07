from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = {path.name: path.read_text() for path in (ROOT / ".github" / "workflows").glob("*.yml")}


def test_every_action_is_pinned_to_a_full_commit_with_its_version_noted() -> None:
    for name, text in WORKFLOWS.items():
        for line in re.findall(r"uses: (.+)", text):
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40} # v[\d.]+", line), f"{name}: {line}"


def test_every_workflow_defaults_to_read_only() -> None:
    for name, text in WORKFLOWS.items():
        assert yaml.safe_load(text)["permissions"] == {"contents": "read"}, name


def test_only_the_release_job_can_write_or_sign() -> None:
    assert "packages: write" not in WORKFLOWS["ci.yml"]
    assert "id-token" not in WORKFLOWS["ci.yml"]
    assert "id-token" not in WORKFLOWS["gpu-acceptance.yml"]
    assert "id-token: write" in WORKFLOWS["release.yml"]


def test_gpu_spending_can_only_be_started_by_a_person_who_authorizes_it() -> None:
    workflow = yaml.safe_load(WORKFLOWS["gpu-acceptance.yml"])
    # PyYAML reads the bare key `on` as True.
    triggers = workflow[True]
    assert list(triggers) == ["workflow_dispatch"]
    assert "spend_authorization" in triggers["workflow_dispatch"]["inputs"]
    assert "inputs.spend_authorization ==" in workflow["jobs"]["acceptance"]["if"]


def test_routine_ci_never_touches_a_gpu_or_a_training_run() -> None:
    assert "train-lora" not in WORKFLOWS["ci.yml"]
    assert "smoke-gpu" not in WORKFLOWS["ci.yml"]
    assert "gpu" not in str(yaml.safe_load(WORKFLOWS["ci.yml"])["jobs"]["worker"]["runs-on"])


def test_commands_in_workflows_are_real_make_targets() -> None:
    makefile = (ROOT / "Makefile").read_text()
    targets = set(re.findall(r"^([a-z0-9-]+):", makefile, flags=re.M))
    for name, text in WORKFLOWS.items():
        for target in re.findall(r"\bmake ([a-z0-9-]+)", text):
            assert target in targets, f"{name} runs make {target}, which doesn't exist"


def test_every_make_target_named_in_the_docs_exists() -> None:
    """The brief asks for real commands. A renamed target would otherwise leave instructions that fail."""
    targets = set(re.findall(r"^([a-z0-9-]+):", (ROOT / "Makefile").read_text(), flags=re.M))
    documents = [ROOT / "README.md", *(ROOT / "docs").rglob("*.md")]
    for document in documents:
        if {"product", "superpowers"} & set(document.parts):
            continue
        for target in re.findall(r"\bmake ([a-z][a-z0-9-]+)", document.read_text()):
            assert target in targets, f"{document.name} names make {target}, which doesn't exist"


def test_every_document_the_brief_requires_exists() -> None:
    required = (
        "IMPLEMENTATION_STATUS architecture/OVERVIEW STACK MESSAGING REDIS SECURITY OBSERVABILITY CI_CD PREREQUISITES "
        "LOCAL_DEVELOPMENT MODEL_CAPABILITIES MODEL_SETUP MODEL_CUSTOMIZATION DATASETS TRAINING MODEL_DEPLOYMENT "
        "MAC_APPLE_SILICON DEPENDENCY_MATRIX API runbooks/CLOUD_GPU_DEPLOYMENT runbooks/OPERATIONS TROUBLESHOOTING "
        "TESTING BENCHMARKS"
    ).split()
    assert not [name for name in required if not (ROOT / "docs" / f"{name}.md").exists()]
