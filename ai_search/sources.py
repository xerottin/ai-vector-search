"""Finding sources (chats, project files) and turning them into text chunks."""
import fnmatch
import glob
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import APP_DIR, DATA_DIR, Config

CHUNK_CHARS = 1500
OVERLAP_CHARS = 200
FILE_WINDOW_LINES = 60
FILE_OVERLAP_LINES = 8

SKIP_DIRS = {
    "node_modules", ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", "dist", "build",
    ".next", ".nuxt", ".output", "target", ".idea", ".vscode", ".cache", "coverage", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "site-packages", "Pods", "DerivedData", ".terraform", "vendor",
    ".gradle", ".turbo", ".parcel-cache", "bower_components", "htmlcov", ".tox", "migrations_backup",
    "static", "assets", "public", "out", "logs", "tmp", "temp",
    "tests", "test", "__tests__", "fixtures", "locale", "locales", "i18n", "versions",
}
# Generated folders (renders, exports) are skipped by name pattern too.
SKIP_DIR_PATTERNS = ["*-output*", "*_output*", "*output copy*"]
# Formats that are usually generated data or bundles: index only small files.
SMALL_ONLY_EXT = {".json", ".csv", ".tsv", ".html", ".htm", ".js", ".mjs", ".cjs", ".css", ".xml", ".ipynb"}
SMALL_ONLY_KB = 100
MAX_CHUNKS_PER_FILE = 300
TEXT_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".md", ".mdx", ".txt", ".rst",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".sql", ".sh", ".zsh", ".bash", ".go",
    ".rs", ".java", ".kt", ".kts", ".swift", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".php", ".rb",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".xml", ".graphql", ".proto", ".tf", ".lua",
    ".dart", ".scala", ".r", ".ipynb", ".csv", ".tsv", ".env.example", ".dockerfile", ".gql",
}
TEXT_NAMES = {"Dockerfile", "Makefile", "Procfile", "Justfile", "README", "LICENSE", "Caddyfile"}
DOC_EXT = {".pdf", ".docx"}
SKIP_NAME_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "*.jks", "id_rsa*", "id_ed25519*",
    "*secret*", "*credential*", "*password*", "*.kdbx", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "uv.lock", "Cargo.lock", "composer.lock", "Gemfile.lock", "*.min.js", "*.min.css",
    "*.map", "*.lock",
]

# Obvious secrets are masked before anything is stored.
SECRET_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
    r"|\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{20,}"
    r"|\bsk-ant-[A-Za-z0-9_\-]{20,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{30,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bxox[abprs]-[A-Za-z0-9\-]{10,}"
    r"|\bAIza[0-9A-Za-z_\-]{35}"
    r"|\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}"
    r"|(?i:(?:private[_-]?key|preshared[_-]?key|password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)"
    r"\s*[:=]\s*)[\"']?[^\s\"']{6,}",
    re.S,
)
NOISE_RE = re.compile(
    r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+|environment_context|user_instructions)>.*?</\1>",
    re.S,
)
TAG_RE = re.compile(r"</?pasted_content[^>]*>")


@dataclass
class Chunk:
    kind: str            # "chat" | "file"
    agent: str           # claude / codex / gemini / antigravity / "" for files
    path: str
    title: str
    project: str
    ts: str
    loc: str
    text: str


def redact(text: str) -> str:
    return SECRET_RE.sub("[СКРЫТО]", text)


def clean(text: str) -> str:
    return TAG_RE.sub("", NOISE_RE.sub("", text or "")).strip()


