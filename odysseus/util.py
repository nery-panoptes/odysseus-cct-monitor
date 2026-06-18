from datetime import datetime
from pathlib import Path
import hashlib
import re
import unicodedata


def now():
    return datetime.now().isoformat(timespec="seconds")


def root(base, val):
    path = Path(val)
    if path.is_absolute():
        return path
    return Path(base) / path


def normcnpj(val):
    return re.sub(r"\D", "", str(val or ""))


def flatkey(val):
    txt = unicodedata.normalize("NFD", str(val or ""))
    txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "", txt.lower())


def cleanname(val, lim=150):
    txt = unicodedata.normalize("NFKC", str(val or "")).strip()
    txt = re.sub(r'[\\/:*?"<>|]+', " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return (txt[:lim].rstrip() or "SEM NOME")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
