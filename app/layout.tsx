import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Блог", template: "%s — Блог" },
  description: "Блог про ИТ-обучение, книги и 1С",
  alternates: { types: { "application/rss+xml": "/rss.xml" } },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="mx-auto max-w-2xl px-4 py-6 text-lg leading-relaxed text-neutral-900">
        <header className="mb-8">
          <a href="/" className="font-semibold">Блог</a>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
