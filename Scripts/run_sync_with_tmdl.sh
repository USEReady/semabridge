#!/usr/bin/env bash
# Run semabridge sync with TMDL engine enabled for this invocation.
export USE_TMDL_ENGINE=true
echo "Running semabridge sync with USE_TMDL_ENGINE=${USE_TMDL_ENGINE}"

# Forward all args to the semabridge CLI
python -m semabridge sync "$@"
