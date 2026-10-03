# r5-ha-addon governance recipes (the cross-repo `just` convention).
#
# `just ci` mirrors the static gates in .github/workflows/ci.yaml, including the same
# dependency install, so a local pass means the same thing CI does.
# NOT covered locally, and remaining remote-only gates: Hadolint, the HA add-on linter,
# Bandit / pip-audit / Trivy, and the multi-arch image build. A runtime change still needs
# the Tier-0 container boot - see CLAUDE.md.

# Local CI gate - the same commands remote CI runs, for the checks it covers.
# `parity` itself is not in here: it needs the a290 twin (a sibling checkout or a clone), so
# only its self-test is. The Parity job in ci.yaml runs the full comparison.
ci: lint pii ha-min parity-self-test docs-sync-self-test test

# Proves the release guard, the release script and the publish decision can fail, and that the
# script writes exactly what the guard accepts. The Docs sync job in ci.yaml runs the same three.
docs-sync-self-test:
    python3 scripts/docs_sync_check.py --self-test
    python3 scripts/prepare_release.py --self-test
    python3 scripts/release_decision.py --self-test

# Cut a release: turn `## Unreleased` into `## <version>` and move config.yaml's version to match,
# and nothing else (a290's ADR 0006). Commit the result as its own PR, titled
# `chore(release): <version>`; merging it publishes the image and the tag.
# `just release <version> --dry-run` only prints.
release version *flags:
    python3 scripts/prepare_release.py {{version}} {{flags}}

# Proves the parity check can fail on each kind of drift; needs nothing outside this repo.
parity-self-test:
    python3 scripts/parity_check.py --self-test

# Same command as the Parity job: diff this add-on against a290-ha-addon through the committed
# map and expected list (ADR 0001 in a290). PARITY_TWIN=<path> must be a git checkout of a290:
# its tracked files (content as in its working tree) and tracked modes are compared; anything
# else is refused rather than silently walked. Unset, it clones a290's main (network).
parity: parity-self-test
    #!/usr/bin/env bash
    set -euo pipefail
    twin="${PARITY_TWIN:-}"
    if [ -z "$twin" ]; then
      tmp="$(mktemp -d)"
      trap 'rm -rf "$tmp"' EXIT
      twin="$tmp/a290-ha-addon"
      git clone --quiet --depth 1 https://github.com/MatthewHobbs/a290-ha-addon "$twin"
    fi
    python3 scripts/parity_check.py --twin "$twin"

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

# Same command as the Security job's PII step, so a leak is caught before it is published.
pii:
    python3 scripts/pii_check.py --self-test
    python3 scripts/pii_check.py

# Same command as the Lint job's minimum-HA step (reads current stable live).
ha-min:
    python3 scripts/ha_minimum_check.py --self-test
    python3 scripts/ha_minimum_check.py renault_5/config.yaml

lint:
    yamllint -c .yamllint renault_5 repository.yaml
    shellcheck renault_5/run.sh scripts/supervisor-pilot.sh
    ruff check renault_5/app renault_5/tests scripts ui-tests

test: venv
    .venv/bin/python -m pytest renault_5/tests -q --cov=renault_5/app --cov-report=term-missing --cov-fail-under=95
