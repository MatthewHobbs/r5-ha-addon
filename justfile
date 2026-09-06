# r5-ha-addon governance recipes (the cross-repo `just` convention).
#
# `just ci` mirrors the static gates in .github/workflows/ci.yaml, including the same
# dependency install, so a local pass means the same thing CI does.
# NOT covered locally, and remaining remote-only gates: Hadolint, the HA add-on linter,
# Bandit / pip-audit / Trivy, and the multi-arch image build. A runtime change still needs
# the Tier-0 container boot - see CLAUDE.md.

# Local CI gate - the same commands remote CI runs, for the checks it covers.
ci: lint test

# Test env built exactly as CI builds it. requirements.txt is hash-pinned, so it installs
# alone; the core is then added --no-deps at the SHA the Dockerfile pins, so local tests
# run against the exact core the image ships.
venv:
    #!/usr/bin/env bash
    set -euo pipefail
    uv venv --python 3.14 --quiet --allow-existing .venv
    uv pip install --python .venv --quiet -r renault_5/app/requirements.txt
    uv pip install --python .venv --quiet pytest pytest-cov
    CORE_REF="$(sed -n 's/^ARG CORE_REF=//p' renault_5/Dockerfile)"
    echo "renault-mqtt pinned to ${CORE_REF}"
    uv pip install --python .venv --quiet --no-deps \
      "renault-mqtt @ git+https://github.com/MatthewHobbs/renault-mqtt@${CORE_REF}"

lint:
    yamllint -c .yamllint renault_5 repository.yaml
    shellcheck renault_5/run.sh
    ruff check renault_5/app renault_5/tests

test: venv
    .venv/bin/python -m pytest renault_5/tests -q --cov=renault_5/app --cov-report=term-missing --cov-fail-under=90
