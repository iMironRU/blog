import { notFound } from "next/navigation";
import { copyLinks, getPosts, readPublished } from "@/lib/posts";

export const dynamicParams = false;

export function generateStaticParams() {
  return getPosts(true).map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const post = getPosts().find((p) => p.slug === slug);
  return post ? { title: post.title, description: post.summary } : {};
}

export default async function PostPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const post = getPosts().find((p) => p.slug === slug);
  if (!post) {
    notFound();
    return <></>;
  }
  const links = copyLinks(post, readPublished());
  return (
    <article>
      <h1 className="mb-1 text-3xl font-bold">{post.title}</h1>
      <time className="text-neutral-500" dateTime={post.date.toISOString()}>{post.date.toISOString().slice(0, 10)}</time>
      <div className="mt-6 space-y-4 [&_a]:text-blue-700 [&_a]:underline [&_h2]:mt-8 [&_h2]:text-2xl [&_h2]:font-semibold [&_img]:h-auto [&_img]:max-w-full [&_pre]:overflow-x-auto [&_pre]:bg-neutral-100 [&_pre]:p-3" dangerouslySetInnerHTML={{ __html: post.html }} />
      {links.length > 0 && (
        <p className="mt-10 border-t pt-4">
          📖 Читайте где удобно:{" "}
          {links.map((l, i) => (
            <span key={l.name}>{i > 0 && " · "}<a className="text-blue-700 underline" href={l.url}>{l.name}</a></span>
          ))}
        </p>
      )}
    </article>
  );
}
