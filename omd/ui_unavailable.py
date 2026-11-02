"""Dependency-free diagnostic for the deferred browser UI."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from omd import __version__


_MESSAGE = (
    f"The browser UI is unavailable in OMD {__version__}. "
    "This is a core backend release. "
    "The omd CLI, omd-mcp, and OMD Home integration remain available. "
    "The browser UI will return after its dependencies are compatible with "
    "the required security fixes."
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omd-ui", description=_MESSAGE)
    # Existing launch options must still explain the deferred UI, not suggest
    # that changing a port or another flag would make this release launch it.
    parser.parse_known_args(argv)
    print(_MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
