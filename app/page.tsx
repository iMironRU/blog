import { getPosts } from "@/lib/posts";

export default function Home() {
  return (
    <ul className="space-y-3">
      {getPosts().map((p) => (
        <li key={p.slug}>
          <a className="text-blue-700 underline" href={`/${p.slug}/`}>{p.title}</a>{" "}
          <small className="text-neutral-500">{p.date.toISOString().slice(0, 10)}</small>
        </li>
      ))}
    </ul>
  );
}
