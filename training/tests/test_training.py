"""The smoke loop for real on tiny weights, and everything around the real run except the GPU itself."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
import torch
from motionmirror.backends.base import GenerationResult, ValidationResult
from motionmirror.media.frames import write_video
from safetensors.torch import load_file, save_file
from test_samples import FakePose, entry, write_clip

from motionmirror_training import cli
from motionmirror_training.adapters import AdapterCommandError, AdminApi, login
from motionmirror_training.datasets.samples import build_samples
from motionmirror_training.evaluate import CHECKLIST, EvaluationRefused, approve, evaluate_adapter
from motionmirror_training.train.profiles import Profile, load_profile
from motionmirror_training.train.real import (
    Hardware,
    PreflightError,
    build_command,
    calibrate,
    newest_checkpoint,
    planned_steps,
    preflight,
    run_real,
)
from motionmirror_training.train.smoke import NoSamplesError, run_smoke

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "configs" / "training" / "profiles"
PRESETS = ROOT / "configs" / "presets"
ENOUGH = Hardware(gpu_count=8, gpu_memory_gb=80, free_disk_gb=500)


@pytest.fixture(scope="module")
def samples(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("archive") / "dataset.zip"
    with zipfile.ZipFile(path, "w") as output:
        write_clip(output, "00001", 100)
    out = tmp_path_factory.mktemp("samples")
    build_samples(path, entry(), out, FakePose())
    return out


def profile(name: str = "cpu-smoke") -> Profile:
    return load_profile(PROFILES, name)


@pytest.mark.parametrize("name", ["cpu-smoke", "mac-mps", "gpu-cuda"])
def test_every_profile_loads(name: str) -> None:
    assert profile(name).id == name


def test_an_unknown_profile_names_the_choices() -> None:
    with pytest.raises(KeyError, match="cpu-smoke"):
        profile("anything")


def test_a_smoke_profile_cant_claim_to_satisfy_the_gate() -> None:
    fields = profile().model_dump() | {"can_satisfy_training_gate": True}
    with pytest.raises(ValueError, match="smoke run"):
        Profile.model_validate(fields)


def test_a_real_profile_must_be_cuda_and_pinned() -> None:
    fields = profile("gpu-cuda").model_dump()
    with pytest.raises(ValueError, match="needs CUDA"):
        Profile.model_validate(fields | {"device": "mps"})
    with pytest.raises(ValueError, match="pinned trainer"):
        Profile.model_validate(fields | {"trainer": None})


def test_sizes_the_model_cant_use_are_refused() -> None:
    fields = profile().model_dump()
    with pytest.raises(ValueError, match="divisible by 16"):
        Profile.model_validate(fields | {"video": {"height": 30, "width": 32, "num_frames": 9}})
    with pytest.raises(ValueError, match="4N"):
        Profile.model_validate(fields | {"video": {"height": 32, "width": 32, "num_frames": 10}})


def test_the_gpu_profile_uses_the_trainers_own_values() -> None:
    real = profile("gpu-cuda")
    assert real.lora.rank == 32
    assert real.lora.target_modules == ["q", "k", "v", "o", "ffn.0", "ffn.2"]
    assert real.learning_rate == 1e-4
    assert real.trainer is not None
    assert real.trainer.commit == "08fdc0da8f41426885d9432e9007e6e460a4c30d"
    assert "1*80G GPU cannot train" in real.trainer.authors_note


def test_a_smoke_run_really_trains_and_exports_through_the_serving_loader(
    samples: Path, tmp_path: Path
) -> None:
    manifest = run_smoke(profile(), samples, tmp_path)
    assert manifest["steps_completed"] == 8
    assert len(manifest["losses"]) == 8
    assert all(math.isfinite(loss) for loss in manifest["losses"])
    assert manifest["verification"]["layers_attached"] > 0
    assert manifest["verification"]["output_changed"] is True


def test_a_smoke_run_says_exactly_what_it_is(samples: Path, tmp_path: Path) -> None:
    manifest = run_smoke(profile(), samples, tmp_path)
    assert manifest["label"] == "smoke"
    assert manifest["can_satisfy_training_gate"] is False
    assert "tiny-random" in manifest["base_model"]
    for name in (
        "hyperparameters",
        "lora",
        "trainer",
        "seed",
        "export_format",
        "peak_host_memory_gb",
        "losses",
    ):
        assert name in manifest, name
    assert manifest["dataset"]["license_scope"] == "research_only"


def test_export_uses_the_trainers_key_format(samples: Path, tmp_path: Path) -> None:
    run_smoke(profile(), samples, tmp_path)
    keys = load_file(str(tmp_path / "adapter.safetensors")).keys()
    assert any(key.endswith(".self_attn.q.lora_A.default.weight") for key in keys)
    assert any(key.endswith(".ffn.2.lora_B.default.weight") for key in keys)


def test_a_resumed_run_ends_exactly_where_an_unbroken_one_does(
    samples: Path, tmp_path: Path
) -> None:
    unbroken = run_smoke(profile(), samples, tmp_path / "unbroken")
    run_smoke(profile(), samples, tmp_path / "resumed", stop_after=4)
    resumed = run_smoke(profile(), samples, tmp_path / "resumed")
    assert resumed["resumed_from_step"] == 4
    assert resumed["adapter"]["sha256"] == unbroken["adapter"]["sha256"]
    assert resumed["losses"] == pytest.approx(unbroken["losses"])


def test_checkpoints_are_data_not_pickles(samples: Path, tmp_path: Path) -> None:
    run_smoke(profile(), samples, tmp_path)
    files = {path.suffix for path in (tmp_path / "checkpoints").rglob("*") if path.is_file()}
    assert files == {".safetensors", ".json"}


@pytest.mark.skipif(not torch.backends.mps.is_available(), reason="no Apple Silicon GPU")
def test_the_mac_profile_trains_on_the_mac_gpu(samples: Path, tmp_path: Path) -> None:
    manifest = run_smoke(profile("mac-mps"), samples, tmp_path)
    assert manifest["device_type"] == "mps"
    assert manifest["verification"]["output_changed"] is True


def test_no_samples_is_a_clear_refusal(tmp_path: Path) -> None:
    with pytest.raises(NoSamplesError, match="samples-build"):
        run_smoke(profile(), tmp_path, tmp_path / "out")


def test_a_real_profile_isnt_a_smoke_run(samples: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="isn't a smoke"):
        run_smoke(profile("gpu-cuda"), samples, tmp_path)


def plan(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "profile": profile("gpu-cuda"),
        "entry": entry(),
        "sample_count": 10,
        "hardware": ENOUGH,
        "seconds_per_step": 30.0,
        "confirmed": True,
        "transfer_confirmed": True,
        "accept_below_tested": False,
    }
    return preflight(**{**arguments, **overrides})


def test_a_complete_preflight_gives_the_plan() -> None:
    result = plan()
    assert result["steps"] == planned_steps(profile("gpu-cuda"), 10, 8)
    assert result["estimated_hours"] == pytest.approx(result["steps"] * 30 / 3600, abs=0.01)
    assert result["estimated_gpu_hours"] == pytest.approx(result["estimated_hours"] * 8, abs=0.05)


def test_nothing_starts_without_explicit_confirmation_and_it_shows_the_plan_first() -> None:
    with pytest.raises(PreflightError, match="Nothing started.*estimated_hours"):
        plan(confirmed=False)


def test_nothing_starts_without_a_duration_estimate() -> None:
    with pytest.raises(PreflightError, match="--calibrate"):
        plan(seconds_per_step=None)


def test_nothing_starts_on_a_machine_with_no_gpu() -> None:
    with pytest.raises(PreflightError, match="never falls back"):
        plan(hardware=Hardware(0, 0, 500))


def test_too_little_disk_is_refused() -> None:
    with pytest.raises(PreflightError, match="GB of disk"):
        plan(hardware=Hardware(8, 80, 10))


def test_below_the_authors_hardware_needs_an_explicit_choice_and_quotes_them() -> None:
    small = Hardware(1, 80, 500)
    with pytest.raises(PreflightError, match="1\\*80G GPU cannot train"):
        plan(hardware=small)
    assert plan(hardware=small, accept_below_tested=True)["gpu_count"] == 1


def test_the_licences_transfer_limit_is_raised_before_any_rented_gpu_is_used() -> None:
    restricted = entry().model_copy(update={"transfer_requires_permission": True})
    with pytest.raises(PreflightError, match="outside parties"):
        plan(entry=restricted, transfer_confirmed=False)
    assert plan(entry=restricted, transfer_confirmed=True)


def test_the_real_tiktok_entry_carries_the_transfer_limit() -> None:
    from motionmirror_training.datasets.registry import load_registry

    assert (
        load_registry(ROOT / "configs" / "datasets" / "registry.yaml")
        .get("tiktok-dance")
        .transfer_requires_permission
    )


def test_a_blocked_dataset_is_refused_even_when_confirmed() -> None:
    from motionmirror_training.datasets.registry import DatasetBlockedError

    with pytest.raises(DatasetBlockedError):
        plan(entry=entry(status="blocked", scope="none", access="blocked"))


def test_a_smoke_profile_cant_start_a_real_run() -> None:
    with pytest.raises(PreflightError, match="smoke profile"):
        plan(profile=profile("mac-mps"))


def model_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "diffusion_pytorch_model-00001-of-00002.safetensors").write_bytes(b"x")
    (path / "diffusion_pytorch_model-00002-of-00002.safetensors").write_bytes(b"x")
    return path


def test_the_command_trains_replacement_mode_from_local_pinned_files(
    samples: Path, tmp_path: Path
) -> None:
    command = build_command(
        profile("gpu-cuda"),
        samples,
        model_dir(tmp_path / "m"),
        tmp_path / "t",
        tmp_path,
        tmp_path / "a.yaml",
    )
    joined = " ".join(command)
    assert "animate_inpaint_video" in command[command.index("--data_file_keys") + 1]
    assert "animate_mask_video" in command[command.index("--extra_inputs") + 1]
    paths = json.loads(command[command.index("--model_paths") + 1])
    assert len(paths[0]) == 2, "shards load together"
    assert "--model_id_with_origin_paths" not in joined, (
        "that path would download whatever is current"
    )
    assert command[command.index("--height") + 1] == "768"
    assert command[command.index("--num_frames") + 1] == "81"


def test_a_stopped_run_resumes_from_its_newest_checkpoint(samples: Path, tmp_path: Path) -> None:
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    older, newer = checkpoints / "step-500.safetensors", checkpoints / "step-1000.safetensors"
    older.write_bytes(b"x")
    newer.write_bytes(b"x")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    command = build_command(
        profile("gpu-cuda"),
        samples,
        model_dir(tmp_path / "m"),
        tmp_path / "t",
        tmp_path,
        tmp_path / "a.yaml",
    )
    assert command[command.index("--resume_from_checkpoint") + 1].endswith("step-1000.safetensors")


def test_the_adapter_a_run_reports_is_its_newest_checkpoint_not_the_last_by_name(
    tmp_path: Path,
) -> None:
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    older, newer = checkpoints / "step-500.safetensors", checkpoints / "step-1000.safetensors"
    older.write_bytes(b"x")
    newer.write_bytes(b"x")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    assert newest_checkpoint(tmp_path) == newer
    assert newest_checkpoint(tmp_path / "missing") is None


def train_samples(samples: Path, tmp_path: Path) -> Path:
    """The synthetic build's one clip lands in whichever split its hash gives, so this makes it the training set."""
    copy = tmp_path / "samples"
    copy.mkdir()
    manifest = json.loads((samples / "manifest.json").read_text())
    for row in manifest["samples"]:
        row["split"] = "train"
    (copy / "manifest.json").write_text(json.dumps(manifest))
    return copy


