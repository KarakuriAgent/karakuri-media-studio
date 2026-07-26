import sys
from pathlib import Path

# backend/ on sys.path so that `import app.…` works without installation.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
