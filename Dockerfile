# syntax=docker/dockerfile:1.6

# ─── asset builder ────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS assets

ARG TAILWIND_VERSION=4.3.0
ARG HTMX_VERSION=2.0.10
ARG ALPINE_VERSION=3.15.12
ARG CHARTJS_VERSION=4.5.1

# Automatically populated by buildx (`docker build --platform`), e.g. amd64 /
# arm64. We need it to pick the matching Tailwind standalone binary — without
# arch-aware download, arm64 builds (under QEMU) pull an x64 binary and exec 127.
ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Tailwind standalone CLI — no Node runtime needed.
# Maps Docker's TARGETARCH naming to Tailwind's release-asset naming.
RUN case "${TARGETARCH}" in \
      amd64)  TW_ARCH=x64 ;; \
      arm64)  TW_ARCH=arm64 ;; \
      arm/v7) TW_ARCH=armv7 ;; \
      *) echo "unsupported TARGETARCH=${TARGETARCH}" && exit 1 ;; \
    esac && \
    curl -fsSL -o tailwindcss \
      "https://github.com/tailwindlabs/tailwindcss/releases/download/v${TAILWIND_VERSION}/tailwindcss-linux-${TW_ARCH}" \
    && chmod +x tailwindcss

# Third-party JS libs
RUN mkdir -p out/js && \
    curl -fsSL "https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/htmx.min.js" -o out/js/htmx.min.js && \
    curl -fsSL "https://cdn.jsdelivr.net/npm/alpinejs@${ALPINE_VERSION}/dist/cdn.min.js" -o out/js/alpine.min.js && \
    curl -fsSL "https://cdn.jsdelivr.net/npm/chart.js@${CHARTJS_VERSION}/dist/chart.umd.min.js" -o out/js/chart.umd.min.js

COPY tailwind.config.js ./
COPY app/templates ./app/templates
COPY app/static ./app/static

RUN mkdir -p out/css && \
    ./tailwindcss \
      -c tailwind.config.js \
      -i app/static/css/input.css \
      -o out/css/app.css \
      --minify

# ─── runtime ──────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -u 10001 -r -s /usr/sbin/nologin -d /app jellysniff

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --from=assets /build/out/css/app.css      ./app/static/css/app.css
COPY --from=assets /build/out/js/htmx.min.js   ./app/static/js/htmx.min.js
COPY --from=assets /build/out/js/alpine.min.js ./app/static/js/alpine.min.js
COPY --from=assets /build/out/js/chart.umd.min.js ./app/static/js/chart.umd.min.js

# input.css is no longer needed in the runtime image
RUN rm -f app/static/css/input.css

RUN mkdir -p /cache && chown -R jellysniff:jellysniff /app /cache

USER jellysniff

EXPOSE 8095
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8095", "--workers", "2", "--no-access-log"]
