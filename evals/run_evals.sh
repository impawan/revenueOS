#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$DIR/.." && pwd )"

export PATH="$ROOT_DIR/.venv/bin:$PATH"
export PYTHONPATH="$ROOT_DIR"

echo "=============================================="
echo " Running RevenueOS RAG Pipeline Evals with Promptfoo"
echo "=============================================="

SUITE="${1:-all}"

if [ "$SUITE" = "retrieval" ] || [ "$SUITE" = "all" ]; then
    echo ""
    echo ">> [1/2] Running Retrieval Evaluation..."
    promptfoo eval -c "$ROOT_DIR/promptfooconfig-retrieval.yaml" --no-cache
fi

if [ "$SUITE" = "generation" ] || [ "$SUITE" = "all" ]; then
    echo ""
    echo ">> [2/2] Running Generation & Grounding Evaluation..."
    promptfoo eval -c "$ROOT_DIR/promptfooconfig-generation.yaml" --no-cache
fi

echo ""
echo "All eval suites completed successfully!"
