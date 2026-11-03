#!/usr/bin/env python3
"""Кросспостинг постов из posts/*/index.md.

Фаза 1: публикует анонс на каждой площадке из publish_to, сохраняет id и url в .published.json.
Фаза 2: когда пост ушёл на все свои площадки, пересобирает каждое сообщение целиком
        (анонс + блок «Читайте где удобно») и заменяет им исходное. Повторный запуск ничего не задваивает.

Env: SITE_URL, TG_TOKEN, TG_CHAT (@username или -100...), DRY_RUN=1 (ничего не отправлять и не писать).
Только стандартная библиотека.
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "posts"
STATE = ROOT / ".published.json"
SITE_URL = os.environ.get("SITE_URL", "https://blog.example.ru").rstrip("/")
DRY = os.environ.get("DRY_RUN") == "1"

NAMES = {"site": "Сайт", "telegram": "Telegram", "vk": "VK", "max": "MAX", "dzen": "Дзен"}
ORDER = ["site", "telegram", "vk", "max", "dzen"]


# ---------- посты ----------

def parse_post(path: Path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        raise ValueError(f"{path}: нет front matter")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            meta[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        elif v.lower() in ("true", "false"):
            meta[k] = v.lower() == "true"
        else:
            meta[k] = v.strip("'\"")
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.parent.name)
    meta["slug"] = slug
    meta["dir"] = path.parent
    meta["site_url"] = f"{SITE_URL}/{slug}/"
    return meta


def load_posts():
    posts = [parse_post(p) for p in sorted(POSTS.glob("*/index.md"))]
    return [p for p in posts if not p.get("draft")]


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save_state(state):
    if DRY:
        return
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------- сообщения ----------

def build_block(post, st, own):
    """Блок ссылок на остальные площадки; своя площадка исключается."""
    links = {"site": post["site_url"]}
    for k in ORDER[1:]:
        if st.get(k, {}).get("url"):
            links[k] = st[k]["url"]
    parts = [(k, u) for k, u in links.items() if k != own]
    return parts


def telegram_text(post, parts):
    t = f"<b>{html.escape(post['title'])}</b>"
    if post.get("summary"):
        t += f"\n\n{html.escape(post['summary'])}"
    if not parts:
        return t
    # без блока ссылок хотя бы ссылка на сайт: она и есть «parts» с site, так что блок всегда содержит её
    links = " · ".join(f'<a href="{html.escape(u, quote=True)}">{NAMES[k]}</a>' for k, u in parts)
    return t + f"\n\n📖 Читайте где удобно:\n{links}"


def block_hash(parts):
    return hashlib.sha1(json.dumps(parts).encode()).hexdigest()[:12]


# ---------- Telegram ----------

class Telegram:
    key = "telegram"
    TEXT_LIMIT, CAPTION_LIMIT = 4096, 1024

    def __init__(self):
        self.token = os.environ["TG_TOKEN"]
        self.chat = os.environ["TG_CHAT"]

    def _call(self, method, fields, file=None):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        if file:
            boundary = uuid.uuid4().hex
            body = b""
            for k, v in fields.items():
                body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            name, data = file
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{name}"\r\n'
                     f'Content-Type: application/octet-stream\r\n\r\n').encode() + data + b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        else:
            req = urllib.request.Request(url, json.dumps(fields).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Telegram {method}: {e.read().decode()}") from e
        if not res.get("ok"):
            raise RuntimeError(f"Telegram {method}: {res}")
        return res["result"]

    def _msg_url(self, message_id):
        if self.chat.startswith("@"):
            return f"https://t.me/{self.chat[1:]}/{message_id}"
        return f"https://t.me/c/{self.chat.replace('-100', '', 1)}/{message_id}"

    def publish(self, post, parts):
        text = telegram_text(post, parts)
        cover = post.get("cover")
        cover_path = post["dir"] / cover if cover else None
        if cover_path and cover_path.exists() and len(text) <= self.CAPTION_LIMIT:
            res = self._call("sendPhoto", {"chat_id": self.chat, "caption": text, "parse_mode": "HTML"},
                             file=(cover_path.name, cover_path.read_bytes()))
            kind = "photo"
        else:
            res = self._call("sendMessage", {"chat_id": self.chat, "text": text, "parse_mode": "HTML"})
            kind = "text"
        return {"id": res["message_id"], "url": self._msg_url(res["message_id"]), "kind": kind}

    def edit(self, post, entry, parts):
        text = telegram_text(post, parts)
        limit = self.CAPTION_LIMIT if entry.get("kind") == "photo" else self.TEXT_LIMIT
        if len(text) > limit:
            raise RuntimeError(f"текст с блоком ссылок {len(text)} > {limit}: сократите summary")
        if entry.get("kind") == "photo":
            self._call("editMessageCaption", {"chat_id": self.chat, "message_id": entry["id"],
                                              "caption": text, "parse_mode": "HTML"})
        else:
            self._call("editMessageText", {"chat_id": self.chat, "message_id": entry["id"],
                                           "text": text, "parse_mode": "HTML"})


def platforms():
    out = {}
    if os.environ.get("TG_TOKEN") and os.environ.get("TG_CHAT"):
        out["telegram"] = Telegram()
    return out


# ---------- основной процесс ----------

def wait_for_site(url, tries=30, delay=10):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def main():
    posts = load_posts()
    state = load_state()
    plats = platforms()
    todo = [p for p in posts if any(k in plats and k not in state.get(p["slug"], {}) for k in p.get("publish_to", []))]

    if todo and not DRY and os.environ.get("SKIP_WAIT") != "1":
        for p in todo:
            if not wait_for_site(p["site_url"]):
                print(f"{p['slug']}: сайт не отдаёт 200, пропускаю", file=sys.stderr)
                todo = [x for x in todo if x is not p]

    failed = False

    # фаза 1
    for p in todo:
        st = state.setdefault(p["slug"], {"site": p["site_url"]})
        for k in p.get("publish_to", []):
            if k not in plats or k in st:
                continue
            try:
                parts = build_block(p, st, own=k)[:1]  # в анонсе пока только ссылка на сайт
                if DRY:
                    print(f"[dry] {p['slug']} -> {k}:\n{telegram_text(p, parts)}\n")
                    continue
                st[k] = plats[k].publish(p, parts)
                st[k]["sent"] = block_hash(parts)
                save_state(state)
                print(f"{p['slug']} -> {k}: {st[k]['url']}")
            except Exception as e:  # сбой одной площадки не блокирует остальные
                failed = True
                print(f"{p['slug']} -> {k}: ОШИБКА {e}", file=sys.stderr)

    # фаза 2: только когда пост ушёл на все свои площадки (иначе блок был бы неполным)
    for p in posts:
        st = state.get(p["slug"])
        if not st:
            continue
        wanted = [k for k in p.get("publish_to", []) if k in plats]
        if not all(k in st for k in wanted):
            continue
        for k in wanted:
            parts = build_block(p, st, own=k)
            h = block_hash(parts)
            if st[k].get("sent") == h:
                if not st[k].get("block"):
                    st[k]["block"] = True
                    save_state(state)
                continue
            try:
                if DRY:
                    print(f"[dry] edit {p['slug']} {k}:\n{telegram_text(p, parts)}\n")
                    continue
                plats[k].edit(p, st[k], parts)
                st[k]["sent"] = h
                st[k]["block"] = True
                save_state(state)
                print(f"{p['slug']}: {k} обновлён блоком ссылок")
            except Exception as e:
                failed = True
                print(f"{p['slug']} edit {k}: ОШИБКА {e}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
