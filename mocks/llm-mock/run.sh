#!/bin/bash
export MOCK_LLM_REQUIRE_AUTH=true
export MOCK_LLM_PORT=8002
python "$(dirname "$0")/main.py"
