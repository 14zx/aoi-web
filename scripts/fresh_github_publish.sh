#!/usr/bin/env bash
# Single-commit master (author 14zx, no Co-authored-by). Run in Git Bash only.
#   cd "/c/Users/Neizy/3D Objects/diplome/diplome"
#   bash scripts/fresh_github_publish.sh

set -euo pipefail
cd "$(dirname "$0")/.."

GITHUB_USER="${GITHUB_USER:-14zx}"
GITHUB_EMAIL="${GITHUB_EMAIL:-${GITHUB_USER}@users.noreply.github.com}"
MSG="${MSG:-АОИ-Web: дипломный проект, модель datasets/7, portable}"

export GIT_AUTHOR_NAME="$GITHUB_USER"
export GIT_AUTHOR_EMAIL="$GITHUB_EMAIL"
export GIT_COMMITTER_NAME="$GITHUB_USER"
export GIT_COMMITTER_EMAIL="$GITHUB_EMAIL"

echo "== Orphan branch -> one commit on master =="
git checkout --orphan fresh-master 2>/dev/null || git checkout --orphan fresh-master
git rm -rf --cached . >/dev/null 2>&1 || true
git add -A

if git diff --cached --quiet; then
  echo "Nothing to commit."
  exit 1
fi

TREE=$(git write-tree)
NEW=$(printf '%s' "$MSG" | git commit-tree "$TREE")
git reset --hard "$NEW"
git branch -D master 2>/dev/null || true
git branch -m master

git log -1 --format=full
echo "OK. Push: git push --force origin master"
