#!/usr/bin/env python3
"""Explicitly promote, inspect, or roll back an immutable Ask generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ask_generation_control import (
    GenerationControlError,
    GenerationSwitchUncertain,
    promote_generation,
    resolve_active_generation,
    rollback_generation,
)


def _safe_result(result: dict) -> dict:
    return {
        key: value
        for key, value in result.items()
        if key != "generation_root"
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Manage the reviewed Ask generation pointer. This command never builds "
            "a generation or reads application configuration."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    promote = commands.add_parser("promote")
    promote.add_argument("--generation-root", type=Path, required=True)
    promote.add_argument("--control-root", type=Path, required=True)
    promote.add_argument("--live-data-root", type=Path, required=True)
    promote.add_argument("--promotion-id", required=True)
    promote.add_argument("--reviewer", required=True)
    promote.add_argument("--reviewed-at", required=True)
    promote.add_argument("--acceptance-sha256", required=True)
    promote.add_argument("--expected-active-sha256")

    status = commands.add_parser("status")
    status.add_argument("--control-root", type=Path, required=True)
    status.add_argument("--live-data-root", type=Path, required=True)

    rollback = commands.add_parser("rollback")
    rollback.add_argument("--control-root", type=Path, required=True)
    rollback.add_argument("--live-data-root", type=Path, required=True)
    rollback.add_argument("--target-promotion-id", required=True)
    rollback.add_argument("--expected-active-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "promote":
            result = promote_generation(
                generation_root=arguments.generation_root,
                control_root=arguments.control_root,
                live_data_root=arguments.live_data_root,
                promotion_id=arguments.promotion_id,
                reviewer=arguments.reviewer,
                reviewed_at=arguments.reviewed_at,
                acceptance_sha256=arguments.acceptance_sha256,
                expected_active_sha256=arguments.expected_active_sha256,
            )
        elif arguments.command == "rollback":
            result = rollback_generation(
                control_root=arguments.control_root,
                live_data_root=arguments.live_data_root,
                target_promotion_id=arguments.target_promotion_id,
                expected_active_sha256=arguments.expected_active_sha256,
            )
        else:
            result = resolve_active_generation(
                control_root=arguments.control_root,
                live_data_root=arguments.live_data_root,
            )
    except GenerationSwitchUncertain as error:
        print(json.dumps({"status": "uncertain", "reason": str(error)}), file=sys.stderr)
        return 3
    except GenerationControlError as error:
        print(json.dumps({"status": "error", "reason": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok", **_safe_result(result)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
