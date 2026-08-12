#!/usr/bin/env bash
# einstein-21: cron entry point for a nightly gap-detection run.
#
# Wraps `uv run einstein` with a fixed domain and writes a timestamped JSON
# report under $EINSTEIN_REPORT_DIR (default: ./reports). Exits nonzero and
# writes no report file if the CLI itself exits nonzero (a failed live fetch
# -- see einstein/cli.py's module docstring for why that must not become a
# silent empty report).
#
# Usage (crontab -e):
#   17 3 * * *  cd /path/to/einstein && USPTO_API_KEY=... GITHUB_TOKEN=... ./scripts/nightly_run.sh "quantum error correction" >> reports/nightly.log 2>&1
#
# Not a scheduler itself -- this script only runs once when invoked; cron
# (or, in CI, .github/workflows/nightly.yml) owns the "nightly" part.

set -euo pipefail

DOMAIN="${1:?usage: nightly_run.sh <domain> [repo-threshold] [patent-threshold]}"
REPO_THRESHOLD="${2:-0.2}"
PATENT_THRESHOLD="${3:-0.2}"
REPORT_DIR="${EINSTEIN_REPORT_DIR:-reports}"

mkdir -p "$REPORT_DIR"

skip_patents=()
if [ -z "${USPTO_API_KEY:-}" ]; then
  echo "USPTO_API_KEY not set -- running with --skip-patents" >&2
  skip_patents=(--skip-patents)
fi

timestamp="$(TZ=UTC date +%Y%m%dT%H%M%SZ)"
report_path="$REPORT_DIR/${timestamp}_$(echo "$DOMAIN" | tr -c 'a-zA-Z0-9' '-').json"

uv run einstein "$DOMAIN" \
  --repo-threshold "$REPO_THRESHOLD" \
  --patent-threshold "$PATENT_THRESHOLD" \
  "${skip_patents[@]}" \
  --json --output "$report_path"

echo "wrote $report_path" >&2
