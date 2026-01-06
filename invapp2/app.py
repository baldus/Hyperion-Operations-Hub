import os
import sys
from pathlib import Path

# Ensure the project root (one level above this file) is on the import path so
# the operations monitor package can be imported regardless of the working
# directory used to launch the app.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from invapp import create_app
from ops_monitor.launcher import launch_monitor_process

app = create_app()

if __name__ == "__main__":
    # Development fallback: the production entry point now uses Gunicorn.
    port = int(os.getenv("PORT", 5000))
    log_file = PROJECT_ROOT / "support" / "operations.log"
    restart_cmd = f"{sys.executable} {Path(__file__).name}"
    launch_monitor_process(
        target_pid=os.getpid(),
        app_port=port,
        log_file=log_file,
        restart_cmd=restart_cmd,
        service_name="Hyperion Operations Hub",
    )
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
