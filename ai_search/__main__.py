import argparse
import json
import sys
import time

from . import config


def cmd_search(cfg, a):
    query = " ".join(a.query).strip()
    if not query:
        sys.exit("Пустой запрос.")
    kind = {"chats": "chat", "chat": "chat", "files": "file", "file": "file"}.get(a.kind or "")
    try:  # fast path: the background service already has the model in memory
        from .daemon import client
        r = client(cfg, "POST", "/search", {"query": query, "limit": a.limit, "kind": kind})
        if "error" in r:
            raise RuntimeError(r["error"])
        results, note = r["results"], r["note"]
    except Exception:
        from . import indexer, store
        from .search import search
        db = store.connect()
        note = ""
        stats, _ = indexer.update(db, cfg, max_changes=cfg.precheck_max_changes, blocking=False)
        if stats and stats.get("skipped"):
            note = (f"Фоновая служба не запущена, а непроиндексированных изменений {stats['skipped']}. "
                    "Запустите: ai-search index")
        results = search(db, cfg, query, a.limit, kind)
    if a.json:
        print(json.dumps({"results": results, "note": note}, ensure_ascii=False, indent=1))
    else:
        from .search import format_results
        print(format_results(results, note))


def cmd_index(cfg, a):
    from . import indexer, store
    if a.full and config.DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            p = config.DB_PATH.with_name(config.DB_PATH.name + suffix)
            if p.exists():
                p.unlink()
        print("Старый индекс удалён, строю заново.")
    db = store.connect()
    t0 = time.time()
    print("Ищу изменения…", file=sys.stderr)
    stats, errors = indexer.update(db, cfg, progress=True, only=a.only)
    if stats is None:
        sys.exit("Индекс сейчас обновляет фоновая служба. Подождите и повторите.")
    print(f"Готово за {time.time() - t0:.0f} с: обработано источников {stats.get('files', 0)}, "
          f"кусков {stats.get('chunks', 0)}, новых векторов {stats.get('embedded', 0)}, "
          f"удалено {stats.get('removed', 0)}, ошибок {stats.get('errors', 0)}.")
    report_permission(errors)


def report_permission(errors):
    if errors:
        print("\n⚠️  Нет доступа к некоторым папкам (macOS их защищает):")
        for e in errors[:5]:
            print("   ", e)
        print("   Разрешите доступ: Системные настройки → Конфиденциальность и безопасность →\n"
              "   Доступ к файлам и папкам (или Полный доступ к диску) → включите Терминал и python.")


def cmd_status(cfg, a):
    from . import store
    db = store.connect()
    print(f"Индекс: {config.DB_PATH}  ({config.DB_PATH.stat().st_size / 1e6:.1f} МБ)")
    print("\nИсточники:")
    for kind, n in db.execute("SELECT kind, count(*) FROM sources GROUP BY kind ORDER BY kind"):
        label = {"file": "файлы проектов"}.get(kind, "переписки " + kind.split(":", 1)[-1])
        print(f"  {label:<28} {n}")
    nchunks = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
    ntexts = db.execute("SELECT count(*) FROM texts").fetchone()[0]
    print(f"  {'кусков текста':<28} {nchunks} (уникальных {ntexts})")
    last = db.execute("SELECT max(indexed_at) FROM sources").fetchone()[0]
    print(f"  {'последняя индексация':<28} {last or '—'}")
    errs = db.execute("SELECT path, error FROM sources WHERE error IS NOT NULL LIMIT 5").fetchall()
    if errs:
        print(f"\nНе удалось прочитать ({db.execute('SELECT count(*) FROM sources WHERE error IS NOT NULL').fetchone()[0]}):")
        for p, e in errs:
            print(f"  {p}: {e}")
    print("\nФоновая служба:", end=" ")
    try:
        from .daemon import client
        s = client(cfg, "GET", "/status", timeout=3)
        print(f"работает с {s['started']}, модель {'в памяти' if s['model_loaded'] else 'выгружена'}, "
              f"{'индексирует: ' + (s.get('progress') or 'поиск изменений') if s['indexing'] else 'ждёт изменений'}, "
              f"в очереди {s['pending']}")
        print("  Следит за:", ", ".join(s["watching"]))
        report_permission(s.get("permission_errors"))
    except Exception:
        print("НЕ запущена (запустите: ai-search start)")


def cmd_model(cfg, a):
    from . import embed
    print(f"Загружаю модель {cfg.model} (при первом запуске скачивается ~2.3 ГБ)…")
    embed.encode(["проверка"], cfg.model)
    print("Модель готова.")


def main():
    cfg = config.load()
    ap = argparse.ArgumentParser(prog="ai-search", description="Поиск по смыслу в переписках с ИИ-агентами и файлах проектов")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", aliases=["q"], help="найти")
    s.add_argument("query", nargs="+")
    s.add_argument("-n", "--limit", type=int, default=None)
    s.add_argument("--kind", choices=["chats", "files", "chat", "file"])
    s.add_argument("--json", action="store_true")
    i = sub.add_parser("index", help="обновить индекс (только изменения)")
    i.add_argument("--full", action="store_true", help="удалить индекс и построить заново")
    i.add_argument("--only", choices=["chats", "files"], help="только переписки или только файлы")
    sub.add_parser("status", help="что проиндексировано")
    sub.add_parser("daemon", help="фоновая служба (её запускает launchd)")
    sub.add_parser("download-model", help="скачать модель эмбеддингов")
    a = ap.parse_args()
    if a.cmd in ("search", "q"):
        cmd_search(cfg, a)
    elif a.cmd == "index":
        cmd_index(cfg, a)
    elif a.cmd == "status":
        cmd_status(cfg, a)
    elif a.cmd == "download-model":
        cmd_model(cfg, a)
    elif a.cmd == "daemon":
        from .daemon import serve
        serve(cfg)


if __name__ == "__main__":
    main()
