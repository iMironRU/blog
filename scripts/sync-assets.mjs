// Копирует картинки постов в public/assets/<slug>/ и скрипт админки в public/admin/.
// Картинки лежат рядом с постом (так их кладёт CMS), на сайте они раздаются из /assets/<slug>/.
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { join } from "node:path";

const IMG = /\.(jpe?g|png|webp|gif|svg)$/i;
const out = "public/assets";
rmSync(out, { recursive: true, force: true });
for (const dir of readdirSync("posts", { withFileTypes: true })) {
  if (!dir.isDirectory()) continue;
  const slug = dir.name.replace(/^\d{4}-\d{2}-\d{2}-/, "");
  for (const f of readdirSync(join("posts", dir.name))) {
    if (!IMG.test(f)) continue;
    mkdirSync(join(out, slug), { recursive: true });
    cpSync(join("posts", dir.name, f), join(out, slug, f));
  }
}
const cms = "node_modules/@sveltia/cms/dist/sveltia-cms.js";
if (existsSync(cms)) cpSync(cms, "public/admin/sveltia-cms.js");
else console.warn("sveltia-cms.js не найден: выполните npm install");
