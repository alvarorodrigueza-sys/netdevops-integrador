from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = Path(os.getenv("NETDEVOPS_DB", DATA_DIR / "netdevops.db"))
LABCTL_CMD = Path(os.getenv("LABCTL_CMD", PROJECT_ROOT.parent / "labctl.cmd"))

DEVICES = ["cisco01", "cisco02", "fortigate01"]

POLL_INTERVAL_SECONDS = float(os.getenv("NETDEVOPS_POLL_INTERVAL", "10"))
THRESHOLD_PERCENT = float(os.getenv("NETDEVOPS_THRESHOLD", "70"))
