# Base image pinned here — the single source of truth. Since Supervisor 2026.04 no longer
# provides BUILD_FROM by default (and build.yaml's build_from is deprecated), the HA add-on
# standard is an explicit, pinned, multi-arch FROM. ghcr.io/home-assistant/base:3.24 is the
# current HA base (Alpine 3.24 / Python 3.14) and is multi-arch (amd64 + aarch64).
FROM ghcr.io/home-assistant/base:3.24

# Version, exposed to the app as R5_VERSION (release.yaml passes BUILD_VERSION from
# config.yaml's version; deploy.py pins dashboard assets to the matching v<version> tag).
ARG BUILD_VERSION="dev"
ENV R5_VERSION=${BUILD_VERSION}

# renault-api needs Python 3 + a few build deps for its crypto/aiohttp wheels.
RUN apk add --no-cache python3 py3-pip

COPY app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY app/ /app/
# Bundle the dashboard YAML in the image so deploy.py reads it locally (no runtime
# dependency on raw.githubusercontent.com). Images are still served via the jsDelivr CDN.
COPY dashboards/*.txt /app/dashboards/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Supervisor monitors this; a deadlocked event loop can't answer /healthz -> unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8099/healthz || exit 1

CMD [ "/run.sh" ]
