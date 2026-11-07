#!/usr/bin/env python3
"""The production broker definitions: real queues and accounts only, with passwords as salted hashes.

Usage: scripts/render_definitions.py --env-file production.env --out definitions.json
Then import it, as the runbook describes. The output holds hashes, so treat it as sensitive and don't commit it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "configs" / "rabbitmq" / "definitions.template.json"
DEV_USERS = {"mm-consumer-mock", "mm-provision"}

_spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts" / "bootstrap.py")
assert _spec and _spec.loader
bootstrap = importlib.util.module_from_spec(_spec)
sys.modules["bootstrap"] = bootstrap
_spec.loader.exec_module(bootstrap)


def production_definitions(definitions: dict[str, Any]) -> dict[str, Any]:
    def is_mock(name: str) -> bool:
        return ".mock." in name

    result = dict(definitions)
    result["users"] = [user for user in definitions["users"] if user["name"] not in DEV_USERS]
    result["permissions"] = [item for item in definitions["permissions"] if item["user"] not in DEV_USERS]
    result["queues"] = [queue for queue in definitions["queues"] if not is_mock(queue["name"])]
    result["bindings"] = [binding for binding in definitions["bindings"] if not is_mock(binding["destination"])]
    return result


def render(template: str, values: dict[str, str]) -> dict[str, Any]:
    """Only the accounts production has need a password, so the others are filled with placeholders and then removed."""
    names = bootstrap.re.findall(r"\$\{(\w+)\}", template)
    filled = dict(values)
    for name in names:
        plain = name.removesuffix("_HASH")
        if any(user.upper().replace("-", "_").removeprefix("MM_") in plain for user in DEV_USERS):
            filled.setdefault(plain, "removed-below")
    return production_definitions(json.loads(bootstrap.render_template(template, filled)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    values = bootstrap.parse_env(args.env_file.read_text())
    try:
        rendered = render(TEMPLATE.read_text(), values)
    except KeyError as missing:
        print(f"{args.env_file} doesn't set {missing}", file=sys.stderr)
        return 2
    bootstrap.write_private(args.out, json.dumps(rendered, indent=2) + "\n")
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