def split_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):  # prefer to cut at a paragraph or sentence boundary
            cut = max(text.rfind("\n", start + size // 2, end), text.rfind(". ", start + size // 2, end))
            if cut > start:
                end = cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [t for t in out if t]


# ---------------------------------------------------------------- discovery

def chat_source_kind(path: str, cfg: Config) -> str | None:
    if "/subagents/" in path:
        return None
    for agent, patterns in cfg.chats.items():
        if any(fnmatch.fnmatch(path, p) for p in patterns):
            return "chat:" + agent
    return None


def file_is_indexable(path: Path, cfg: Config) -> bool:
    name = path.name
    if any(fnmatch.fnmatch(name.lower(), p) for p in SKIP_NAME_PATTERNS) and not name.endswith(".env.example"):
        return False
    ext = path.suffix.lower()
    if ext in DOC_EXT:
        limit = cfg.max_doc_mb
    elif ext in SMALL_ONLY_EXT:
        limit = SMALL_ONLY_KB / 1024
    elif ext in TEXT_EXT or name in TEXT_NAMES:
        limit = cfg.max_text_mb
    else:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return 0 < size <= limit * 1024 * 1024


def skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".") or any(fnmatch.fnmatch(name, p) for p in SKIP_DIR_PATTERNS)


def in_skipped_dir(path: Path, root: Path, cfg: Config | None = None) -> bool:
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        return True
    return any(skip_dir(p) for p in parts) or (cfg is not None and excluded(path, root, cfg))


def excluded(path: Path, root: Path, cfg: Config) -> bool:
    """User exclusions from config.toml, matched against the path relative to the root folder."""
    rel = str(path.relative_to(root))
    return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat.rstrip("/") + "/*") for pat in cfg.exclude)


def file_source_kind(path: str, cfg: Config) -> str | None:
    p = Path(path)
    if APP_DIR in p.parents or DATA_DIR in p.parents:
        return None
    for root in cfg.file_roots:
        if root in p.parents and not in_skipped_dir(p, root, cfg) and file_is_indexable(p, cfg):
            return "file"
    return None


def classify(path: str, cfg: Config) -> str | None:
    return chat_source_kind(path, cfg) or file_source_kind(path, cfg)


def discover(cfg: Config) -> tuple[dict[str, str], list[str]]:
    """Return ({path: kind}, [permission errors])."""
    found, errors = {}, []
    for agent, patterns in cfg.chats.items():
        for pattern in patterns:
            for path in glob.glob(pattern, recursive=True):
                if os.path.isfile(path) and "/subagents/" not in path:
                    found[path] = "chat:" + agent
    for root in cfg.file_roots:
        if not root.exists():
            continue

        def onerror(e, root=root):
            if isinstance(e, PermissionError):
                errors.append(str(e.filename or root))

        for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
            d = Path(dirpath)
            if d == APP_DIR or APP_DIR in d.parents:
                dirnames[:] = []
                continue
            dirnames[:] = [n for n in dirnames if not skip_dir(n) and not excluded(d / n, root, cfg)]
            for n in filenames:
                p = d / n
                if file_is_indexable(p, cfg):
                    found[str(p)] = "file"
    return found, errors


# ---------------------------------------------------------------- parsing

