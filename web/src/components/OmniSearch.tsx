import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { KIND_LABELS } from "../format";
import type { SearchResult } from "../types";

interface Props {
  asOf: number | null;
  onSelect: (id: string) => void;
}

export function OmniSearch({ asOf, onSelect }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        input.current?.focus();
      }
      if (event.key === "Escape") {
        setOpen(false);
        input.current?.blur();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }
    const timer = window.setTimeout(() => {
      void api.search(query, asOf).then((value) => {
        setResults(value);
        setOpen(true);
      });
    }, 180);
    return () => window.clearTimeout(timer);
  }, [asOf, query]);

  return (
    <div className="omni-wrap">
      <label className="omni-search">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14"/></svg>
        <input ref={input} value={query} onChange={(event) => setQuery(event.target.value)} onFocus={() => results.length && setOpen(true)} placeholder="노드·주소·태그 검색" aria-label="옴니 검색" aria-expanded={open} />
        <kbd>⌘K</kbd>
      </label>
      {open && (
        <div className="search-results" role="listbox">
          {results.map((node) => <button key={node.id} role="option" onClick={() => { onSelect(node.id); setOpen(false); setQuery(""); }}><span className={`layer-dot ${node.layer}`} /><div><b>{node.title}</b><small>{node.kind} · {KIND_LABELS[node.kind] ?? node.kind}</small></div><code>{node.id}</code></button>)}
          {!results.length && <div className="search-empty">일치하는 노드가 없습니다.</div>}
        </div>
      )}
    </div>
  );
}
