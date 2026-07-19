"""Allow `python -m omd <input>` to dispatch via the CLI."""
from omd.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
