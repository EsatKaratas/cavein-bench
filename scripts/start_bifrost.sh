#!/usr/bin/env bash
# Starts the Bifrost gateway with this project's config (bifrost/config.json).
# Keys are read from .env and passed to Bifrost as environment variables.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/node/bin:$HOME/.local/bin:$PATH"
set -a
source .env
set +a
exec npx -y @maximhq/bifrost -app-dir ./bifrost
