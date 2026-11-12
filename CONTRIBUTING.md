# Contributing

Thanks for your interest in ai-search! Issues and pull requests are welcome, in English or Russian.

## Development setup

```bash
git clone https://github.com/xerottin/ai-search-vector.git ~/ai-search
cd ~/ai-search
./install.sh          # creates .venv, downloads the model, starts the service
```

Run the CLI straight from the checkout while you work:

```bash
./ai-search search "test query"
./ai-search index --only chats
./ai-search restart   # after changing daemon code or config.toml
./ai-search logs
```

To experiment without touching your real index, point the data directory elsewhere:

```bash
AI_SEARCH_HOME=/tmp/ai-search-dev ./ai-search index --only chats
```

## Project layout

| Path | What it is |
|---|---|
| `ai_search/sources.py` | finds sources and parses chats / files into text |
| `ai_search/indexer.py` | chunking, secret masking, dedup, incremental updates |
| `ai_search/embed.py` | the embedding model (bge-m3) |
| `ai_search/store.py` | SQLite schema: sqlite-vec + FTS5 |
| `ai_search/search.py` | hybrid search and RRF merge |
| `ai_search/daemon.py` | background service: FSEvents watcher + local HTTP API |
| `templates/` | agent instructions and the launchd plist |

See [HOW-IT-WORKS.md](HOW-IT-WORKS.md) for a detailed walkthrough.

## Good first contributions

- Support for another AI agent's chat format (add a parser in `sources.py` and a pattern in `config.toml`).
- Linux support (systemd user service instead of launchd, inotify instead of FSEvents).
- English CLI messages / i18n.
- Better secret-masking patterns.

## Pull requests

- Keep changes focused; one topic per PR.
- Make sure `bash -n *.sh ai-search` and `python -m compileall ai_search` pass (CI runs them).
- Never commit real chat logs, indexes or secrets in tests or examples.
