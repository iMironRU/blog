#!/usr/bin/env python3
"""Кросспостинг постов из posts/*/index.md.

Фаза 1: публикует анонс на каждой площадке из publish_to, сохраняет id и url в .published.json.
Фаза 2: когда пост ушёл на все свои площадки, пересобирает каждое сообщение целиком
        (анонс + блок «Читайте где удобно») и заменяет им исходное. Повторный запуск ничего не задваивает.

Env: SITE_URL, DRY_RUN=1 (ничего не отправлять и не писать).
Площадки читают свои токены/чаты из env: TG_TOKEN, TG_CHAT, TGPH_TOKEN, TELETYPE_TOKEN.
Только стандартная библиотека.
"""
import hashlib
import html
import json
import os
import random
import re
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "posts"
STATE = ROOT / ".published.json"
SITE_URL = os.environ.get("SITE_URL", "https://blog.example.ru").rstrip("/")
DRY = os.environ.get("DRY_RUN") == "1"

NAMES = {"site": "Сайт", "telegram": "Telegram", "telegraph": "Telegraph",
         "teletype": "Teletype", "vk": "VK", "max": "MAX", "dzen": "Дзен"}
ORDER = ["site", "telegram", "telegraph", "teletype", "vk", "max", "dzen"]


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
    meta["body"] = m.group(2).strip()  # сырой markdown-текст поста
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


def build_block_text(parts):
    """Текст «Читайте где удобно: ссылка1 · ссылка2»."""
    if not parts:
        return ""
    links = " · ".join(f'{NAMES[k]}: {u}' for k, u in parts)
    return f"📖 Читайте где удобно:\n{links}"


def telegram_text(post, parts):
    t = f"<b>{html.escape(post['title'])}</b>"
    if post.get("summary"):
        t += f"\n\n{html.escape(post['summary'])}"
    if not parts:
        return t
    links = " · ".join(f'<a href="{html.escape(u, quote=True)}">{NAMES[k]}</a>' for k, u in parts)
    return t + f"\n\n📖 Читайте где удобно:\n{links}"


def block_hash(parts):
    return hashlib.sha1(json.dumps(parts).encode()).hexdigest()[:12]


# ---------- общие утилиты markdown ----------

def _rand_anchor():
    """Случайные 4 латинские буквы для Teletype XML."""
    return "".join(random.choices(string.ascii_letters, k=4))


def _inline_md(text):
    """Простая конвертация inline-разметки в HTML (для Teletype XML)."""
    # `code`
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # **bold**
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # *italic*
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    # [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _inline_telegraph(text):
    """Конвертация inline-разметки в массив Telegraph Node."""
    children = []
    # Простой подход: разбиваем на сегменты обычного текста и форматирования
    parts = re.split(r"(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))", text)
    for p in parts:
        if not p:
            continue
        if p.startswith("`") and p.endswith("`"):
            children.append({"tag": "code", "children": [p[1:-1]]})
        elif p.startswith("**") and p.endswith("**"):
            children.append({"tag": "strong", "children": [p[2:-2]]})
        elif p.startswith("*") and p.endswith("*") and not p.startswith("**"):
            children.append({"tag": "em", "children": [p[1:-1]]})
        elif p.startswith("[") and "](" in p:
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", p)
            if m:
                children.append({"tag": "a", "attrs": {"href": m.group(2)}, "children": [m.group(1)]})
        else:
            children.append(p)
    return children


# ---------- Markdown → Telegraph Nodes ----------

def _md_to_telegraph(text):
    """Конвертирует markdown в массив Telegraph Node."""
    nodes = []
    in_list = False
    list_items = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if in_list:
                nodes.append({"tag": "ul", "children": [{"tag": "li", "children": [t]} for t in list_items]})
                list_items = []
                in_list = False
            continue

        if stripped.startswith("## "):
            if in_list:
                nodes.append({"tag": "ul", "children": [{"tag": "li", "children": [t]} for t in list_items]})
                list_items = []
                in_list = False
            nodes.append({"tag": "h2", "children": [stripped[3:]]})
        elif stripped.startswith("# "):
            if in_list:
                nodes.append({"tag": "ul", "children": [{"tag": "li", "children": [t]} for t in list_items]})
                list_items = []
                in_list = False
            nodes.append({"tag": "h3", "children": [stripped[2:]]})
        elif stripped.startswith("> "):
            if in_list:
                nodes.append({"tag": "ul", "children": [{"tag": "li", "children": [t]} for t in list_items]})
                list_items = []
                in_list = False
            nodes.append({"tag": "blockquote", "children": [{"tag": "p", "children": _inline_telegraph(stripped[2:])}]})
        elif stripped.startswith("- ") or stripped.startswith("* "):
            in_list = True
            list_items.append(_inline_telegraph(stripped[2:]))
        elif stripped.startswith("```"):
            if in_list:
                nodes.append({"tag": "ul", "children": [{"tag": "li", "children": [t]} for t in list_items]})
                list_items = []
                in_list = False
            code_lines = []
            lang = stripped[3:].strip()
            in_list = False
            continue
        elif stripped.startswith("---") and len(stripped) >= 3:
            if in_list:
                nodes.append({"tag": "ul", "children": [{"tag": "li", "children": [t]} for t in list_items]})
                list_items = []
                in_list = False
            nodes.append({"tag": "hr"})
        elif stripped.startswith("!["):
            if in_list:
                nodes.append({"tag": "ul", "children": [{"tag": "li", "children": [t]} for t in list_items]})
                list_items = []
                in_list = False
            m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", stripped)
            if m:
                nodes.append({"tag": "img", "attrs": {"src": m.group(2)}})
        else:
            if in_list:
                nodes.append({"tag": "ul", "children": [{"tag": "li", "children": [t]} for t in list_items]})
                list_items = []
                in_list = False
            # paragraph
            c = _inline_telegraph(stripped)
            # Если есть инлайн-код с целым блоком, обернём как code_block
            nodes.append({"tag": "p", "children": c})

    if in_list:
        nodes.append({"tag": "ul", "children": [{"tag": "li", "children": [t]} for t in list_items]})

    return nodes


