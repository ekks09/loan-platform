#!/usr/bin/env python3
"""Emit the tail of a log file as a GitHub Actions ::error annotation.

The raw workflow log lives behind a host that is not reachable from every
network; annotations are, via the check-runs API. Escaping follows the
workflow-command spec: %, CR and LF must be percent-encoded in that order.

Usage: ci_annotate_log.py <annotation-title> <log-path> [tail-lines]
"""

import sys


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: ci_annotate_log.py <title> <log-path> [tail-lines]", file=sys.stderr)
        return 2
    title, path = sys.argv[1], sys.argv[2]
    tail_lines = int(sys.argv[3]) if len(sys.argv) > 3 else 120
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        text = f"<log file missing: {path}>"
    tail = "\n".join(text.splitlines()[-tail_lines:])
    encoded = tail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title={title}::{encoded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
