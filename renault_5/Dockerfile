ARG BUILD_FROM
FROM ${BUILD_FROM}

# Version, exposed to the app as R5_VERSION (release.yaml passes BUILD_VERSION from
# config.yaml's version; deploy.py pins dashboard assets to the matching v<version> tag).
ARG BUILD_VERSION="dev"
ENV R5_VERSION=${BUILD_VERSION}

# renault-api needs Python 3 + a few build deps for its crypto/aiohttp wheels.
RUN apk add --no-cache python3 py3-pip

COPY app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY app/ /app/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Supervisor monitors this; a deadlocked event loop can't answer /healthz -> unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8099/healthz || exit 1

CMD [ "/run.sh" ]