def test_a_finished_run_writes_the_manifest_the_brief_lists(samples: Path, tmp_path: Path) -> None:
    copy = train_samples(samples, tmp_path)

    def trainer(command: list[str], cwd: Path) -> int:
        output = Path(command[command.index("--output_path") + 1])
        output.mkdir(parents=True, exist_ok=True)
        save_file(
            {"blocks.0.self_attn.q.lora_A.default.weight": torch.zeros(1)},
            str(output / "epoch-4.safetensors"),
        )
        (output / "loss.csv").write_text("step,loss\n1,0.5\n")
        return 0

    manifest = run_real(
        profile("gpu-cuda"), entry(), copy, model_dir(tmp_path / "m"), tmp_path / "t", tmp_path / "run",
        30.0, True, True, False, hardware=ENOUGH, execute=trainer,
    )  # fmt: skip
    assert manifest["trainer"]["commit"] == "08fdc0da8f41426885d9432e9007e6e460a4c30d"
    assert manifest["base_model"]["serving_revision"] == "6f4df10861c758af86ac3c979aacc1bf5c03eff0"
    assert manifest["dataset"]["id"] == "tiny-dance"
    assert manifest["license_scope"] == "research_only"
    assert manifest["label"] == "research"
    assert manifest["can_satisfy_training_gate"] is False
    assert manifest["export_format"] == "safetensors"
    assert manifest["loss_logs"]
    assert len(manifest["adapter"]["sha256"]) == 64
    assert (
        json.loads((tmp_path / "run" / "run_manifest.json").read_text())["adapter"]["path"]
        == "epoch-4.safetensors"
    )


