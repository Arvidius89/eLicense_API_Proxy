#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
API_KEY_HEADER_NAME="${API_KEY_HEADER_NAME:-x-api-key}"
API_KEY_VALUE="${API_KEY_VALUE:-replace-with-strong-local-api-key}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

CREATE_PAYLOAD="${ROOT_DIR}/examples/create_document.json"
ACTIVATION_PAYLOAD="${ROOT_DIR}/examples/activation_status.json"
DELETE_PAYLOAD="${ROOT_DIR}/examples/delete_document.json"

echo "[1/5] Health check"
curl -sS -i "${BASE_URL}/health"
echo

echo "[2/5] Unauthorized check (missing api key, expected 401)"
curl -sS -i -X POST "${BASE_URL}/documents" \
  -H "Content-Type: application/json" \
  --data-binary "@${CREATE_PAYLOAD}" || true
echo

echo "[3/5] POST /documents"
curl -sS -i -X POST "${BASE_URL}/documents" \
  -H "${API_KEY_HEADER_NAME}: ${API_KEY_VALUE}" \
  -H "Content-Type: application/json" \
  --data-binary "@${CREATE_PAYLOAD}"
echo

echo "[4/5] POST /documents/activation-status"
curl -sS -i -X POST "${BASE_URL}/documents/activation-status" \
  -H "${API_KEY_HEADER_NAME}: ${API_KEY_VALUE}" \
  -H "Content-Type: application/json" \
  --data-binary "@${ACTIVATION_PAYLOAD}"
echo

echo "[5/5] POST /documents/delete"
curl -sS -i -X POST "${BASE_URL}/documents/delete" \
  -H "${API_KEY_HEADER_NAME}: ${API_KEY_VALUE}" \
  -H "Content-Type: application/json" \
  --data-binary "@${DELETE_PAYLOAD}"
echo
