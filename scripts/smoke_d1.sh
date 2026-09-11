#!/usr/bin/env bash
# Local/CI D1 smoke: apply migrations, boot Worker via wrangler, ingest+assert /feed.json.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v node >/dev/null; then
  echo "node is required (Node 22+)" >&2
  exit 1
fi

NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
if [[ "$NODE_MAJOR" -lt 22 ]]; then
  echo "Node 22+ required for wrangler (found $(node -v))" >&2
  exit 1
fi

if [[ ! -d node_modules/wrangler ]]; then
  npm ci
fi

npx wrangler d1 migrations apply source-watch --local --config wrangler.smoke.jsonc
node --test service/test/d1_ingest_smoke.test.mjs
