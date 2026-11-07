#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
"${PYTHON:-python3}" -m pytest -p pytest_asyncio.plugin -p pytest_socket --disable-socket --allow-unix-socket -q tests
