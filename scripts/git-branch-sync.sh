#!/usr/bin/env bash
# git-branch-sync.sh
# Summarize local branch push status vs origin and optionally show details.
#
# Features:
#  - Ensures remote refs are fresh (git fetch) unless --no-fetch given.
#  - Table of local branches with upstream, ahead/behind counts.
#  - Lists branches with no upstream tracking branch.
#  - Shows unpushed commits for current branch (default) or all branches with --logs.
#  - Safe: read-only by default. Use --suggest-push to print push commands you can copy.
#  - Optional --auto-push to actually push branches that are ahead (EXPERIMENTAL / careful!).
#
# Usage:
#   scripts/git-branch-sync.sh                # summary + current branch unpushed commits
#   scripts/git-branch-sync.sh --logs         # include per-branch unpushed commit logs
#   scripts/git-branch-sync.sh --suggest-push # show git push commands needed
#   scripts/git-branch-sync.sh --auto-push    # (careful) push all ahead branches
#   scripts/git-branch-sync.sh --no-fetch     # skip git fetch (use current refs)
#   scripts/git-branch-sync.sh --help         # help text
#
# Exit codes:
#   0 success
#   1 generic error

set -euo pipefail

SHOW_LOGS=0
SUGGEST_PUSH=0
AUTO_PUSH=0
DO_FETCH=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --logs) SHOW_LOGS=1 ; shift ;;
    --suggest-push) SUGGEST_PUSH=1 ; shift ;;
    --auto-push) AUTO_PUSH=1 ; shift ;;
    --no-fetch) DO_FETCH=0 ; shift ;;
    --help|-h)
      grep '^# ' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Error: not inside a git repository" >&2
  exit 1
fi

if [[ $DO_FETCH -eq 1 ]]; then
  echo "==> Fetching remotes (git fetch --all --prune)" >&2
  git fetch --all --prune --quiet || echo "(fetch had warnings)" >&2
fi

current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "(detached)")

printf "\nBranch sync summary (current: %s)\n" "$current_branch"
printf "%-30s %-30s %8s %8s %s\n" "LOCAL" "UPSTREAM" "AHEAD" "BEHIND" "STATUS"
printf '%s\n' "$(printf '%.0s-' {1..95})"

mapfile -t branches < <(git for-each-ref --format='%(refname:short)' refs/heads | sort)

declare -a no_upstream
declare -a ahead_branches
declare -A ahead_counts
declare -A behind_counts

for br in "${branches[@]}"; do
  upstream=$(git rev-parse --abbrev-ref --symbolic-full-name "$br@{upstream}" 2>/dev/null || true)
  if [[ -z "$upstream" ]]; then
    printf "%-30s %-30s %8s %8s %s\n" "$br" "-" "-" "-" "NO_UPSTREAM"
    no_upstream+=("$br")
    continue
  fi
  ahead=$(git rev-list --count "$upstream..$br" 2>/dev/null || echo 0)
  behind=$(git rev-list --count "$br..$upstream" 2>/dev/null || echo 0)
  status="OK"
  if [[ $ahead -gt 0 && $behind -gt 0 ]]; then
    status="DIVERGED"
  elif [[ $ahead -gt 0 ]]; then
    status="AHEAD"
  elif [[ $behind -gt 0 ]]; then
    status="BEHIND"
  fi
  printf "%-30s %-30s %8d %8d %s\n" "$br" "$upstream" "$ahead" "$behind" "$status"
  if [[ $ahead -gt 0 ]]; then
    ahead_branches+=("$br")
    ahead_counts["$br"]=$ahead
    behind_counts["$br"]=$behind
  fi
done

echo
if (( ${#no_upstream[@]} )); then
  echo "Branches with no upstream:" >&2
  printf '  %s\n' "${no_upstream[@]}"
  echo "To set upstream: git push -u origin <branch>" >&2
  echo
fi

if [[ $SHOW_LOGS -eq 1 ]]; then
  echo "Unpushed commits per branch (ahead of upstream):"
  for br in "${ahead_branches[@]}"; do
    upstream=$(git rev-parse --abbrev-ref --symbolic-full-name "$br@{upstream}")
    echo "--- $br (ahead ${ahead_counts[$br]}, behind ${behind_counts[$br]:-0}) vs $upstream ---"
    git log --oneline "$upstream..$br"
    echo
  done
else
  # Always show current branch unpushed commits (if any) for quick view
  if git rev-parse --abbrev-ref --symbolic-full-name HEAD@{upstream} >/dev/null 2>&1; then
    upstream_curr=$(git rev-parse --abbrev-ref --symbolic-full-name HEAD@{upstream})
    commits=$(git log --oneline "$upstream_curr..HEAD" || true)
    if [[ -n "$commits" ]]; then
      echo "Unpushed commits on current branch ($current_branch -> $upstream_curr):"
      echo "$commits"
      echo
    fi
  fi
fi

if [[ $SUGGEST_PUSH -eq 1 ]]; then
  echo "Suggested push commands:"
  for br in "${ahead_branches[@]}"; do
    upstream=$(git rev-parse --abbrev-ref --symbolic-full-name "$br@{upstream}" 2>/dev/null || true)
    if [[ -n "$upstream" ]]; then
      echo "git push origin $br" # upstream already set; no -u needed
    fi
  done
  for br in "${no_upstream[@]}"; do
    echo "git push -u origin $br"
  done
  echo
fi

if [[ $AUTO_PUSH -eq 1 ]]; then
  echo "Auto-pushing ahead branches..." >&2
  for br in "${ahead_branches[@]}"; do
    echo "Pushing $br" >&2
    git push origin "$br"
  done
  for br in "${no_upstream[@]}"; do
    echo "Setting upstream & pushing $br" >&2
    git push -u origin "$br"
  done
fi

echo "Done." >&2
