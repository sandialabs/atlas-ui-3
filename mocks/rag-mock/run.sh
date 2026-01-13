#!/bin/bash

# Activate virtual environment
source ../../.venv/bin/activate

# Run the RAG mock server with reload enabled
uvicorn main_rag_mock:app --reload --host 0.0.0.0 --port 8001