def test_a_trainer_failure_stops_the_run_with_no_manifest(samples: Path, tmp_path: Path) -> None:
    copy = train_samples(samples, tmp_path)
    with pytest.raises(RuntimeError, match="exited with 137"):
        run_real(
            profile("gpu-cuda"), entry(), copy, model_dir(tmp_path / "m"), tmp_path / "t", tmp_path / "run",
            30.0, True, True, False, hardware=ENOUGH, execute=lambda _command, _cwd: 137,
        )  # fmt: skip
    assert not (tmp_path / "run" / "run_manifest.json").exists()


def test_calibration_measures_a_step_and_is_a_short_pass(samples: Path, tmp_path: Path) -> None:
    copy = train_samples(samples, tmp_path)
    seen: list[list[str]] = []
    times = iter([100.0, 160.0])

    def trainer(command: list[str], cwd: Path) -> int:
        seen.append(command)
        return 0

    result = calibrate(
        profile("gpu-cuda"), copy, model_dir(tmp_path / "m"), tmp_path / "t", tmp_path / "run",
        hardware=Hardware(1, 80, 500), execute=trainer, clock=lambda: next(times),
    )  # fmt: skip
    assert result["seconds_per_step"] == 60.0
    assert result["includes_model_load"] is True
    assert seen[0][seen[0].index("--num_epochs") + 1] == "1"
    assert seen[0][seen[0].index("--dataset_repeat") + 1] == "1"


