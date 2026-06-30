import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("ATL_DB_PATH", str(BASE_DIR / "autonomy_test_logger.db")))
EXPORTS_DIR = Path(os.getenv("ATL_EXPORTS_DIR", str(BASE_DIR / "exports")))
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
