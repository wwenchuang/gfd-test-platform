"""Atomic file storage helpers with JSON caching.

Migrated from midscene-upload.py — path-safety, filename sanitisation,
file I/O, and an in-process TTL cache for JSON reads.
"""

import json
import os
import re
import threading
import time
from pathlib import Path

_ID_COUNTER = 0
_ID_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Path safety & filename helpers (from midscene-upload.py)
# ---------------------------------------------------------------------------

def safe_join(root, *parts):
    """Join *root* with *parts*, raising ValueError on path traversal."""
    root_abs = os.path.abspath(root)
    path = os.path.abspath(os.path.join(root_abs, *parts))
    if path != root_abs and not path.startswith(root_abs + os.sep):
        raise ValueError("非法路径")
    return path


def unique_millis_id(prefix=""):
    """Return a unique id string: ``{prefix}_{millis}_{counter:05d}``."""
    global _ID_COUNTER
    with _ID_LOCK:
        _ID_COUNTER = (_ID_COUNTER + 1) % 100000
        counter = _ID_COUNTER
    return f"{prefix}_{int(time.time() * 1000)}_{counter:05d}"


def clean_filename(name, default="task.yaml"):
    """Sanitise a name and ensure it ends with ``.yaml``."""
    name = str(name or "").strip()
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r'[\\:*?"<>|]+', "_", name).strip()
    base = re.sub(r"\.(yaml|yml)$", "", name, flags=re.I).strip(" ._\t\r\n")
    if not base:
        name = default
    elif name.startswith("."):
        name = base
    if not name.endswith((".yaml", ".yml")):
        name += ".yaml"
    return name


def is_visible_yaml_filename(name):
    """Return True if *name* is a non-hidden YAML file."""
    name = str(name or "").strip()
    if not name or name.startswith(".") or name.startswith("._"):
        return False
    if not name.endswith((".yaml", ".yml")):
        return False
    base = re.sub(r"\.(yaml|yml)$", "", name, flags=re.I).strip(" ._\t\r\n")
    return bool(base)


def clean_asset_filename(name, default="asset.txt"):
    """Sanitise an asset filename (no directory separators or wild chars)."""
    name = (name or default).strip()
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r'[\\:*?"<>|]+', "_", name)
    return name or default


def clean_id(value, default="page"):
    """Sanitise an identifier string."""
    value = (value or default).strip()
    value = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value)
    return value.strip("._")[:80] or default


# ---------------------------------------------------------------------------
# JSON TTL cache
# ---------------------------------------------------------------------------

_JSON_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def read_json_cached(path, ttl_seconds=3, default=None):
    """Read a JSON file with an in-process TTL cache."""
    key = str(path)
    now = time.time()
    with _CACHE_LOCK:
        entry = _JSON_CACHE.get(key)
        if entry and (now - entry[0]) < ttl_seconds:
            return entry[1]
    # cache miss
    data = read_json_file(path, default=default)
    with _CACHE_LOCK:
        _JSON_CACHE[key] = (now, data)
    return data


def invalidate_json_cache(path=None):
    """Invalidate cached JSON entries.  *path*=None clears all."""
    with _CACHE_LOCK:
        if path is None:
            _JSON_CACHE.clear()
        else:
            _JSON_CACHE.pop(str(path), None)


# ---------------------------------------------------------------------------
# File I/O — atomic writes, safe reads
# ---------------------------------------------------------------------------

def read_json_file(path, default=None):
    """Read and parse a JSON file; return *default* on failure."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        bad = f"{path}.bad.{int(time.time())}"
        try:
            with open(path, "rb") as src, open(bad, "wb") as dst:
                dst.write(src.read())
        except Exception:
            bad = ""
        print(f"read_json_file failed: {path}: {e}" + (f"; backup={bad}" if bad else ""))
        return default


def write_json_file(path, data):
    """Atomically write *data* as JSON and invalidate cache."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    bad = target.with_suffix(target.suffix + ".bad")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        if tmp.exists():
            try:
                os.replace(tmp, bad)
            except Exception:
                pass
        raise
    invalidate_json_cache(str(target))


# Backward-compatible alias (used by tests and legacy code)
write_json_atomic = write_json_file


def read_text_file(path, default=""):
    """Read a text file; return *default* on failure."""
    try:
        p = Path(path)
        if not p.exists():
            return default
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return default


def write_text_file(path, text):
    """Atomically write *text* to a file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text or "")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def write_bytes_file(path, data):
    """Atomically write binary *data* to a file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        with open(tmp, "wb") as f:
            f.write(data or b"")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def runtime_path_status(path):
    """Return a dict describing whether *path* exists, is a dir, and is writable."""
    exists = os.path.exists(path)
    check_path = path if exists else os.path.dirname(path) or "."
    return {
        "path": path,
        "exists": exists,
        "is_dir": os.path.isdir(path),
        "writable": os.access(check_path, os.W_OK),
    }
