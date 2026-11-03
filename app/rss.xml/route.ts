import { SITE_URL, getPosts, readPublished, readyForRss } from "@/lib/posts";

export const dynamic = "force-static";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function GET() {
  const state = readPublished();
  const items = getPosts()
    .filter((p) => readyForRss(p, state))
    .map((p) => {
      const url = `${SITE_URL}/${p.slug}/`;
      // относительные ссылки и картинки в полном тексте делаем абсолютными
      const body = p.html.replace(/(src|href)="\/(?!\/)/g, `$1="${SITE_URL}/`);
      return `  <item>
    <title>${esc(p.title)}</title>
    <link>${url}</link>
    <guid>${url}</guid>
    <pubDate>${p.date.toUTCString()}</pubDate>
    <description>${esc(p.summary)}</description>${p.cover ? `
    <enclosure url="${SITE_URL}/assets/${p.slug}/${p.cover}" type="image/jpeg"/>` : ""}
    <content:encoded><![CDATA[${body.replace(/]]>/g, "]]]]><![CDATA[>")}]]></content:encoded>
  </item>`;
    })
    .join("\n");
  const xml = `<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>Блог</title>
  <link>${SITE_URL}/</link>
  <description>Блог про ИТ-обучение, книги и 1С</description>
  <language>ru</language>
${items}
</channel>
</rss>
`;
  return new Response(xml, { headers: { "Content-Type": "application/rss+xml; charset=utf-8" } });
}
