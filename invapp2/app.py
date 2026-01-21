import os
import subprocess
import sys
from pathlib import Path

# Ensure the project root (one level above this file) is on the import path so
# the operations monitor package can be imported regardless of the working
# directory used to launch the app.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from invapp import create_app
app = create_app()

if __name__ == "__main__":
    # Development fallback: the production entry point now uses Gunicorn.
    port = int(os.getenv("PORT", 5000))
    if os.getenv("ENABLE_TERMINAL_MONITOR", os.getenv("ENABLE_OPS_MONITOR", "1")) != "0":
        monitor_script = PROJECT_ROOT / "scripts" / "monitor_launch.sh"
        if monitor_script.exists():
            subprocess.Popen(
                [str(monitor_script), "--headless"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            )
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
