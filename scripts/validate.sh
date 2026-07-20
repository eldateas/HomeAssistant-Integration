#!/usr/bin/env bash
# Run the same validation checks as GitHub Actions (hassfest + HACS + unit tests).
#
# Usage:
#   ./scripts/validate.sh              # all checks
#   ./scripts/validate.sh hassfest     # only hassfest
#   ./scripts/validate.sh hacs         # only HACS
#   ./scripts/validate.sh tests        # only unit tests
#
# Environment:
#   GITHUB_TOKEN       Optional; improves HACS checks that call the GitHub API
#                      (description, topics, issues, brands, archived).
#   GITHUB_REPOSITORY  Defaults to git remote owner/name if detectable.
#   HACS_IGNORE        Space-separated HACS checks to skip (empty for default-store grade).
#                      Example for offline/local-only:
#                        HACS_IGNORE="brands description topics issues archived"
#   CORE_PATH          Optional path to a Home Assistant Core checkout (for
#                      hassfest fallback without Docker). Defaults to
#                      ../HomeAssistant-Core relative to this repo.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RED=$'\033[31m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

INTEGRATION_PATH="$ROOT/custom_components/easywave"

need_docker() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

detect_repository() {
  if [[ -n "${GITHUB_REPOSITORY:-}" ]]; then
    printf '%s' "$GITHUB_REPOSITORY"
    return
  fi
  local url
  url="$(git remote get-url origin 2>/dev/null || true)"
  if [[ "$url" =~ github\.com[:/]([^/]+)/([^/.]+)(\.git)?$ ]]; then
    printf '%s/%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return
  fi
  printf ''
}

detect_core_path() {
  if [[ -n "${CORE_PATH:-}" ]]; then
    printf '%s' "$CORE_PATH"
    return
  fi
  local candidate
  for candidate in \
    "$ROOT/../HomeAssistant-Core" \
    "$HOME/HomeAssistant-Core" \
    "$ROOT/../core"; do
    if [[ -f "$candidate/script/hassfest/__main__.py" ]]; then
      printf '%s' "$(cd "$candidate" && pwd)"
      return
    fi
  done
  printf ''
}

run_hassfest_docker() {
  echo "${BOLD}▶ Hassfest${RESET} (ghcr.io/home-assistant/hassfest)"
  docker pull -q ghcr.io/home-assistant/hassfest >/dev/null
  docker run --rm \
    -v "$ROOT:/github/workspace" \
    ghcr.io/home-assistant/hassfest
}

run_hassfest_core() {
  local core_path="$1"
  echo "${BOLD}▶ Hassfest${RESET} (local Core: $core_path)"
  if [[ ! -x "$core_path/.venv/bin/python" ]]; then
    echo "${RED}Core venv missing at $core_path/.venv — run script/setup there first.${RESET}" >&2
    return 1
  fi
  (
    cd "$core_path"
    export PATH="$core_path/.venv/bin:$PATH"
    .venv/bin/python -m script.hassfest \
      --action validate \
      --integration-path "$INTEGRATION_PATH"
  )
}

run_hassfest() {
  if need_docker; then
    run_hassfest_docker
  else
    echo "${YELLOW}Docker unavailable — trying Home Assistant Core hassfest fallback.${RESET}"
    local core_path
    core_path="$(detect_core_path)"
    if [[ -z "$core_path" ]]; then
      echo "${RED}Neither Docker nor a Core checkout was found.${RESET}" >&2
      echo "Install Docker, or set CORE_PATH=/path/to/HomeAssistant-Core" >&2
      exit 1
    fi
    run_hassfest_core "$core_path"
  fi
  echo "${GREEN}✓ Hassfest passed${RESET}"
}

run_hacs() {
  if ! need_docker; then
    echo "${RED}Docker is required for HACS validation (ghcr.io/hacs/action).${RESET}" >&2
    echo "Install/start Docker, then re-run: ./scripts/validate.sh hacs" >&2
    exit 1
  fi

  local repo
  repo="$(detect_repository)"
  echo "${BOLD}▶ HACS${RESET} (ghcr.io/hacs/action:main)"
  if [[ -z "$repo" ]]; then
    echo "${YELLOW}Warning: could not detect GITHUB_REPOSITORY; set it explicitly.${RESET}"
  else
    echo "  repository: $repo"
  fi
  if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    echo "${YELLOW}Warning: GITHUB_TOKEN unset — GitHub metadata checks may fail.${RESET}"
    echo "${YELLOW}  Tip: export GITHUB_TOKEN=…  or set HACS_IGNORE=\"brands description topics issues archived\"${RESET}"
  fi

  docker pull -q ghcr.io/hacs/action:main >/dev/null
  docker run --rm \
    -e INPUT_CATEGORY=integration \
    -e INPUT_IGNORE="${HACS_IGNORE:-}" \
    -e INPUT_COMMENT=false \
    -e GITHUB_TOKEN="${GITHUB_TOKEN:-}" \
    -e GITHUB_REPOSITORY="${repo}" \
    -e GITHUB_REPOSITORY_OWNER="${repo%%/*}" \
    -v "$ROOT:/github/workspace" \
    ghcr.io/hacs/action:main
  echo "${GREEN}✓ HACS passed${RESET}"
}

run_tests() {
  echo "${BOLD}▶ Unit tests${RESET}"
  if command -v python3 >/dev/null 2>&1; then
    python3 -m pytest tests/easywave -q --tb=short
  else
    python -m pytest tests/easywave -q --tb=short
  fi
  echo "${GREEN}✓ Unit tests passed${RESET}"
}

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \?//'
}

main() {
  local target="${1:-all}"
  case "$target" in
    -h|--help|help) usage; exit 0 ;;
    all)
      run_hassfest
      if need_docker; then
        run_hacs
      else
        echo "${YELLOW}Skipping HACS (Docker not available).${RESET}"
      fi
      run_tests
      ;;
    hassfest) run_hassfest ;;
    hacs) run_hacs ;;
    tests|pytest|unit) run_tests ;;
    *)
      echo "${RED}Unknown target: $target${RESET}" >&2
      usage >&2
      exit 2
      ;;
  esac
  echo
  echo "${GREEN}${BOLD}All requested checks passed.${RESET}"
}

main "$@"