def _texts(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for x in obj:
            yield from _texts(x)
    elif isinstance(obj, dict):
        if obj.get("type") in ("tool_use", "tool_result", "image", "thinking", "redacted_thinking",
                               "function_call", "function_call_output", "reasoning"):
            return
        for k in ("text", "content", "parts"):
            if k in obj:
                yield from _texts(obj[k])


def _turns_to_chunks(turns, agent, path, title, project) -> list[Chunk]:
    chunks = []
    for n, (ts, user, answer) in enumerate(turns, 1):
        body = ""
        if user:
            body += "Пользователь: " + user
        if answer:
            body += ("\n\n" if body else "") + "Агент: " + answer
        for part in split_text(redact(body)):
            chunks.append(Chunk("chat", agent, path, title, project, ts, f"реплика {n}", part))
    return chunks


def _group_turns(messages):
    """messages: iterable of (role, text, ts) -> list of (ts, user_text, assistant_text)."""
    turns = []
    for role, text, ts in messages:
        if not text:
            continue
        if role == "user" or not turns:
            turns.append([ts, text if role == "user" else "", "" if role == "user" else text])
        else:
            turns[-1][2] += ("\n" if turns[-1][2] else "") + text
    return turns


def parse_claude(path: str) -> list[Chunk]:
    title, cwd, msgs, first_user = "", "", [], ""
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("type")
            if t in ("custom-title", "ai-title"):
                title = d.get("customTitle") or d.get("aiTitle") or title
            elif t == "summary" and not title:
                title = d.get("summary", "")
            if t not in ("user", "assistant") or d.get("isMeta"):
                continue
            cwd = cwd or d.get("cwd", "")
            text = clean("\n".join(_texts((d.get("message") or {}).get("content"))))
            if t == "user" and text and not first_user:
                first_user = text
            msgs.append((t, text, d.get("timestamp", "")))
    title = title or first_user[:80].replace("\n", " ")
    return _turns_to_chunks(_group_turns(msgs), "claude", path, title, cwd)


def parse_codex(path: str) -> list[Chunk]:
    cwd, msgs = "", []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            p = d.get("payload") or {}
            if d.get("type") == "session_meta":
                cwd = p.get("cwd", cwd)
            elif d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") in ("user", "assistant"):
                text = clean("\n".join(_texts(p.get("content"))))
                if text.startswith("# AGENTS.md instructions"):
                    continue
                msgs.append((p["role"], text, d.get("timestamp", "")))
    turns = _group_turns(msgs)
    title = next((u for _, u, _ in turns if u), "")[:80].replace("\n", " ")
    return _turns_to_chunks(turns, "codex", path, title, cwd)


def parse_gemini(path: str) -> list[Chunk]:
    try:
        data = json.load(open(path, encoding="utf-8", errors="ignore"))
    except ValueError:
        return []
    items = data.get("messages", []) if isinstance(data, dict) else data
    msgs = []
    for m in items if isinstance(items, list) else []:
        if not isinstance(m, dict):
            continue
        role = m.get("type") or m.get("role") or ""
        role = "user" if role == "user" else "assistant" if role in ("gemini", "model", "assistant") else ""
        if role:
            msgs.append((role, clean("\n".join(_texts(m.get("content") or m.get("parts")))), m.get("timestamp", "")))
    turns = _group_turns(msgs)
    title = next((u for _, u, _ in turns if u), "")[:80].replace("\n", " ")
    return _turns_to_chunks(turns, "gemini", path, title, "")


def parse_markdown_note(path: str, agent: str) -> list[Chunk]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    heading = next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")), "")
    title = f"{heading or Path(path).stem} ({Path(path).name})"
    ts = _mtime_iso(path)
    return [Chunk("chat", agent, path, title, "", ts, f"часть {i}", part)
            for i, part in enumerate(split_text(redact(text)), 1)]


def _mtime_iso(path: str) -> str:
    import datetime
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")
    except OSError:
        return ""


def _project_of(path: Path, cfg: Config) -> str:
    for root in cfg.file_roots:
        if root in path.parents:
            rel = path.relative_to(root).parts
            return str(root / rel[0]) if len(rel) > 1 else str(root)
    return ""


def parse_file(path: str, cfg: Config) -> list[Chunk]:
    p = Path(path)
    rel = str(p)
    for root in cfg.file_roots:
        if root in p.parents:
            rel = str(p.relative_to(root))
    project, ts, ext = _project_of(p, cfg), _mtime_iso(path), p.suffix.lower()
    header = f"Файл: {rel}\n"
    out = []

    def add(loc, text):
        out.append(Chunk("file", "", path, p.name, project, ts, loc, header + redact(text)))

    if ext == ".pdf":
        import logging
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        from pypdf import PdfReader
        try:
            reader = PdfReader(path)
            for n, page in enumerate(reader.pages, 1):
                for part in split_text(page.extract_text() or ""):
                    add(f"стр. {n}", part)
        except Exception:
            return []
        return out
    if ext == ".docx":
        import docx
        try:
            text = "\n".join(par.text for par in docx.Document(path).paragraphs)
        except Exception:
            return []
        for n, part in enumerate(split_text(text), 1):
            add(f"часть {n}", part)
        return out

    with open(path, "rb") as f:
        raw = f.read()
    if b"\x00" in raw[:4096]:
        return []
    lines = raw.decode("utf-8", errors="ignore").splitlines()
    i = 0
    while i < len(lines):
        j, size = i, 0
        while j < len(lines) and j - i < FILE_WINDOW_LINES and size < CHUNK_CHARS:
            size += len(lines[j]) + 1
            j += 1
        text = "\n".join(lines[i:j]).strip()
        if text:
            for part in split_text(text):
                add(f"строки {i + 1}–{j}", part)
        if j >= len(lines):
            break
        i = max(j - FILE_OVERLAP_LINES, i + 1)
    return out


def parse(path: str, kind: str, cfg: Config) -> list[Chunk]:
    if kind == "file":
        return parse_file(path, cfg)[:MAX_CHUNKS_PER_FILE]
    agent = kind.split(":", 1)[1]
    if path.endswith(".md"):
        return parse_markdown_note(path, agent)
    if agent == "claude":
        return parse_claude(path)
    if agent == "codex":
        return parse_codex(path)
    if agent == "gemini":
        return parse_gemini(path)
    return []
