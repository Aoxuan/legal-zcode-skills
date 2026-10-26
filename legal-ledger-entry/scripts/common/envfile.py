# -*- coding: utf-8 -*-
"""改技能根目录 .env 里的指定键。不打印值。"""
import os


def upsert_env(path, updates):
    lines = []
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    keys = set(updates)
    seen = set()
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in keys:
            out.append("%s=%s" % (key, updates[key]))
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append("%s=%s" % (key, value))
    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    for key, value in updates.items():
        os.environ[key] = value
