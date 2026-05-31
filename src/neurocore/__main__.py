"""Package entrypoint for `python -m neurocore`."""

from __future__ import annotations

from neurocore.adapters.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
