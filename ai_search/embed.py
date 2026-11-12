"""Local embedding model (bge-m3), loaded lazily and unloaded after idle time."""
import gc
import os
import threading
import time

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

_model = None
_lock = threading.RLock()
_last_used = 0.0
MAX_TOKENS = 512


def _device():
    import torch
    return "mps" if torch.backends.mps.is_available() else "cpu"


def get_model(name: str):
    global _model, _last_used
    with _lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            dev = _device()
            m = SentenceTransformer(name, device=dev)
            m.max_seq_length = MAX_TOKENS
            if dev == "mps":
                m = m.half()
            _model = m
        _last_used = time.time()
        return _model


def encode(texts: list[str], name: str, batch_size: int = 16):
    with _lock:
        m = get_model(name)
        vecs = m.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                        convert_to_numpy=True, show_progress_bar=False)
        global _last_used
        _last_used = time.time()
        return vecs.astype("float32")


def unload_if_idle(idle_sec: float) -> bool:
    global _model
    with _lock:
        if _model is not None and time.time() - _last_used > idle_sec:
            _model = None
            gc.collect()
            try:
                import torch
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
            except Exception:
                pass
            return True
    return False


def is_loaded() -> bool:
    return _model is not None
