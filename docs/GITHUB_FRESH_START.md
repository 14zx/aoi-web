# Удалить репозиторий на GitHub и залить заново

## 1. Удалить старый репозиторий (в браузере)

1. Откройте https://github.com/14zx/aoi-web/settings  
2. Внизу **Danger Zone** → **Delete this repository**  
3. Подтвердите имя `14zx/aoi-web`

История с ФИО и `cursoragent` на GitHub исчезнет вместе с репозиторием.

## 2. Подготовить чистый Git локально (Git Bash)

```bash
cd "/c/Users/Neizy/3D Objects/diplome/diplome"

git config --global user.name "14zx"
git config --global user.email "14zx@users.noreply.github.com"

bash scripts/fresh_github_publish.sh
```

Должен остаться **один** коммит от **14zx**, без `Co-authored-by`.

## 3. Создать новый репозиторий на GitHub

1. https://github.com/new  
2. Repository name: **aoi-web**  
3. **Private** (или Public)  
4. **Без** README, .gitignore, license  
5. **Create repository**

## 4. Залить код

```bash
git remote remove origin 2>/dev/null || true
git remote add origin https://github.com/14zx/aoi-web.git
git push -u origin master
```

Логин: **14zx**, пароль: токен **ghp_…** с галочками **repo** и **workflow**.

## 5. Actions

Через 1–2 минуты: https://github.com/14zx/aoi-web/actions  

- **CI** — тесты  
- **Portable Windows** — сборка ZIP (~30 мин)

Токен и `credential.helper manager` настраивать так же, как раньше.
