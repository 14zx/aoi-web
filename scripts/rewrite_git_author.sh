#!/usr/bin/env bash
# Переписать единственный коммит: без Co-authored-by, автор = логин GitHub.
# Запускать в Git Bash (НЕ в терминале Cursor):
#   cd "/c/Users/Neizy/3D Objects/diplome/diplome"
#   bash scripts/rewrite_git_author.sh
#   git push --force origin master

set -euo pipefail
cd "$(dirname "$0")/.."

GITHUB_USER="${GITHUB_USER:-14zx}"
GITHUB_EMAIL="${GITHUB_EMAIL:-${GITHUB_USER}@users.noreply.github.com}"
MSG="${1:-АОИ-Web: исходники дипломного проекта (без весов моделей)}"

export GIT_AUTHOR_NAME="$GITHUB_USER"
export GIT_AUTHOR_EMAIL="$GITHUB_EMAIL"
export GIT_COMMITTER_NAME="$GITHUB_USER"
export GIT_COMMITTER_EMAIL="$GITHUB_EMAIL"

TREE=$(git rev-parse 'HEAD^{tree}')
NEW=$(printf '%s' "$MSG" | git commit-tree "$TREE")
git reset --hard "$NEW"

echo "---"
git log -1 --format=full
echo "---"
echo "OK. Теперь: git push --force origin master"
