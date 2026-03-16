"""
run.py — Entry point.

Local:
    python run.py

Google Colab:
    # In a cell:
    !pip install -q -r requirements.txt
    import nest_asyncio; nest_asyncio.apply()
    exec(open('run.py').read())
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path so `from app.xxx import ...` works
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import start_server  # noqa: E402

if __name__ == "__main__":
    start_server()
    