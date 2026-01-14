check:
    uv run ruff format --check src tests
    uv run ruff check src tests
    uv run ty check src tests
    uv run pytest

format:
    uv run ruff format src tests

bundle:
    #!/usr/bin/env bash
    set -euo pipefail
    bundle="takopi.git.bundle"
    git bundle create "$bundle" --all
    open -R "$bundle"
