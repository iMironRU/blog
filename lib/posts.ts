import fs from "node:fs";
import path from "node:path";
import matter from "gray-matter";
import { marked } from "marked";

const POSTS = path.join(process.cwd(), "posts");
export const SITE_URL = (process.env.SITE_URL || "https://blog.example.ru").replace(/\/$/, "");
const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export type Post = {
  slug: string;
  title: string;
  date: Date;
  summary: string;
  cover?: string;
  publish_to: string[];
  html: string;
};

export type PublishedState = Record<string, Record<string, { url?: string; block?: boolean } | string>>;

export function readPublished(): PublishedState {
  try {
    return JSON.parse(fs.readFileSync(path.join(process.cwd(), ".published.json"), "utf8"));
  } catch {
    return {};
  }
}

export function getPosts(drafts = false): Post[] {
  if (!fs.existsSync(POSTS)) return [];
  const posts: Post[] = [];
  for (const name of fs.readdirSync(POSTS)) {
    const dir = path.join(POSTS, name);
    const file = path.join(dir, "index.md");
    try {
      if (!fs.statSync(dir).isDirectory()) continue;
    } catch { continue; }
    if (!fs.existsSync(file)) continue;
    const { data, content } = matter(fs.readFileSync(file, "utf8"));
    if (!drafts && data.draft) continue;
    const slug = name.replace(/^\d{4}-\d{2}-\d{2}-/, "");
    // картинки в тексте лежат рядом с постом, на сайте они в /assets/<slug>/
    const md = content.replace(/(!\[[^\]]*\]\()(?!https?:|\/)([^)\s]+)/g, `$1${BASE}/assets/${slug}/$2`);
    posts.push({
      slug,
      title: String(data.title),
      date: new Date(data.date),
      summary: String(data.summary ?? ""),
      cover: data.cover ? String(data.cover) : undefined,
      publish_to: data.publish_to ?? [],
      html: marked.parse(md, { async: false }) as string,
    });
  }
  return posts.sort((a, b) => b.date.getTime() - a.date.getTime());
}

export const PLATFORM_NAMES: Record<string, string> = { telegram: "Telegram", telegraph: "Telegraph", teletype: "Teletype", vk: "VK", max: "MAX", dzen: "Дзен" };

/** Ссылки на копии поста для блока «Читайте где удобно». */
export function copyLinks(post: Post, state: PublishedState) {
  const st = state[post.slug] ?? {};
  return Object.entries(PLATFORM_NAMES).flatMap(([k, name]) => {
    const e = st[k];
    return e && typeof e === "object" && e.url ? [{ name, url: e.url }] : [];
  });
}

/** В RSS (Дзен) пост попадает только после того, как все его анонсы получили блок ссылок. */
export function readyForRss(post: Post, state: PublishedState) {
  const st = state[post.slug];
  if (!st) return false;
  return post.publish_to.every((k) => {
    const e = st[k];
    return e && typeof e === "object" && e.block;
  });
}
