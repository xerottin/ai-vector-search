"""Hybrid search: vector similarity (meaning) + FTS5 trigram (exact words), merged with RRF."""
import re

import numpy as np

from . import embed
from .config import Config

RRF_K = 60
CANDIDATES = 80
CYR = re.compile(r"[а-яё]", re.I)


def fts_query(query: str) -> str | None:
    terms = []
    for w in re.findall(r"[\w\-\.]+", query.lower()):
        w = w.strip(".-")
        if CYR.search(w) and len(w) >= 6:
            w = w[:-2]                      # crude Russian stemming: drop the ending
        if len(w) >= 3:
            terms.append('"' + w.replace('"', "") + '"')
    return " OR ".join(dict.fromkeys(terms)) or None


def search(db, cfg: Config, query: str, limit: int | None = None, kind: str | None = None) -> list[dict]:
    limit = limit or cfg.limit
    ranks: dict[int, float] = {}
    sims: dict[int, float] = {}

    qv = embed.encode([query], cfg.model)[0]
    rows = db.execute("SELECT rowid, distance FROM vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                      (qv.astype(np.float32).tobytes(), CANDIDATES)).fetchall()
    for r, (tid, dist) in enumerate(rows):
        ranks[tid] = ranks.get(tid, 0) + 1 / (RRF_K + r)
        sims[tid] = 1 - dist

    fq = fts_query(query)
    if fq:
        try:
            rows = db.execute("SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
                              (fq, CANDIDATES)).fetchall()
        except Exception:
            rows = []
        for r, (tid,) in enumerate(rows):
            ranks[tid] = ranks.get(tid, 0) + 1 / (RRF_K + r)

    results = []
    for tid, score in sorted(ranks.items(), key=lambda x: -x[1]):
        q = "SELECT path, kind, agent, title, project, ts, loc FROM chunks WHERE text_id = ?"
        args = [tid]
        if kind:
            q += " AND kind = ?"
            args.append(kind)
        places = db.execute(q + " ORDER BY ts DESC", args).fetchall()
        if not places:
            continue
        text = db.execute("SELECT text FROM texts WHERE id = ?", (tid,)).fetchone()[0]
        path, k, agent, title, project, ts, loc = places[0]
        results.append({
            "score": round(score, 4), "similarity": round(sims.get(tid, 0.0), 3),
            "kind": k, "agent": agent, "path": path, "title": title, "project": project,
            "ts": ts, "loc": loc, "also": len({p[0] for p in places}) - 1,
            "text": snippet(text, query, cfg.snippet_chars),
        })
        if len(results) >= limit:
            break
    return results


def snippet(text: str, query: str, width: int) -> str:
    if len(text) <= width:
        return text
    low = text.lower()
    words = [w[:-2] if CYR.search(w) and len(w) >= 6 else w for w in re.findall(r"\w{3,}", query.lower())]
    pos = min((low.find(w) for w in words if w in low), default=0)
    start = max(0, pos - width // 3)
    return ("…" if start else "") + text[start:start + width] + ("…" if start + width < len(text) else "")


def format_results(results: list[dict], note: str = "") -> str:
    if not results:
        return (note + "\n" if note else "") + "Ничего не найдено."
    out = [note] if note else []
    for i, r in enumerate(results, 1):
        date = (r["ts"] or "")[:16].replace("T", " ")
        if r["kind"] == "chat":
            head = f"[{i}] переписка · {r['agent']} · {date} · {r['loc']}"
            where = f"    Сессия: {r['title'] or '(без названия)'}"
            if r["project"]:
                where += f" | папка: {r['project']}"
            where += f"\n    Файл сессии: {r['path']}"
        else:
            head = f"[{i}] файл · {date}"
            where = f"    {r['path']} · {r['loc']}"
        if r["also"]:
            where += f"\n    (этот же текст ещё в {r['also']} местах)"
        body = "    " + r["text"].replace("\n", "\n    ")
        out.append(f"{head} · близость {r['similarity']}\n{where}\n{body}\n")
    return "\n".join(out)
