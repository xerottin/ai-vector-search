"""Background service: watches folders through macOS FSEvents (variant A) and answers searches
with a pre-search freshness check (variant B) on a local-only HTTP port."""
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import embed, indexer, store
from .config import LOG_DIR, Config
from .search import format_results, search
from .sources import classify

log = logging.getLogger("ai-search")


def watch_roots(cfg: Config) -> list[str]:
    roots = {str(r) for r in cfg.file_roots}
    for patterns in cfg.chats.values():
        for p in patterns:
            base = re.split(r"[*?\[]", p, maxsplit=1)[0]
            roots.add(base if base.endswith("/") else os.path.dirname(base))
    # drop roots that are inside other roots, and missing ones
    roots = sorted(r.rstrip("/") for r in roots if os.path.isdir(r))
    return [r for r in roots if not any(r != o and r.startswith(o + "/") for o in roots)]


class Watcher(FileSystemEventHandler):
    def __init__(self, svc):
        self.svc = svc

    def on_any_event(self, event):
        if event.event_type in ("opened", "closed_no_write"):
            return
        paths = [event.src_path, getattr(event, "dest_path", "") or ""]
        if event.is_directory:
            if event.event_type in ("moved", "deleted"):
                self.svc.request_rescan()
            return
        for p in paths:
            if p:
                self.svc.touch(os.fsdecode(p))


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = store.connect()
        self.pending: dict[str, float] = {}
        self.mu = threading.Lock()
        self.rescan = True                     # full incremental scan on start
        self.state = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "last_index": None,
                      "last_stats": None, "indexing": False, "permission_errors": []}

    def touch(self, path: str):
        if path.endswith(("-wal", "-shm", "-journal", ".tmp", ".swp", "~")):
            return
        with self.mu:
            self.pending[path] = time.time()

    def request_rescan(self):
        self.rescan = True

    def _ready(self):
        now, out = time.time(), []
        with self.mu:
            for p, t in list(self.pending.items()):
                wait = self.cfg.debounce_chats_sec if "/." in p else self.cfg.debounce_files_sec
                if now - t >= wait:
                    out.append(p)
                    del self.pending[p]
        return out

    def loop(self):
        while True:
            try:
                if self.rescan:
                    self.rescan = False
                    self._run(lambda: indexer.update(self.db, self.cfg, on_progress=self._progress))
                changed, deleted = self._collect_ready()
                if changed or deleted:
                    def job():
                        with indexer.index_lock() as _:
                            return indexer.index_paths(self.db, self.cfg, changed, deleted,
                                                       on_progress=self._progress), []
                    self._run(job)
                if embed.unload_if_idle(self.cfg.model_idle_unload_min * 60):
                    log.info("model unloaded (idle)")
            except Exception:
                log.exception("index loop error")
            time.sleep(1)

    def _collect_ready(self):
        ready = self._ready()
        if not ready:
            return {}, []
        known = store.known_sources(self.db)
        changed, deleted = {}, []
        for p in ready:
            kind = classify(p, self.cfg) if os.path.isfile(p) else None
            if kind:
                changed[p] = kind
            elif p in known:
                deleted.append(p)
        return changed, deleted

    def _progress(self, n, total):
        """Called during long indexing runs (the index lock is already held). Fresh changes from the
        watcher jump the queue here, so a new file never waits for a long first indexing to finish."""
        self.state["progress"] = f"{n}/{total} источников"
        changed, deleted = self._collect_ready()
        if changed or deleted:
            stats = indexer.index_paths(self.db, self.cfg, changed, deleted)
            log.info("fresh changes indexed mid-run: %s", stats)

    def _run(self, fn):
        self.state["indexing"] = True
        self.state["progress"] = None
        try:
            stats, errors = fn()
            if errors:
                self.state["permission_errors"] = sorted(set(errors))[:20]
            if stats and stats.get("files"):
                log.info("indexed: %s", stats)
            self.state["last_index"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.state["last_stats"] = stats
        finally:
            self.state["indexing"] = False

    def handle_search(self, req: dict) -> dict:
        db = store.connect()
        try:
            note = ""
            stats, _ = indexer.update(db, self.cfg, max_changes=self.cfg.precheck_max_changes, blocking=False)
            if stats is None:
                note = "Индекс сейчас обновляется в фоне — самые свежие изменения могут не попасть в выдачу."
            elif stats.get("skipped"):
                note = f"Найдено {stats['skipped']} новых изменений — они индексируются в фоне."
                self.request_rescan()
            res = search(db, self.cfg, req["query"], req.get("limit"), req.get("kind"))
            return {"results": res, "note": note}
        finally:
            db.close()


def serve(cfg: Config):
    logging.basicConfig(filename=LOG_DIR / "daemon.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    svc = Service(cfg)
    obs = Observer()
    for r in watch_roots(cfg):
        obs.schedule(Watcher(svc), r, recursive=True)
        log.info("watching %s", r)
    obs.start()
    threading.Thread(target=svc.loop, daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/status":
                self._send(200, {**svc.state, "model_loaded": embed.is_loaded(), "pending": len(svc.pending),
                                 "watching": watch_roots(cfg)})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/search":
                return self._send(404, {"error": "not found"})
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                self._send(200, svc.handle_search(req))
            except Exception as e:
                log.exception("search failed")
                self._send(500, {"error": str(e)})

    log.info("listening on 127.0.0.1:%s", cfg.port)
    ThreadingHTTPServer(("127.0.0.1", cfg.port), H).serve_forever()


def client(cfg: Config, method: str, path: str, payload: dict | None = None, timeout: float = 120):
    import urllib.request
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{cfg.port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


__all__ = ["serve", "client", "format_results"]
