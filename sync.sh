#!/bin/bash
set -e

# Load .env if it exists (for local dev)
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

if [ -z "$OMNI_API_KEY" ]; then
  echo "❌ OMNI_API_KEY is not set. Add it to .env or export it."
  exit 1
fi

echo "▶ Pulling latest changes..."
git pull

echo ""
echo "▶ Running dbt compile..."
dbt compile --no-partial-parse

echo ""
echo "▶ Running Omni sync..."
python3 sync.py