class StubBackend:
    def __init__(self, fail_with_adapter: bool = False) -> None:
        self.fail_with_adapter = fail_with_adapter

    def validate(self, request: Any) -> ValidationResult:
        return ValidationResult(True)

    def prepare(self, request: Any) -> Any:
        return request

    def generate(self, inputs: Any, context: Any) -> GenerationResult:
        if inputs.adapter is not None and self.fail_with_adapter:
            raise RuntimeError("out of memory with the adapter loaded")
        shade = 200 if inputs.adapter else 90
        import numpy as np

        video = context.workspace / "result.mp4"
        write_video([np.full((64, 48, 3), shade, dtype=np.uint8)] * 6, video, 10)
        manifest = context.workspace / "manifest.json"
        manifest.write_text(json.dumps({"seed": inputs.settings["seed"]}))
        return GenerationResult(video, manifest, {})

    def release(self) -> None:
        pass


def evaluation_inputs(samples: Path, tmp_path: Path) -> tuple[Path, Path]:
    copy = tmp_path / "eval-samples"
    copy.mkdir()
    manifest = json.loads((samples / "manifest.json").read_text())
    for row in manifest["samples"]:
        row["split"] = "test"
    manifest["fixed_eval_set"] = [row["clip_id"] for row in manifest["samples"]]
    (copy / "manifest.json").write_text(json.dumps(manifest))
    for name in ("samples",):
        (copy / name).symlink_to(samples / name)
    run = tmp_path / "run"
    run.mkdir()
    (run / "adapter.safetensors").write_bytes(b"weights")
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "label": "research",
                "license_scope": "research_only",
                "adapter": {
                    "path": "adapter.safetensors",
                    "sha256": hashlib.sha256(b"weights").hexdigest(),
                },
                "lora": {"rank": 32, "target_modules": ["q"]},
                "trainer": {"repo": "r", "commit": "c"},
                "base_model": {"serving_id": "s"},
            }
        )
    )
    return copy, run