# ---------- Markdown → Teletype XML ----------

def _md_to_teletype_xml(text):
    """Конвертирует markdown в Teletype XML document."""
    parts = ['<document>']
    code_block = False
    code_lang = ""
    code_lines = []

    def flush_code():
        nonlocal code_block, code_lang, code_lines
        if code_lines:
            lang_attr = f' lang="{html.escape(code_lang, quote=True)}"' if code_lang else ""
            code = html.escape("\n".join(code_lines))
            parts.append(f'<code_block anchor="{_rand_anchor()}"{lang_attr}>{code}</code_block>')
            code_lines = []
            code_block = False
            code_lang = ""

    for line in text.split("\n"):
        stripped = line.rstrip()

        if code_block:
            if stripped == "```":
                flush_code()
            else:
                code_lines.append(stripped)
            continue

        if stripped.startswith("```"):
            flush_code()
            code_block = True
            code_lang = stripped[3:].strip()
            continue

        if not stripped:
            continue

        if stripped.startswith("## "):
            parts.append(f'<h2 anchor="{_rand_anchor()}">{html.escape(stripped[3:])}</h2>')
        elif stripped.startswith("# "):
            parts.append(f'<h2 anchor="{_rand_anchor()}">{html.escape(stripped[2:])}</h2>')
        elif stripped.startswith("> "):
            inner = _inline_md(stripped[2:])
            parts.append(f'<blockquote anchor="{_rand_anchor()}"><p anchor="{_rand_anchor()}">{inner}</p></blockquote>')
        elif stripped.startswith("- ") or stripped.startswith("* "):
            inner = _inline_md(stripped[2:])
            parts.append(f'<ul anchor="{_rand_anchor()}"><li anchor="{_rand_anchor()}">{inner}</li></ul>')
        elif stripped.startswith("---") and len(stripped) >= 3:
            parts.append(f'<hr anchor="{_rand_anchor()}"/>')
        elif stripped.startswith("!["):
            m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", stripped)
            if m:
                parts.append(f'<image anchor="{_rand_anchor()}" src="{html.escape(m.group(2), quote=True)}"/>')
        else:
            inner = _inline_md(stripped)
            parts.append(f'<p anchor="{_rand_anchor()}">{inner}</p>')

    flush_code()
    parts.append("</document>")
    return "\n".join(parts)


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


# ---------- Telegraph ----------

APPHDR = {"Content-Type": "application/json"}


class Telegraph:
    key = "telegraph"
    API = "https://api.telegra.ph"

    def __init__(self):
        self.token = os.environ["TGPH_TOKEN"]

    def _call(self, method, fields):
        url = f"{self.API}/{method}?access_token={self.token}"
        req = urllib.request.Request(url, json.dumps(fields).encode(), APPHDR)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Telegraph {method}: {e.read().decode()}") from e
        if not res.get("ok"):
            raise RuntimeError(f"Telegraph {method}: {res}")
        return res["result"]

    def publish(self, post, parts):
        body = post.get("body", "")
        content = _md_to_telegraph(body)
        # Добавляем блок ссылок
        block_text = build_block_text(parts)
        if block_text:
            content.append({"tag": "p", "children": [block_text]})
        result = self._call("createPage", {
            "title": post["title"],
            "content": content,
            "author_name": "iMiron",
            "author_url": SITE_URL,
            "return_content": False,
        })
        url = f"https://telegra.ph/{result['path']}"
        return {"id": result["path"], "url": url}

    def edit(self, post, entry, parts):
        body = post.get("body", "")
        content = _md_to_telegraph(body)
        block_text = build_block_text(parts)
        if block_text:
            content.append({"tag": "p", "children": [block_text]})
        result = self._call("editPage", {
            "path": entry["id"],
            "title": post["title"],
            "content": content,
            "author_name": "iMiron",
            "author_url": SITE_URL,
            "return_content": False,
        })
        url = f"https://telegra.ph/{result['path']}"
        return {"id": result["path"], "url": url}


