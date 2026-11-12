import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent          # the ai-search folder
DATA_DIR = Path(os.environ.get("AI_SEARCH_HOME", "~/.ai-search")).expanduser()
DB_PATH = DATA_DIR / "index.db"
LOCK_PATH = DATA_DIR / "index.lock"
LOG_DIR = DATA_DIR / "logs"
EMBED_DIM = 1024  # bge-m3


@dataclass
class Config:
    file_roots: list[Path] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    chats: dict[str, list[str]] = field(default_factory=dict)
    model: str = "BAAI/bge-m3"
    max_text_mb: float = 1
    max_doc_mb: float = 30
    debounce_files_sec: float = 5
    debounce_chats_sec: float = 20
    model_idle_unload_min: float = 15
    precheck_max_changes: int = 50
    port: int = 47400
    limit: int = 10
    snippet_chars: int = 600


def load() -> Config:
    raw = tomllib.loads((APP_DIR / "config.toml").read_text(encoding="utf-8"))
    idx, srv, srch = raw.get("index", {}), raw.get("server", {}), raw.get("search", {})
    c = Config(
        file_roots=[Path(p).expanduser() for p in raw.get("file_roots", [])],
        exclude=list(raw.get("exclude", [])),
        chats={k: [os.path.expanduser(p) for p in v] for k, v in raw.get("chats", {}).items()},
    )
    for k, v in {**idx, **srv, **srch}.items():
        if hasattr(c, k):
            setattr(c, k, type(getattr(c, k))(v))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return c
