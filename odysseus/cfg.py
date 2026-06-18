from pathlib import Path
import os

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def loadcfg(file="config.toml"):
    path = Path(file)
    with path.open("rb") as src:
        cfg = tomllib.load(src)
    cfg["base"] = str(path.parent.resolve())
    return cfg


def sec(cfg, name):
    return cfg.get(name, {})


def env(name, default=""):
    return os.environ.get(name, default)