def evaluate(samples: Path, tmp_path: Path, device: str = "cuda", **kwargs: Any) -> dict[str, Any]:
    copy, run = evaluation_inputs(samples, tmp_path)
    factory = kwargs.pop("factory", StubBackend)
    return evaluate_adapter(
        run, copy, PRESETS, tmp_path / "evaluation", factory, device, (42, 7), **kwargs
    )


def test_a_mac_evaluation_is_refused_as_promotion_evidence(samples: Path, tmp_path: Path) -> None:
    with pytest.raises(EvaluationRefused, match="CUDA"):
        evaluate(samples, tmp_path, "mps")


def test_a_mac_run_of_the_path_can_never_count(samples: Path, tmp_path: Path) -> None:
    manifest = evaluate(samples, tmp_path, "mps", not_for_promotion=True)
    assert manifest["promotion_evidence"] is False
    with pytest.raises(EvaluationRefused, match="isn't promotion evidence"):
        approve(
            tmp_path / "evaluation" / "eval_manifest.json",
            "Someone",
            dict.fromkeys(CHECKLIST, True),
            "",
        )


def test_evaluation_compares_base_and_adapter_on_every_clip_and_seed(
    samples: Path, tmp_path: Path
) -> None:
    manifest = evaluate(samples, tmp_path)
    assert manifest["promotion_evidence"] is True
    assert len(manifest["comparisons"]) == 2
    assert {entry["seed"] for entry in manifest["comparisons"]} == {42, 7}
    for comparison in manifest["comparisons"]:
        assert comparison["adapter_changed_output"] is True
        assert len(comparison["base"]["sha256"]) == 64
        assert Path(comparison["contact_sheet"]).exists()
    assert manifest["human_review"]["passed"] is False, "no adapter passes until a person says so"


def test_an_out_of_memory_with_the_adapter_is_recorded_and_isnt_evidence(
    samples: Path, tmp_path: Path
) -> None:
    manifest = evaluate(samples, tmp_path, factory=lambda: StubBackend(fail_with_adapter=True))
    assert manifest["promotion_evidence"] is False
    assert manifest["comparisons"][0]["adapter"]["code"] == "RuntimeError"
    card = (tmp_path / "evaluation" / "MODEL_CARD.md").read_text()
    assert "adapter failed with RuntimeError" in card


def test_a_changed_adapter_file_is_refused(samples: Path, tmp_path: Path) -> None:
    copy, run = evaluation_inputs(samples, tmp_path)
    (run / "adapter.safetensors").write_bytes(b"swapped")
    with pytest.raises(EvaluationRefused, match="doesn't match"):
        evaluate_adapter(run, copy, PRESETS, tmp_path / "e", StubBackend, "cuda")


def test_the_model_card_states_data_licence_and_intended_use(samples: Path, tmp_path: Path) -> None:
    evaluate(samples, tmp_path)
    card = (tmp_path / "evaluation" / "MODEL_CARD.md").read_text()
    assert "can't be promoted to production" in card
    assert "CC BY-NC 4.0" in card
    assert "Known failures" in card


def test_a_review_must_answer_every_item_and_name_a_person(samples: Path, tmp_path: Path) -> None:
    evaluate(samples, tmp_path)
    path = tmp_path / "evaluation" / "eval_manifest.json"
    with pytest.raises(EvaluationRefused, match="Still to answer: outfit_continuity"):
        approve(path, "Someone", {"likeness": True} | {i: True for i in CHECKLIST[2:]}, "")
    with pytest.raises(EvaluationRefused, match="reviewer"):
        approve(path, " ", dict.fromkeys(CHECKLIST, True), "")
    with pytest.raises(EvaluationRefused, match="Not on the checklist"):
        approve(path, "Someone", dict.fromkeys(CHECKLIST, True) | {"vibes": True}, "")


def test_one_failed_item_fails_the_review(samples: Path, tmp_path: Path) -> None:
    evaluate(samples, tmp_path)
    path = tmp_path / "evaluation" / "eval_manifest.json"
    assert (
        approve(path, "Someone", dict.fromkeys(CHECKLIST, True) | {"hands": False}, "")[
            "human_review"
        ]["passed"]
        is False
    )
    assert (
        approve(path, "Someone", dict.fromkeys(CHECKLIST, True), "")["human_review"]["passed"]
        is True
    )


