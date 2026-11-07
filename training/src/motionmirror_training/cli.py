"""Training track command line. `make` targets call this."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from motionmirror_training.adapters import AdapterCommandError, AdminApi, login, token_path
from motionmirror_training.datasets.fetch import (
    download_resumable,
    verify_manifest,
    write_manifest,
)
from motionmirror_training.datasets.registry import (
    DatasetBlockedError,
    assert_fetch_allowed,
    load_registry,
)
from motionmirror_training.datasets.sources import kaggle_credentials, kaggle_url
from motionmirror_training.evaluate import EvaluationRefused
from motionmirror_training.train.profiles import load_profile
from motionmirror_training.train.real import PreflightError, calibrate
from motionmirror_training.train.smoke import NoSamplesError

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = REPO_ROOT / "configs" / "datasets" / "registry.yaml"
PROFILES_DIR = REPO_ROOT / "configs" / "training" / "profiles"


def model_cache() -> Path:
    return Path(os.environ.get("MM_MODEL_CACHE", REPO_ROOT / ".models"))


def trainer_dir() -> Path:
    return Path(os.environ.get("MM_DIFFSYNTH_DIR", "/opt/DiffSynth-Studio"))


def _run_dir(profile_id: str) -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    return REPO_ROOT / ".data" / "runs" / "training" / f"{profile_id}-{stamp}"


def dataset_root() -> Path:
    return Path(os.environ.get("MM_DATASET_ROOT", REPO_ROOT / ".data" / "datasets"))


def _progress(total: int | None):
    step = {"next": 0}

    def report(done: int) -> None:
        if done >= step["next"]:
            share = f" ({done / total:.1%})" if total else ""
            print(f"downloaded {done / 1024**3:.2f} GB{share}", flush=True)
            step["next"] = done + 1024**3

    return report


def datasets_fetch(dataset_id: str) -> int:
    entry = load_registry(REGISTRY_PATH).get(dataset_id)
    assert_fetch_allowed(entry)
    if entry.access != "kaggle" or not entry.source_ref:
        raise DatasetBlockedError(f"{dataset_id}: no automatic source is configured")

    root = dataset_root() / dataset_id
    target = root / "download" / f"{dataset_id}.zip"
    if target.exists():
        print(f"already downloaded: {target}")
        return 0

    print(
        f"{entry.name}\nlicence: {entry.license_name}\nreview: {entry.review.decision}\n",
        flush=True,
    )
    download_resumable(
        kaggle_url(entry.source_ref),
        target,
        auth=kaggle_credentials(),
        on_progress=_progress(entry.size_bytes),
    )
    print(f"downloaded: {target}")
    return 0


def datasets_validate(dataset_id: str) -> int:
    entry = load_registry(REGISTRY_PATH).get(dataset_id)
    assert_fetch_allowed(entry)
    root = dataset_root() / dataset_id
    manifest = root / "MANIFEST.json"
    if not manifest.exists():
        if not any(root.rglob("*")):
            print(f"{dataset_id}: nothing downloaded under {root}", file=sys.stderr)
            return 1
        write_manifest(root, manifest, dataset_id, entry.version, dt.date.today().isoformat())
        print(f"wrote {manifest}")
    failures = verify_manifest(root, manifest)
    for name in failures:
        print(f"checksum mismatch or missing: {name}", file=sys.stderr)
    print(f"{dataset_id}: {'FAILED' if failures else 'OK'}")
    return 1 if failures else 0


def samples_build(dataset_id: str, limit: int | None, clips: list[str] | None) -> int:
    """Real pose estimation on real clips. Slow on a Mac, so it takes a limit."""
    from motionmirror.preprocessing.models import load_pose_estimator

    from motionmirror_training.datasets.samples import build_samples

    entry = load_registry(REGISTRY_PATH).get(dataset_id)
    root = dataset_root() / dataset_id
    report = build_samples(
        root / "download" / f"{dataset_id}.zip",
        entry,
        root / "built",
        load_pose_estimator(),
        limit,
        clips,
    )
    print(
        f"built {len(report.samples)} samples, rejected {len(report.rejections)}. See {root / 'built' / 'manifest.json'}"
    )
    return 0


def _device(profile_device: str) -> str:
    from motionmirror.device import select_device

    return select_device(profile_device).device  # type: ignore[arg-type]


def train_smoke(profile_id: str, samples: Path | None, dataset: str, out: Path | None) -> int:
    profile = load_profile(PROFILES_DIR, profile_id)
    samples_dir = samples or dataset_root() / dataset / "built"
    if profile.kind == "real":
        print(
            "A short real pass on the GPU, to measure a step and prove the trainer starts.",
            flush=True,
        )
        result = calibrate(
            profile,
            samples_dir,
            model_cache() / "wan-animate-14b-training",
            trainer_dir(),
            out or _run_dir(profile_id),
        )
        print(json.dumps(result, indent=2))
        return 0
    from motionmirror_training.train.smoke import run_smoke

    manifest = run_smoke(profile, samples_dir, out or _run_dir(profile_id))
    print(
        json.dumps(
            {
                key: manifest[key]
                for key in ("label", "device_type", "steps_completed", "losses", "verification")
            },
            indent=2,
        )
    )
    return 0


def train_lora(args: argparse.Namespace) -> int:
    from motionmirror_training.train.real import run_real

    profile = load_profile(PROFILES_DIR, args.profile)
    entry = load_registry(REGISTRY_PATH).get(args.dataset)
    samples_dir = args.samples or dataset_root() / args.dataset / "built"
    manifest = run_real(
        profile,
        entry,
        samples_dir,
        model_cache() / "wan-animate-14b-training",
        trainer_dir(),
        args.out or _run_dir(args.profile),
        args.seconds_per_step,
        args.confirm,
        args.transfer_permission_confirmed,
        args.accept_below_tested_hardware,
    )
    print(json.dumps(manifest["adapter"], indent=2))
    return 0


def eval_adapter(args: argparse.Namespace) -> int:
    from motionmirror.backends.wan_animate import WanAnimateReplacementBackend

    from motionmirror_training.evaluate import evaluate_adapter

    device = os.environ.get("MM_DEVICE_PROFILE", "cuda")
    samples_dir = args.samples or dataset_root() / args.dataset / "built"
    manifest = evaluate_adapter(
        args.adapter,
        samples_dir,
        REPO_ROOT / "configs" / "presets",
        args.adapter / "evaluation",
        WanAnimateReplacementBackend.from_environment,
        device,
        not_for_promotion=args.not_for_promotion,
    )
    print(
        json.dumps(
            {key: manifest[key] for key in ("promotion_evidence", "sets", "hardware")}, indent=2
        )
    )
    print(f"Review the contact sheets, then run: make eval-approve ADAPTER={args.adapter}")
    return 0


def eval_approve(args: argparse.Namespace) -> int:
    from motionmirror_training.evaluate import CHECKLIST, approve

    results = {item: item not in (args.failed or []) for item in CHECKLIST}
    manifest = approve(
        args.adapter / "evaluation" / "eval_manifest.json", args.reviewer, results, args.notes
    )
    print(json.dumps(manifest["human_review"], indent=2))
    return 0


def _operator_api() -> AdminApi:
    path = token_path(REPO_ROOT)
    token = os.environ.get("MM_OPERATOR_TOKEN") or (
        path.read_text().strip() if path.exists() else ""
    )
    if not token:
        raise AdapterCommandError("Not signed in. Run: make adapter-login")
    return AdminApi(_api_url(), token)


def _api_url() -> str:
    return os.environ.get("MM_API_URL", "http://localhost:8088")


def adapter_command(args: argparse.Namespace) -> int:
    action = args.action
    if action == "login":
        login(_api_url(), token_path(REPO_ROOT))
        return 0
    api = _operator_api()
    if action == "register":
        result = api.register(args.adapter, args.name, args.scale, args.eval_manifest)
    elif action == "evaluate":
        result = api.attach_evaluation(args.id, args.eval_manifest)
    elif action == "stage":
        result = api.stage(args.id, args.preset)
    elif action == "promote":
        result = api.promote(args.id, args.preset)
    elif action == "deprecate":
        result = api.deprecate(args.id)
    elif action == "rollback":
        result = api.rollback(args.preset)
    else:
        result = api.list()
    print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mm-train")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("datasets-fetch")
    fetch.add_argument("--dataset", required=True)
    validate = commands.add_parser("datasets-validate")
    validate.add_argument("--dataset", required=True)
    build = commands.add_parser("samples-build")
    build.add_argument("--dataset", required=True)
    build.add_argument("--limit", type=int, help="stop after this many samples")
    build.add_argument("--clip", action="append", help="only these clip ids")
    smoke = commands.add_parser("train-smoke")
    smoke.add_argument("--profile", required=True)
    smoke.add_argument("--dataset", default="tiktok-dance")
    smoke.add_argument("--samples", type=Path)
    smoke.add_argument("--out", type=Path)
    lora = commands.add_parser("train-lora")
    lora.add_argument("--profile", required=True)
    lora.add_argument("--dataset", default="tiktok-dance")
    lora.add_argument("--samples", type=Path)
    lora.add_argument("--out", type=Path)
    lora.add_argument("--seconds-per-step", type=float)
    lora.add_argument("--confirm", action="store_true")
    lora.add_argument("--transfer-permission-confirmed", action="store_true")
    lora.add_argument("--accept-below-tested-hardware", action="store_true")
    evaluation = commands.add_parser("eval-adapter")
    evaluation.add_argument("--adapter", type=Path, required=True, help="the run directory")
    evaluation.add_argument("--dataset", default="tiktok-dance")
    evaluation.add_argument("--samples", type=Path)
    evaluation.add_argument("--not-for-promotion", action="store_true")
    approval = commands.add_parser("eval-approve")
    approval.add_argument("--adapter", type=Path, required=True)
    approval.add_argument("--reviewer", required=True)
    approval.add_argument("--notes", default="")
    approval.add_argument("--failed", action="append", help="a checklist item that did not pass")
    adapter = commands.add_parser("adapter")
    adapter.add_argument(
        "action",
        choices=[
            "login",
            "register",
            "evaluate",
            "stage",
            "promote",
            "deprecate",
            "rollback",
            "list",
        ],
    )
    adapter.add_argument("--adapter", type=Path)
    adapter.add_argument("--id")
    adapter.add_argument("--name")
    adapter.add_argument("--preset", default="standard")
    adapter.add_argument("--scale", type=float, default=1.0)
    adapter.add_argument("--eval-manifest", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "datasets-fetch":
            return datasets_fetch(args.dataset)
        if args.command == "samples-build":
            return samples_build(args.dataset, args.limit, args.clip)
        if args.command == "train-smoke":
            return train_smoke(args.profile, args.samples, args.dataset, args.out)
        if args.command == "train-lora":
            return train_lora(args)
        if args.command == "eval-adapter":
            return eval_adapter(args)
        if args.command == "eval-approve":
            return eval_approve(args)
        if args.command == "adapter":
            return adapter_command(args)
        return datasets_validate(args.dataset)
    except DatasetBlockedError as blocked:
        print(f"licence gate: {blocked}", file=sys.stderr)
        return 2
    except (
        KeyError,
        PreflightError,
        EvaluationRefused,
        AdapterCommandError,
        NoSamplesError,
    ) as refused:
        print(refused.args[0] if refused.args else refused, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
