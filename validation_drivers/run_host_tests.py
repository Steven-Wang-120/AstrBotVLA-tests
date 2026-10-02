"""Real offline Host runner: delegates identity/evidence to the shared strict kit."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_validation import main

if __name__ == '__main__':
    raise SystemExit(main(['host-unit', *sys.argv[1:]]))
