# The web image: the built screens served by Caddy, which also passes the API paths on to the
# API container. Build from the repository root:
#   docker build -f deploy/web/Dockerfile -t denial-appeal-agent-web .
#
# Given at run time: SITE_ADDRESS (see the Caddyfile). Caddy keeps its TLS certificates in /data,
# so production mounts a volume there.

# node:26-slim, the Node major version in frontend/.nvmrc; the digest names one exact image.
FROM node:26-slim@sha256:712b5d4c10e5262e60d8c027f7eb84065d3fa6a6e8fe9001d0d877bdb030b7f7 AS build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
# Exactly the versions in package-lock.json, checked against their integrity hashes.
RUN npm ci --no-audit --no-fund
# `npm run build` type-checks tests/ and the config files too, so the whole folder is copied
# (.dockerignore keeps node_modules, dist and coverage out).
COPY frontend ./
RUN npm run build


# Caddy compiled here, not taken from the image below: v2.11.7, the newest release, was built with
# a Go and two modules that have known vulnerabilities. Go 1.27.3 comes with this builder image.
# Move these versions by hand: Dependabot does not read them; the image scan fails on a new finding.
FROM caddy:2-builder-alpine@sha256:95a5e7e2692b5eb9545aadc5b90a39f6f509bee59c9508a493f83ee902eaef63 AS caddy-build

RUN xcaddy build v2.11.7 \
    --with github.com/go-jose/go-jose/v4@v4.1.6 \
    --replace golang.org/x/net=golang.org/x/net@v0.60.0 \
    --output /usr/bin/caddy


FROM caddy:2-alpine@sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5

# curl is not used (the health check is BusyBox wget); removing it also removes libcurl and
# brotli-libs. setcap lets the new binary bind ports 80 and 443 without root (a file capability
# does not survive COPY), so it runs as a plain user that owns only Caddy's own state.
COPY --from=caddy-build /usr/bin/caddy /usr/bin/caddy
RUN apk del --no-cache curl \
    && setcap cap_net_bind_service=+ep /usr/bin/caddy \
    && addgroup -S -g 10001 web \
    && adduser -S -D -H -u 10001 -G web -s /sbin/nologin web \
    && chown -R 10001:10001 /data /config

COPY deploy/web/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /frontend/dist /srv

USER 10001

EXPOSE 80 443 443/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "--spider", "http://127.0.0.1:2019/config/"]
