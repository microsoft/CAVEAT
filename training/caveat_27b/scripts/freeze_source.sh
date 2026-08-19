#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:---check}"

case "$mode" in
  --check)
    test -f "$root/pyproject.toml"
    test -f "$root/configs/campaign.yaml"
    git_root="$(git -C "$root" rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$git_root" && "$(realpath "$git_root")" == "$root" ]]; then
      test -z "$(git -C "$root" status --porcelain)" || {
        echo "caveat_27b source is not clean" >&2
        exit 1
      }
      git -C "$root" rev-parse HEAD
    else
      echo "source is validated but not frozen; run: scripts/freeze_source.sh --write" >&2
      exit 2
    fi
    ;;
  --write)
    git_root="$(git -C "$root" rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$git_root" && "$(realpath "$git_root")" == "$root" ]]; then
      echo "refusing to initialize over an existing Git worktree" >&2
      exit 1
    fi
    git -C "$root" init -b main
    git -C "$root" config user.name "Codex Scientific Automation"
    git -C "$root" config user.email "codex@local.invalid"
    git -C "$root" add .gitignore Makefile README.md pyproject.toml configs scripts src vendor
    git -C "$root" commit -m "Freeze CAVEAT-27B training campaign"
    git -C "$root" status --short
    ;;
  *)
    echo "usage: $0 [--check|--write]" >&2
    exit 2
    ;;
esac