# ---------- Teletype ----------

class Teletype:
    key = "teletype"
    API = "https://teletype.in/api"

    def __init__(self):
        self.token = os.environ["TELETYPE_TOKEN"]
        self.blog_id = int(os.environ.get("TELETYPE_BLOG_ID", "1370830"))

    def _hdr(self):
        return {
            "Authorization": self.token,
            "X-Client-Version": "Teletype.Web/4.41.26",
            "Content-Type": "application/json",
        }

    def _call(self, method, url, body=None):
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(f"{self.API}/{url}", data, self._hdr(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Teletype {method} {url}: {e.read().decode()}") from e

    def publish(self, post, parts):
        body = post.get("body", "")
        xml = _md_to_teletype_xml(body)
        block_text = build_block_text(parts)
        if block_text:
            xml = xml.rstrip("\n") + f'\n<p anchor="{_rand_anchor()}">{html.escape(block_text)}</p>\n</document>'

        payload = {
            "author_id": self.blog_id,
            "creator_id": self.blog_id,
            "title": post["title"],
            "text": xml,
            "cut_text": post.get("summary", ""),
            "visibility": "all",
        }
        result = self._call("POST", "articles", payload)
        article_id = result.get("id")
        uri = result.get("uri", "")
        url = f"{SITE_URL}/{uri}/" if uri else ""
        return {"id": article_id or uri, "uri": uri, "url": url}

    def edit(self, post, entry, parts):
        body = post.get("body", "")
        xml = _md_to_teletype_xml(body)
        block_text = build_block_text(parts)
        if block_text:
            xml = xml.rstrip("\n") + f'\n<p anchor="{_rand_anchor()}">{html.escape(block_text)}</p>\n</document>'

        article_id = entry["id"]
        payload = {
            "title": post["title"],
            "text": xml,
            "cut_text": post.get("summary", ""),
        }
        result = self._call("PUT", f"articles/{article_id}", payload)
        return {"id": result.get("id", article_id), "url": entry.get("url", "")}


# ---------- VK ----------

class VK:
    key = "vk"
    API = "https://api.vk.com/method"

    def __init__(self):
        self.token = os.environ["VK_TOKEN"]
        self.owner_id = int(os.environ.get("VK_OWNER_ID", "0"))

    def _call(self, method, params):
        params["access_token"] = self.token
        params["v"] = "5.199"
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            f"{self.API}/{method}", data,
            {"Content-Type": "application/x-www-form-urlencoded"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"VK {method}: {e.read().decode()}") from e
        if "error" in res:
            raise RuntimeError(f"VK {method}: {res['error']['error_msg']} (code {res['error']['error_code']})")
        return res["response"]

    def publish(self, post, parts):
        message = f"🔗 {post['title']}\n\n{post.get('summary', '')}"
        links = build_block_text(parts)
        if links:
            message += f"\n\n{links}"
        # Ссылка на сайт в attachments
        result = self._call("wall.post", {
            "owner_id": self.owner_id,
            "message": message.strip(),
            "from_group": 1,
            "attachments": post["site_url"],
        })
        post_id = result.get("post_id")
        url = f"https://vk.com/wall{self.owner_id}_{post_id}"
        return {"id": post_id, "url": url}

    def edit(self, post, entry, parts):
        message = f"🔗 {post['title']}\n\n{post.get('summary', '')}"
        links = build_block_text(parts)
        if links:
            message += f"\n\n{links}"
        self._call("wall.edit", {
            "owner_id": self.owner_id,
            "post_id": entry["id"],
            "message": message.strip(),
            "attachments": post["site_url"],
        })
        return entry  # id и url не меняются


# ---------- фабрика площадок ----------

def platforms():
    out = {}
    if os.environ.get("TG_TOKEN") and os.environ.get("TG_CHAT"):
        out["telegram"] = Telegram()
    if os.environ.get("TGPH_TOKEN"):
        out["telegraph"] = Telegraph()
    if os.environ.get("TELETYPE_TOKEN"):
        out["teletype"] = Teletype()
    if os.environ.get("VK_TOKEN") and os.environ.get("VK_OWNER_ID"):
        out["vk"] = VK()
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
                    print(f"[dry] {p['slug']} -> {k}: площада {NAMES.get(k, k)}")
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
                    print(f"[dry] edit {p['slug']} {k}: блок ссылок")
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