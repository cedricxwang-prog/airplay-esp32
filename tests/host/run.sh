#!/bin/sh
set -eu
python3 "$(dirname "$0")/run.py"
python3 "$(dirname "$0")/info_handler_run.py"
python3 "$(dirname "$0")/initial_setup_run.py"
python3 "$(dirname "$0")/ptp_cleanup_run.py"
python3 "$(dirname "$0")/embedded_webui_run.py"
python3 "$(dirname "$0")/ota_run.py"
node "$(dirname "$0")/test_ota_ui.js"
