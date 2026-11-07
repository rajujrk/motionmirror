#!/usr/bin/env python3
"""The production realm: the development template without its test accounts or mock worker.

The output holds placeholders only, which Keycloak fills from its environment at import, so it is safe to keep on disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "infra" / "identity" / "realm-template.json"
DEV_PLACEHOLDERS = ("MM_DEV_USER_PASSWORD", "MM_DEV_ADMIN_PASSWORD", "MM_WORKER_MOCK_CLIENT_SECRET")
DEV_CLIENTS = {"motionmirror-worker-mock"}


def production_realm(template: dict) -> dict:
    realm = dict(template)
    realm.pop("users", None)
    realm["clients"] = [client for client in template.get("clients", []) if client["clientId"] not in DEV_CLIENTS]
    realm["sslRequired"] = "all"
    realm["registrationAllowed"] = False
    return realm


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / ".data" / "runtime" / "realm-production.json")
    args = parser.parse_args()
    realm = production_realm(json.loads(TEMPLATE.read_text()))
    rendered = json.dumps(realm, indent=2)
    leftover = [name for name in DEV_PLACEHOLDERS if name in rendered]
    if leftover:
        print(f"Development values are still in the realm: {leftover}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered + "\n")
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
