#!/usr/bin/env bash
# Чистая история Git (1 коммит, автор 14zx, без Co-authored-by) перед заливкой на новый GitHub.
# Запускать только в Git Bash (не в терминале Cursor):
#   cd "/c/Users/Neizy/3D Objects/diplome/diplome"
#   bash scripts/fresh_github_publish.sh

set -euo pipefail
cd "$(dirname "$0")/.."

GITHUB_USER="${GITHUB_USER:-14zx}"
GITHUB_EMAIL="${GITHUB_EMAIL:-${GITHUB_USER}@users.noreply.github.com}"
MSG="АОИ-Web: исходники дипломного проекта (АОИ.01)"

export GIT_AUTHOR_NAME="$GITHUB_USER"
export GIT_AUTHOR_EMAIL="$GITHUB_EMAIL"
export GIT_COMMITTER_NAME="$GITHUB_USER"
export GIT_COMMITTER_EMAIL="$GITHUB_EMAIL"

echo "== Чистая ветка master (без старой истории) =="
git checkout --orphan fresh-master 2>/dev/null || git checkout --orphan fresh-master
git rm -rf --cached . >/dev/null 2>&1 || true
git add -A

if git diff --cached --quiet; then
  echo "Нечего коммитить."
  exit 1
fi

TREE=$(git write-tree)
NEW=$(printf '%s' "$MSG" | git commit-tree "$TREE")
git reset --hard "$NEW"
git branch -D master 2>/dev/null || true
git branch -m master

echo ""
git log -1 --format=full
echo ""
echo "OK. Дальше на GitHub:"
echo "  1) Удалите старый репозиторий aoi-web (Settings → Delete)."
echo "  2) Создайте новый aoi-web (без README), Private/Public как нужно."
echo "  3) Выполните:"
echo "       git remote remove origin 2>/dev/null || true"
echo "       git remote add origin https://github.com/${GITHUB_USER}/aoi-web.git"
echo "       git push -u origin master"
