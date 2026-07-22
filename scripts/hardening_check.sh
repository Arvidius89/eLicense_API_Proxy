#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

failures=0

report_fail() {
  echo "[FAIL] $1" >&2
  failures=$((failures + 1))
}

report_ok() {
  echo "[OK] $1"
}

check_forbidden_tracked_files() {
  local tracked
  tracked="$(git ls-files | grep -E '(^|/)\.env$|(^|/)local\.settings\.json$|(^|/)certs/.*\.(pfx|pem|key)$' || true)"

  if [[ -n "$tracked" ]]; then
    report_fail "Forbidden files are tracked by Git:\n$tracked"
  else
    report_ok "No forbidden secret files are tracked by Git"
  fi
}

check_required_ignore_patterns() {
  local gitignore_patterns=(
    ".env"
    ".env.*"
    "!.env.example"
    "local.settings.json"
    "certs/*"
  )

  local funcignore_patterns=(
    ".env"
    ".env.*"
    "local.settings.json"
    "certs/*"
  )

  local pattern
  for pattern in "${gitignore_patterns[@]}"; do
    if ! grep -Fxq "$pattern" .gitignore; then
      report_fail ".gitignore is missing required pattern: $pattern"
    fi
  done

  for pattern in "${funcignore_patterns[@]}"; do
    if ! grep -Fxq "$pattern" .funcignore; then
      report_fail ".funcignore is missing required pattern: $pattern"
    fi
  done

  if [[ $failures -eq 0 ]]; then
    report_ok "Required ignore patterns are present in .gitignore and .funcignore"
  fi
}

archive_contains_forbidden_files() {
  local archive="$1"

  if [[ "$archive" == *.zip ]]; then
    unzip -l "$archive" | awk '{print $4}' | grep -E '(^|/)\.env($|\.[^/]+$)|(^|/)local\.settings\.json$|(^|/)certs/.*\.(pfx|pem|key)$' >/dev/null 2>&1
    return $?
  fi

  tar -tf "$archive" | grep -E '(^|/)\.env($|\.[^/]+$)|(^|/)local\.settings\.json$|(^|/)certs/.*\.(pfx|pem|key)$' >/dev/null 2>&1
  return $?
}

check_repository_archives() {
  local found_archive=0
  local archive

  while IFS= read -r archive; do
    found_archive=1
    if archive_contains_forbidden_files "$archive"; then
      report_fail "Forbidden files detected inside archive: $archive"
    else
      report_ok "Archive OK: $archive"
    fi
  done < <(find . -type f \( -name '*.zip' -o -name '*.tar' -o -name '*.tgz' -o -name '*.tar.gz' \) ! -path './.git/*' ! -path './.venv*/*')

  if [[ $found_archive -eq 0 ]]; then
    report_ok "No repository archives found to inspect"
  fi
}

main() {
  check_forbidden_tracked_files
  check_required_ignore_patterns
  check_repository_archives

  if [[ $failures -gt 0 ]]; then
    echo "Hardening check failed with $failures issue(s)." >&2
    exit 1
  fi

  echo "Hardening check passed."
}

main
