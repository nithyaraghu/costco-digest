import { useEffect, useState, useCallback } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "";

function relativeTime(tsSeconds) {
  if (!tsSeconds) return "";
  const diff = Date.now() / 1000 - tsSeconds;
  const h = Math.floor(diff / 3600);
  if (h < 1) return "just now";
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/finds${refresh ? "?refresh=true" : ""}`);
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      setData(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(false);
  }, [load]);

  const updated = data?.generated_at
    ? new Date(data.generated_at).toLocaleString()
    : "—";

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <div className="brand">COSTCO · NEW FINDS</div>
          <div className="sub">
            {data?.region || "—"} · last updated {updated}
          </div>
        </div>
        <button className="refresh" onClick={() => load(true)} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {data?.interests && (
        <div className="interests">
          <span className="label">FILTERING FOR</span> {data.interests}
        </div>
      )}

      {error && <div className="notice error">Couldn’t load finds: {error}</div>}

      {data?.digest && (
        <section className="panel">
          <div className="panel-title">Curated digest</div>
          <pre className="digest">{data.digest}</pre>
        </section>
      )}

      <section>
        <div className="section-head">
          <span>New mentions</span>
          <span className="count">
            {data?.count ?? 0} in last {data?.lookback_hours ?? "—"}h
          </span>
        </div>

        {loading && !data && <div className="notice">Loading…</div>}

        {data && data.items.length === 0 && !loading && (
          <div className="notice">No new finds in the window. Check back later.</div>
        )}

        <div className="grid">
          {data?.items.map((it, i) => (
            <a
              key={i}
              className="card"
              href={it.link}
              target="_blank"
              rel="noreferrer"
            >
              <div className="card-top">
                <span className="source">{it.source}</span>
                <span className="time">{relativeTime(it.ts)}</span>
              </div>
              <div className="card-title">{it.title}</div>
              {it.text && <div className="card-text">{it.text}</div>}
            </a>
          ))}
        </div>
      </section>

      <footer className="foot">
        National / regional / online chatter, filtered to you — not a live feed of your
        local warehouse. Sources are public; personal use.
      </footer>
    </div>
  );
}
