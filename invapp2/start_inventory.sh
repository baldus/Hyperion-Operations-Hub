#!/bin/bash
# Bootstrap script to run the inventory application
# Installs dependencies and launches the Flask app
set -e

# Always run relative to this script's location
cd "$(dirname "$0")"

pip install -r requirements.txt

python app.py "$@"
