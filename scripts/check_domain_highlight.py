#!/usr/bin/env python3
"""Suoji incident-edge highlight: isolated synthetic browser acceptance."""
import sys
from check_domain_layout import main
from check_frontend import CheckInterrupted

if __name__ == "__main__":
    try:
        sys.exit(main("highlight"))
    except CheckInterrupted as error:
        print(str(error), file=sys.stderr)
        sys.exit(128 + error.signum)
