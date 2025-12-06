#!/bin/bash
# Script to bundle recent documentation (excluding archive folder) into a zip file
# This allows AI agents to receive and understand how to interact with Atlas UI 3

set -e

# Get the script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOCS_DIR="${PROJECT_ROOT}/docs"
OUTPUT_DIR="${1:-${PROJECT_ROOT}}"
OUTPUT_FILE="${OUTPUT_DIR}/atlas-ui-3-docs.zip"

# Ensure docs directory exists
if [ ! -d "${DOCS_DIR}" ]; then
    echo "Error: docs directory not found at ${DOCS_DIR}"
    exit 1
fi

# Create temporary directory for bundling
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "${TEMP_DIR}"' EXIT

echo "Bundling recent documentation..."
echo "Source: ${DOCS_DIR}"
echo "Output: ${OUTPUT_FILE}"

# Copy docs to temp directory, excluding archive folder
mkdir -p "${TEMP_DIR}/docs"
rsync -av --exclude='archive' "${DOCS_DIR}/" "${TEMP_DIR}/docs/"

# Create the zip file
cd "${TEMP_DIR}"
zip -r "${OUTPUT_FILE}" docs/

echo "Documentation bundle created successfully: ${OUTPUT_FILE}"
echo "Contents:"
unzip -l "${OUTPUT_FILE}"
