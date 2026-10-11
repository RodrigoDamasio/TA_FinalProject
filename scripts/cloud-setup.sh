#!/bin/bash
# Setup script for the "taller-final" cloud environment (claude.ai/code).
# Paste this file's content into the environment's "Setup script" field.
# It runs as root on Ubuntu 24.04 before Claude Code starts, and its result
# is cached, so keep it under ~5 minutes and exit 0.

# Node 24 (Next.js 16 / Vitest need it; the image ships Node 20–22).
# Pinned to the version used locally, with its official SHA-256.
NODE_VERSION=24.16.0
NODE_SHA256=d804845d34eddc21dc1092b519d643ef40b1f58ec5dec5c22b1f4bd8fabde6c9
if [ ! -x /opt/node24/bin/node ]; then
  cd /tmp \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    && echo "${NODE_SHA256}  node-v${NODE_VERSION}-linux-x64.tar.xz" | sha256sum -c - \
    && mkdir -p /opt/node24 \
    && tar -xJf "node-v${NODE_VERSION}-linux-x64.tar.xz" -C /opt/node24 --strip-components=1 \
    && rm -f "node-v${NODE_VERSION}-linux-x64.tar.xz" \
    || echo "WARNING: Node 24 install failed; frontend work needs it"
fi

# Python 3.12, the version the backend targets (the image's default may differ).
uv python install 3.12 || echo "WARNING: uv could not install Python 3.12"

exit 0
