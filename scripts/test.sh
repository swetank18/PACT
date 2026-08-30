#!/usr/bin/env bash
# Run the suite.
#
# PYTHONPATH is cleared because a ROS install on a developer machine puts its
# pytest plugins there, and they fail to import under this venv. Nothing in this
# project needs them, and clearing the variable is less invasive than disabling
# plugin autoload, which would also disable the ones we do want.
set -euo pipefail
cd "$(dirname "$0")/.."
exec env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest "$@"
