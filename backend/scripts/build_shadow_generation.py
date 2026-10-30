#!/usr/bin/env python3
"""Build an offline Ask shadow generation from a verified restore."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.shadow_generation import build_restored_shadow_generation, finalize_shadow_generation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--production-wiki-root", type=Path, required=True)
    parser.add_argument("--finalize-existing", action="store_true")
    arguments = parser.parse_args()
    if arguments.finalize_existing:
        result = finalize_shadow_generation(
            output_root=arguments.output_root,
            production_wiki_root=arguments.production_wiki_root,
        )
    else:
        if arguments.data_root is None:
            parser.error("--data-root is required unless --finalize-existing is used")
        result = build_restored_shadow_generation(
            data_root=arguments.data_root,
            output_root=arguments.output_root,
            production_wiki_root=arguments.production_wiki_root,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
