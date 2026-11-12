# ai-search

**English** · [Русский](README.ru.md)

[![CI](https://github.com/xerottin/ai-search-vector/actions/workflows/ci.yml/badge.svg)](https://github.com/xerottin/ai-search-vector/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey)

**Long-term memory for your AI coding agents.** ai-search is a local semantic search over your past conversations with AI agents (Claude Code, Codex, Gemini CLI, Antigravity) and over your project files. Your agent calls it with `/search`, finds what you did before, and answers with that context.

Everything runs on your Mac. The model, the index and the search are local; nothing is sent anywhere.

Ask *"how did I check whether the server can handle lots of users"* and it finds the session about *"load testing with Locust"*, even though none of the words match.

## Features

- **Hybrid search**: meaning (bge-m3 embeddings, multilingual) + exact words (SQLite FTS5), merged with RRF.
- **Agent chats**: Claude Code, Codex, Gemini CLI, Antigravity plans and reports.
- **Project files**: code, Markdown, text, PDF, DOCX. Skips `node_modules`, `.git`, venvs, builds and secrets.
- **Always fresh**: a background service watches folders via FSEvents and re-indexes only what changed.
- **Secret masking**: API keys, tokens, passwords and private keys are masked before anything is stored; `.env`, `*.pem`, `*.key` are never read.
- **One command install** that wires `/search` into every agent it finds.

## Requirements

- macOS on Apple Silicon (M1 or newer)
- ~5 GB of free disk space
- Internet during installation (to download Python packages and the ~2.3 GB model)

## Install

```bash
git clone https://github.com/xerottin/ai-search-vector.git ~/ai-search
cd ~/ai-search
./install.sh
```

The script takes 5–10 minutes and does everything:

1. Installs [`uv`](https://github.com/astral-sh/uv) (via Homebrew, or the official installer).
2. Creates an isolated `.venv` with Python 3.12. Your system Python is not touched.
3. Downloads the embedding model `BAAI/bge-m3` (once).
4. Indexes your agent conversations.
5. Links the `ai-search` command into `~/.local/bin`.
6. Starts a background service that launches at login and indexes your project files, newest first.
7. Adds a search command to the agents you have installed:
   - Claude Code: `/search <question>`
   - Codex: `$search <question>`
   - Gemini CLI: `/search <question>`

If macOS asks *"python would like to access files in your Desktop folder"*, click **Allow**.

Re-running `./install.sh` is safe. To update: `cd ~/ai-search && git pull && ./install.sh`.

## Usage

In your agent:

```
/search how did I set up traefik
```

From the terminal:

```bash
ai-search search "how did I set up traefik"
ai-search search "CORS error" --kind chats          # only past conversations
ai-search search "sending SMS" --kind files -n 5    # only files, 5 results
ai-search search "..." --json                       # machine-readable output
```

Any other agent can use it too: just ask it to run `ai-search search "<question>"`.

Managing the service:

```bash
ai-search status          # what is indexed, whether the service runs, progress
ai-search index           # check for changes manually
ai-search index --full    # rebuild the index from scratch
ai-search stop | start | restart
ai-search logs            # service log
./uninstall.sh            # remove everything
```

> CLI messages are currently in Russian. English output is on the roadmap; contributions welcome.

## How it works

```
 Agent conversations             Project files (~/Desktop)
 ~/.claude/projects/*.jsonl      code, .md, .txt, PDF, DOCX
 ~/.codex/sessions/*.jsonl       (no node_modules, .git, venv,
 ~/.gemini/…                      build, tests, secrets)
            │                               │
            └───────────────┬───────────────┘
                            ▼
              parse → ~1500-char chunks
              → mask secrets (keys, tokens, passwords)
              → deduplicate
                            ▼
          bge-m3 model (local) → embedding vector
                            ▼
          ~/.ai-search/index.db  (SQLite)
          ├── sqlite-vec: vectors, search by meaning
          └── FTS5:       search by exact words
```

The index stays fresh in two ways: the background service re-indexes a file a few seconds after it changes, and every search first checks for recent changes in case the service was off. A typical `/search` costs the agent about 8–20k tokens; indexing itself costs no tokens.

For the full walkthrough see [HOW-IT-WORKS.md](HOW-IT-WORKS.md) (in Russian).

## Configuration

Edit `config.toml`, then run `ai-search restart`.

- `file_roots`: folders to index (default `~/Desktop`).
- `exclude`: paths to skip, e.g. `["old", "*/label-studio"]`.
- `[chats]`: where agent conversations live.
- `[index]`, `[server]`, `[search]`: model, size limits, debounce, port, result count.

## Where things live

| What | Where |
|---|---|
| Program | this folder (`.venv` inside) |
| Index and logs | `~/.ai-search/` |
| Model | `~/.cache/huggingface/hub/models--BAAI--bge-m3` |
| Terminal command | `~/.local/bin/ai-search` (symlink) |
| Background service | `~/Library/LaunchAgents/com.local.ai-search.plist` |
| Agent commands | `~/.claude/skills/search`, `~/.codex/skills/search`, `~/.gemini/commands/search.toml` |

## Privacy

- The service listens on `127.0.0.1` only.
- Secrets are masked **before** they are written to the index.
- The only data that leaves your machine is what your agent receives as `/search` results, which go to that agent's provider like any other text in your conversation.
- Masking is pattern-based and cannot catch everything. Don't keep secrets in plain notes.

## Limitations

- macOS only for now (launchd + FSEvents). Linux support is welcome.
- Antigravity stores conversations in a closed format; only its plans and reports (`brain/*.md`) are indexed.
- Images, audio, video and archives are not indexed. Scanned PDFs without a text layer are not read (no OCR).
- The first indexing of a large Desktop can take about an hour in the background (~14 chunks/s on M2 Pro). Search works right away.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
