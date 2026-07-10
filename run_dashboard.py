#!/usr/bin/env python3
"""Launch the Cuckoo Dashboard web server."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from dashboard import main  # noqa: E402

if __name__ == "__main__":
    main()
