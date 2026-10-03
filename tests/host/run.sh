#!/bin/sh
set -eu
python3 "$(dirname "$0")/run.py"
python3 "$(dirname "$0")/info_handler_run.py"
