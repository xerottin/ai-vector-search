"""Incremental indexing: find what changed, parse, embed only new texts, write."""
import contextlib
import fcntl
import os
import sys
import time

from . import embed, store
from .config import LOCK_PATH, Config
from .sources import discover, parse

GROUP_TEXTS = 64    # embed this many new texts at once (≈4 s on Apple Silicon)


@contextlib.contextmanager
def index_lock(blocking: bool = True):
    f = open(LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        f.close()


def scan(db, cfg: Config):
    """Compare disk with the index. Returns ({path: kind} to (re)index, [deleted paths], [permission errors])."""
    found, errors = discover(cfg)
    known = store.known_sources(db)
    changed = {}
    for path, kind in found.items():
        try:
            st = os.stat(path)
        except OSError:
            continue
        if known.get(path) != (st.st_mtime, st.st_size):
            changed[path] = kind
    deleted = [p for p in known if p not in found]
    return changed, deleted, errors


def _order(changed: dict[str, str]) -> list[tuple[str, str]]:
    """Chats first, then files from the most recently modified: fresh projects become searchable first."""
    def key(item):
        path, kind = item
        try:
            m = os.path.getmtime(path)
        except OSError:
            m = 0
        return (kind == "file", -m)
    return sorted(changed.items(), key=key)


def index_paths(db, cfg: Config, changed: dict[str, str], deleted: list[str] = (), progress: bool = False,
                on_progress=None) -> dict:
    stats = {"files": 0, "chunks": 0, "embedded": 0, "removed": 0, "errors": 0}
    for p in deleted:
        store.remove_source(db, p)
        stats["removed"] += 1

    items = _order(changed)
    total, t0 = len(items), time.time()
    pending = []          # [(path, kind, stat, chunks)]
    pending_new = {}      # hash -> text needing an embedding

    def flush():
        if not pending:
            return
        hashes = list(pending_new)
        have = store.existing_text_ids(db, hashes)
        todo = [h for h in hashes if h not in have]
        vecs = embed.encode([pending_new[h] for h in todo], cfg.model) if todo else []
        with db:
            for h, v in zip(todo, vecs):
                have[h] = store.add_text(db, h, pending_new[h], v)
            for path, kind, st, chunks, err in pending:
                store.remove_source(db, path)
                for c in chunks:
                    db.execute(
                        "INSERT INTO chunks(text_id, path, kind, agent, title, project, ts, loc) VALUES (?,?,?,?,?,?,?,?)",
                        (have[store.text_hash(c.text)], c.path, c.kind, c.agent, c.title, c.project, c.ts, c.loc))
                db.execute("INSERT OR REPLACE INTO sources(path, kind, mtime, size, error) VALUES (?,?,?,?,?)",
                           (path, kind, st.st_mtime, st.st_size, err))
        stats["embedded"] += len(todo)
        pending.clear()
        pending_new.clear()

    for n, (path, kind) in enumerate(items, 1):
        try:
            st = os.stat(path)
        except OSError:
            store.remove_source(db, path)
            continue
        err = None
        try:
            chunks = parse(path, kind, cfg)
        except Exception as e:  # unreadable file: remember it, don't retry until it changes
            chunks, err = [], f"{type(e).__name__}: {e}"[:300]
            stats["errors"] += 1
        for c in chunks:
            pending_new.setdefault(store.text_hash(c.text), c.text)
        pending.append((path, kind, st, chunks, err))
        stats["files"] += 1
        stats["chunks"] += len(chunks)
        if len(pending_new) >= GROUP_TEXTS:
            flush()
        if on_progress and n % 10 == 0:
            on_progress(n, total)
        if progress and (n % 25 == 0 or n == total):
            el = time.time() - t0
            eta = el / n * (total - n)
            print(f"\r  {n}/{total} источников · {stats['chunks']} кусков · осталось ~{eta/60:.0f} мин   ",
                  end="", file=sys.stderr, flush=True)
    flush()
    with db:
        stats["pruned"] = store.prune_orphans(db)
    if progress and total:
        print(file=sys.stderr)
    return stats


def update(db, cfg: Config, progress: bool = False, max_changes: int | None = None, blocking: bool = True,
           only: str | None = None, on_progress=None):
    """Scan + index. only="chats"|"files" limits the run. Returns (stats, errors), or (None, []) if
    another process holds the index lock and blocking=False."""
    with index_lock(blocking) as got:
        if not got:
            return None, []
        changed, deleted, errors = scan(db, cfg)
        if only:
            want = (lambda k: k != "file") if only == "chats" else (lambda k: k == "file")
            known = dict(db.execute("SELECT path, kind FROM sources"))
            changed = {p: k for p, k in changed.items() if want(k)}
            deleted = [p for p in deleted if want(known.get(p, ""))]
        if max_changes is not None and len(changed) + len(deleted) > max_changes:
            return {"skipped": len(changed) + len(deleted)}, errors
        if not changed and not deleted:
            return {"files": 0}, errors
        return index_paths(db, cfg, changed, deleted, progress=progress, on_progress=on_progress), errors
