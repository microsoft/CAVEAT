#!/usr/bin/env bash
# Build migration tarball(s) for moving this repo to another host. Honours .gitignore, so no
# .venv / results / .env / node_modules / .tmp* / logs leak in. See MIGRATION.md.
#
#   scripts/make_migration_bundle.sh                 # code+data only (~1.1 GB)
#   scripts/make_migration_bundle.sh --results json  # + results JSON/logs (~0.6 GB extra)
#   scripts/make_migration_bundle.sh --results full  # + all results incl. screenshots (~14 GB)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$ROOT"
OUT="$(dirname "$ROOT")"
NAME="$(basename "$ROOT")"

echo "== bundling code + data (git-clean: tracked + new, excludes ignored) =="
# git ls-files -c also lists files deleted-but-not-yet-committed (e.g. this session's removals);
# keep only paths that still exist on disk so the bundle reflects the real working tree. NUL-safe.
git ls-files -c -o --exclude-standard -z \
  | while IFS= read -r -d '' f; do [ -e "$f" ] && printf '%s\0' "$f"; done > /tmp/pf_bundle_list
tar --null -czf "$OUT/${NAME}-code.tar.gz" -T /tmp/pf_bundle_list
echo "   -> $OUT/${NAME}-code.tar.gz  ($(du -h "$OUT/${NAME}-code.tar.gz" | cut -f1))"

if [ "${1:-}" = "--results" ]; then
  case "${2:-}" in
    json)
      echo "== bundling results JSON + logs only =="
      find results \( -name '*.json' -o -name 'run.log' -o -name 'index.json' \) -print0 \
        | tar -czf "$OUT/${NAME}-results-json.tar.gz" --null -T -
      echo "   -> $OUT/${NAME}-results-json.tar.gz  ($(du -h "$OUT/${NAME}-results-json.tar.gz" | cut -f1))" ;;
    full)
      echo "== bundling full results tree (incl. screenshots) =="
      tar -czf "$OUT/${NAME}-results-full.tar.gz" results/
      echo "   -> $OUT/${NAME}-results-full.tar.gz  ($(du -h "$OUT/${NAME}-results-full.tar.gz" | cut -f1))" ;;
    *) echo "   (unknown --results mode '${2:-}'; use 'json' or 'full')" ;;
  esac
fi
rm -f /tmp/pf_bundle_list
echo "== done. Upload, unpack, then: bash scripts/setup_new_host.sh =="
