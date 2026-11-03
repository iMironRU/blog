# Блог: один пост, много площадок

Пост = `posts/YYYY-MM-DD-slug/index.md` (+ `cover.jpg`). После `git push` в main:
сборка Next.js (статический экспорт) → Pages → анонс в Telegram → правка анонса блоком «Читайте где удобно» → пересборка с RSS.

Front matter: `title`, `date`, `draft`, `summary` (коротко: с блоком ссылок подпись к фото ≤ 1024 символов), `cover`, `publish_to: [telegram]`.

## Админка
`<сайт>/admin/` — Sveltia CMS: список постов, Markdown-редактор, обложка, чекбоксы площадок, «Черновик».
Вход: GitHub → «Sign in with token» (fine-grained токен на этот репозиторий, Contents: read/write).
Сохранение = коммит в main, дальше срабатывает CI. Репозиторий указан в `public/admin/config.yml` (`backend.repo`).
Локально без токена: `npm run dev`, открыть `/admin/` и выбрать папку проекта («Work with Local Repository»).

## Настройка
- Repository variables: `SITE_URL` (адрес сайта без слэша в конце), `BASE_PATH` (только если Pages без своего домена, например `/blog`).
- Secrets: `TG_TOKEN` (бот — админ канала), `TG_CHAT` (`@username` или `-100…`).
- Settings → Pages → Source: GitHub Actions.

## Локально
```
npm i && npm run dev
DRY_RUN=1 TG_TOKEN=x TG_CHAT=@x python3 scripts/crosspost.py   # показать, что бы отправилось
```
Повторная публикация: удалить запись поста из `.published.json`.
Дзен: подключить `<SITE_URL>/rss.xml` в Дзен-студии.
