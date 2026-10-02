#!/bin/bash
# Start the mock enterprise LiteLLM proxy (see README.md).
export MOCK_LITELLM_PORT="${MOCK_LITELLM_PORT:-4010}"
python "$(dirname "$0")/main.py"
