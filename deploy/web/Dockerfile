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


FROM caddy:2-alpine@sha256:eb4d35b7452d1ab6171fbdbe8d9275b2301dad6dad92115d91e19fb9b24d510f

# The caddy binary in this image may bind ports 80 and 443 without root (it carries the
# NET_BIND_SERVICE file capability), so it runs as a plain user that owns only Caddy's own state.
RUN addgroup -S -g 10001 web \
    && adduser -S -D -H -u 10001 -G web -s /sbin/nologin web \
    && chown -R 10001:10001 /data /config

COPY deploy/web/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /frontend/dist /srv

USER 10001

EXPOSE 80 443 443/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "--spider", "http://127.0.0.1:2019/config/"]