def run_directory(path: Path, label: str = "research") -> Path:
    path.mkdir()
    (path / "adapter.safetensors").write_bytes(b"weights")
    (path / "run_manifest.json").write_text(
        json.dumps(
            {
                "label": label,
                "license_scope": "research_only",
                "adapter": {"path": "adapter.safetensors", "sha256": "a" * 64},
                "base_model": {
                    "serving_id": "serve",
                    "serving_revision": "rev",
                    "training_id": "train",
                },
                "lora": {"rank": 32, "target_modules": ["q"]},
                "dataset": {"id": "tiktok-dance", "version": "1", "license": "CC BY-NC 4.0"},
                "trainer": {"repo": "r", "commit": "c"},
                "plan": {"steps": 1},
            }
        )
    )
    return path


def admin_api(handler: Any) -> AdminApi:
    return AdminApi("http://api.test", "operator-token", transport=httpx.MockTransport(handler))


def test_registering_uploads_to_the_apis_key_and_sends_the_manifests_facts(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("upload-target"):
            assert request.headers["Authorization"] == "Bearer operator-token"
            return httpx.Response(
                200, json={"id": "id-1", "key": "k", "url": "http://store.test/put"}
            )
        if request.url.host == "store.test":
            seen["uploaded"] = request.read()
            assert "Authorization" not in request.headers, (
                "a signed URL must not carry the operator's token"
            )
            return httpx.Response(200)
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "id-1", "state": "REGISTERED"})

    result = admin_api(handler).register(run_directory(tmp_path / "run"), None, 0.8, None)
    assert result["state"] == "REGISTERED"
    assert seen["uploaded"] == b"weights"
    body = seen["body"]
    assert body["id"] == "id-1"
    assert body["baseModelId"] == "serve", "registered against what serving loads"
    assert body["licenseScope"] == "research_only"
    assert body["datasetVersions"] == ["tiktok-dance@1"]
    assert body["scale"] == 0.8


def test_a_smoke_adapter_cant_be_registered(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nothing should be sent")

    with pytest.raises(AdapterCommandError, match="smoke run"):
        admin_api(handler).register(run_directory(tmp_path / "run", "smoke"), None, 1.0, None)


def test_the_apis_refusal_reaches_the_operator_in_its_own_words() -> None:
    api = admin_api(
        lambda _request: httpx.Response(
            409, json={"code": "RESEARCH_ADAPTER", "detail": "Can't be promoted"}
        )
    )
    with pytest.raises(AdapterCommandError, match="RESEARCH_ADAPTER: Can't be promoted"):
        api.promote("id-1", "standard")


def test_signing_in_stores_the_token_for_this_user_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    polls = iter(
        [
            httpx.Response(400, json={"error": "authorization_pending"}),
            httpx.Response(200, json={"access_token": "secret-token"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/device"):
            return httpx.Response(
                200,
                json={
                    "device_code": "d",
                    "verification_uri_complete": "http://id.test/activate?code=x",
                    "expires_in": 30,
                },
            )
        return next(polls)

    destination = tmp_path / "nested" / "token"
    login("http://localhost:8088", destination, transport=httpx.MockTransport(handler), wait=0)
    assert destination.read_text() == "secret-token"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    printed = capsys.readouterr().out
    assert "http://id.test/activate" in printed
    assert "secret-token" not in printed


def test_a_refused_sign_in_says_so(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/device"):
            return httpx.Response(
                200, json={"device_code": "d", "verification_uri_complete": "u", "expires_in": 30}
            )
        return httpx.Response(400, json={"error": "access_denied"})

    with pytest.raises(AdapterCommandError, match="access_denied"):
        login("http://x", tmp_path / "t", transport=httpx.MockTransport(handler), wait=0)


def test_the_command_line_refuses_a_real_run_here_with_a_reason(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = cli.main(["train-lora", "--profile", "mac-mps", "--samples", str(tmp_path)])
    assert code == 2
    assert capsys.readouterr().err


def test_the_command_line_refuses_commands_without_signing_in(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MM_OPERATOR_TOKEN", raising=False)
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    assert cli.main(["adapter", "list"]) == 2
    assert "make adapter-login" in capsys.readouterr().err
