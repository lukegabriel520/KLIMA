#!/usr/bin/env bash
# KLIMA AWS extractor — CSRF + cookie session against panahon.gov.ph
# Usage: ./api.sh [parameter]
# stdout: JSON only | stderr: diagnostics
set -euo pipefail

PARAM="${1:-rainfall}"
BASE_URL="${PAGASA_BASE_URL:-https://panahon.gov.ph}"
MAX_RETRIES="${API_MAX_RETRIES:-3}"
CONNECT_TIMEOUT="${API_CONNECT_TIMEOUT:-10}"
MAX_TIME="${API_MAX_TIME:-30}"

TMPDIR_WORK="$(mktemp -d "${TMPDIR:-/tmp}/klima-aws.XXXXXX")"
COOKIE_JAR="${TMPDIR_WORK}/cookies.txt"
INDEX_HTML="${TMPDIR_WORK}/index.html"
BODY_FILE="${TMPDIR_WORK}/body.json"

cleanup() {
  rm -rf "${TMPDIR_WORK}"
}
trap cleanup EXIT

log() {
  printf '%s\n' "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

pick_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    die "python3/python required for URL encoding and JSON validation"
  fi
}

PYTHON_BIN="$(pick_python)"

# Allow only known-safe parameter characters
[[ "${PARAM}" =~ ^[A-Za-z0-9_-]+$ ]] || die "Invalid parameter: ${PARAM}"

fetch_with_retry() {
  local url="$1"
  local out="$2"
  local cookie_mode="$3" # write | read
  local attempt=1
  local http_code=0

  while (( attempt <= MAX_RETRIES )); do
    if [[ "${cookie_mode}" == "write" ]]; then
      http_code="$(curl -sS -L \
        --connect-timeout "${CONNECT_TIMEOUT}" \
        --max-time "${MAX_TIME}" \
        -c "${COOKIE_JAR}" \
        -o "${out}" \
        -w '%{http_code}' \
        "${url}" || true)"
    else
      http_code="$(curl -sS -L \
        --connect-timeout "${CONNECT_TIMEOUT}" \
        --max-time "${MAX_TIME}" \
        -b "${COOKIE_JAR}" \
        -o "${out}" \
        -w '%{http_code}' \
        "${url}" || true)"
    fi

    if [[ "${http_code}" =~ ^2[0-9][0-9]$ ]]; then
      return 0
    fi
    log "Attempt ${attempt}/${MAX_RETRIES} failed (HTTP ${http_code}) for ${url}"
    sleep "$(( attempt * 2 ))"
    (( attempt++ )) || true
  done
  return 1
}

log "Fetching CSRF session for parameter=${PARAM}"
fetch_with_retry "${BASE_URL}/" "${INDEX_HTML}" write \
  || die "Failed to fetch homepage after ${MAX_RETRIES} retries"

TOKEN="$(grep -oE 'meta name="csrf-token" content="[^"]+"' "${INDEX_HTML}" \
  | sed -E 's/.*content="([^"]+)".*/\1/' \
  | head -n1 || true)"

[[ -n "${TOKEN}" ]] || die "CSRF token not found in homepage HTML"

ENCODED_TOKEN="$("${PYTHON_BIN}" -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "${TOKEN}")"

API_URL="${BASE_URL}/api/v1/aws?token=${ENCODED_TOKEN}&parameter=${PARAM}"
fetch_with_retry "${API_URL}" "${BODY_FILE}" read \
  || die "API request failed after ${MAX_RETRIES} retries"

"${PYTHON_BIN}" -c '
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    payload = json.load(f)
if not isinstance(payload, dict) or not payload.get("success") or "data" not in payload:
    raise SystemExit("Invalid API response structure")
if not isinstance(payload["data"], list):
    raise SystemExit("API data is not a list")
print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
' "${BODY_FILE}"
