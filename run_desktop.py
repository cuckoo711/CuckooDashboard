#!/usr/bin/env python3
"""Launch the Cuckoo Dashboard native desktop app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# desktop.py uses argparse and webview directly
import runpy
runpy.run_path(str(Path(__file__).resolve().parent / "src" / "desktop.py"), run_name="__main__")
