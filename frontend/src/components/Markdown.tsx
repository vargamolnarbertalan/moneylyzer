// A tiny, safe Markdown renderer: headings, paragraphs, **bold**, *italic*, `code`, lists, quotes, rules, fenced code.
// It builds React elements only (never dangerouslySetInnerHTML); links and raw HTML stay plain text.
import { Fragment, type ReactNode } from "react";

type Block =
  | { t: "h"; level: 3 | 4 | 5; text: string }
  | { t: "p"; text: string }
  | { t: "ul"; items: string[] }
  | { t: "ol"; items: string[] }
  | { t: "quote"; text: string }
  | { t: "hr" }
  | { t: "code"; text: string };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;

export function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ t: "p", text: para.join(" ") });
    para = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] as string;

    if (/^\s*```/.test(line)) {
      flush();
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i] as string)) body.push(lines[i++] as string);
      blocks.push({ t: "code", text: body.join("\n") });
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
      flush();
      blocks.push({ t: "hr" });
      continue;
    }
    const h = HEADING.exec(line);
    if (h) {
      flush();
      // Page headings are h1/h2 already; the answer's headings start at h3.
      const level = Math.min(5, Math.max(3, (h[1] as string).length + 1)) as 3 | 4 | 5;
      blocks.push({ t: "h", level, text: h[2] as string });
      continue;
    }
    const ul = BULLET.exec(line);
    if (ul) {
      flush();
      const items = [ul[1] as string];
      while (i + 1 < lines.length && BULLET.test(lines[i + 1] as string)) items.push((BULLET.exec(lines[++i] as string) as RegExpExecArray)[1] as string);
      blocks.push({ t: "ul", items });
      continue;
    }
    const ol = ORDERED.exec(line);
    if (ol) {
      flush();
      const items = [ol[1] as string];
      while (i + 1 < lines.length && ORDERED.test(lines[i + 1] as string)) items.push((ORDERED.exec(lines[++i] as string) as RegExpExecArray)[1] as string);
      blocks.push({ t: "ol", items });
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      flush();
      const quoted = [line.replace(/^\s*>\s?/, "")];
      while (i + 1 < lines.length && /^\s*>\s?/.test(lines[i + 1] as string)) quoted.push((lines[++i] as string).replace(/^\s*>\s?/, ""));
      blocks.push({ t: "quote", text: quoted.join(" ") });
      continue;
    }
    para.push(line.trim());
  }
  flush();
  return blocks;
}

const INLINE = /(\*\*[^*]+?\*\*|__[^_]+?__|`[^`]+?`|\*[^*\s](?:[^*]*[^*\s])?\*|(?<![\w])_[^_\s](?:[^_]*[^_\s])?_(?![\w]))/g;

function inline(text: string, keyBase = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of text.matchAll(INLINE)) {
    const tok = m[0];
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const key = `${keyBase}${k++}`;
    if (tok.startsWith("**") || tok.startsWith("__")) out.push(<strong key={key}>{inline(tok.slice(2, -2), key)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={key}>{tok.slice(1, -1)}</code>);
    else out.push(<em key={key}>{inline(tok.slice(1, -1), key)}</em>);
    last = at + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ source }: { source: string }) {
  const blocks = parseBlocks(source);
  return (
    <div className="md">
      {blocks.map((b, i) => {
        switch (b.t) {
          case "h": {
            const Tag = `h${b.level}` as "h3" | "h4" | "h5";
            return <Tag key={i}>{inline(b.text)}</Tag>;
          }
          case "p":
            return <p key={i}>{inline(b.text)}</p>;
          case "ul":
            return (
              <ul key={i}>
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={i}>
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it)}</li>
                ))}
              </ol>
            );
          case "quote":
            return <blockquote key={i}>{inline(b.text)}</blockquote>;
          case "hr":
            return <hr key={i} />;
          case "code":
            return (
              <pre key={i}>
                <code>{b.text}</code>
              </pre>
            );
          default:
            return <Fragment key={i} />;
        }
      })}
    </div>
  );
}
