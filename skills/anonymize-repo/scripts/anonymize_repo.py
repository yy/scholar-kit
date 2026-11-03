#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Export an anonymized snapshot of a research repository for double-blind review.

anonymize_repo.py export REPO OUT [--ref REF] [--config FILE] [--init]
    Write the tracked files at REF (default HEAD) to OUT, with no git history; drop the
    excluded paths, apply the text replacements, blank author fields in Office metadata and PDF
    /Author entries, mask identity terms left in PDF strings (annotation authors), then scan OUT. --init makes OUT a fresh git repository with one commit by
    "Anonymous". Exit 1 if the scan finds anything.
anonymize_repo.py scan DIR [--config FILE]
    Report every match of the identity patterns in DIR: text files, Office document parts,
    and PDF text (with pdftotext, if installed). Exit 1 on any match.

The config (default REPO/anonymize.toml; never exported) is TOML:

    exclude = ["CLAUDE.md", "notes/**"]          # paths left out (globs, from the root)

    [[replace]]                                  # applied to exported text files
    files = "README.md"                          # glob
    pattern = '(?ms)^## Authors\\n.*?(?=^## )'   # Python regex
    with = ""                                    # replacement; each rule must match

    [identity]
    patterns = ['Jane Doe', '\\bjdoe\\b']        # regexes the scan must not find
    allow = [{ files = "data/raw/**", pattern = 'Doe Run' }]   # known false positives
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

OFFICE = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"}
# Office metadata fields that name people or organizations
OFFICE_FIELDS = re.compile(rb"(<(dc:creator|cp:lastModifiedBy|Company|Manager)>)[^<]*(</\2>)")
PDF_FIELDS = re.compile(rb"/(Author)\s*\((?:\\.|[^\\)])*\)")


@dataclass
class Config:
    exclude: list[str] = field(default_factory=list)
    replace: list[dict] = field(default_factory=list)
    patterns: list[re.Pattern] = field(default_factory=list)
    allow: list[tuple[str, re.Pattern]] = field(default_factory=list)


def load_config(path: Path) -> Config:
    raw = tomllib.loads(path.read_text())
    ident = raw.get("identity", {})
    return Config(
        exclude=raw.get("exclude", []),
        replace=raw.get("replace", []),
        patterns=[re.compile(p) for p in ident.get("patterns", [])],
        allow=[(a["files"], re.compile(a["pattern"])) for a in ident.get("allow", [])],
    )


def matches(rel: str, glob: str) -> bool:
    """Glob match on a posix path; "dir/**" covers everything under dir."""
    if glob.endswith("/**"):
        return rel == glob[:-3] or rel.startswith(glob[:-2])
    return fnmatch.fnmatch(rel, glob)


def files_under(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)


def export_tree(repo: Path, out: Path, ref: str) -> None:
    if out.exists() and any(out.iterdir()):
        sys.exit(f"anonymize_repo: {out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    tar = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", ref],
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(out, filter="data")


def drop_excluded(out: Path, cfg: Config, config_name: str) -> list[str]:
    dropped = []
    for p in files_under(out):
        rel = p.relative_to(out).as_posix()
        if rel == config_name or any(matches(rel, g) for g in cfg.exclude):
            p.unlink()
            dropped.append(rel)
    for d in sorted((d for d in out.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts)):
        if not any(d.iterdir()):
            d.rmdir()
    return dropped


def read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


def apply_replacements(out: Path, cfg: Config) -> list[str]:
    errors = []
    for rule in cfg.replace:
        pat, hits = re.compile(rule["pattern"]), 0
        for p in files_under(out):
            rel = p.relative_to(out).as_posix()
            text = read_text(p) if matches(rel, rule["files"]) else None
            if text is None:
                continue
            new, n = pat.subn(rule.get("with", ""), text)
            if n:
                with p.open("w", encoding="utf-8", newline="") as fh:
                    fh.write(new)
                hits += n
        if not hits:  # a rule that no longer matches usually means the source moved
            errors.append(f"replace rule matched nothing: {rule['files']}: {rule['pattern']}")
    return errors


def scrub_metadata(out: Path, cfg: Config) -> list[str]:
    scrubbed = []
    for p in files_under(out):
        if p.suffix.lower() in OFFICE:
            with zipfile.ZipFile(p) as z:
                items = [(i, z.read(i.filename)) for i in z.infolist()]
            changed = False
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for info, data in items:
                    if info.filename in ("docProps/core.xml", "docProps/app.xml"):
                        new = OFFICE_FIELDS.sub(rb"\1\3", data)
                        changed |= new != data
                        data = new
                    z.writestr(info, data)
            if changed:
                p.write_bytes(buf.getvalue())
                scrubbed.append(p.relative_to(out).as_posix())
        elif p.suffix.lower() == ".pdf":
            data = p.read_bytes()
            # same length, so the cross-reference offsets stay valid: /Author, and identity
            # terms in uncompressed strings such as annotation authors (/T (Name))
            new = PDF_FIELDS.sub(
                lambda m: b"/" + m[1] + b" (" + b" " * (len(m[0]) - len(m[1]) - 4) + b")", data
            )
            for pat in cfg.patterns:
                new = re.sub(pat.pattern.encode(), lambda m: b"X" * len(m[0]), new)
            if new != data:
                p.write_bytes(new)
                scrubbed.append(p.relative_to(out).as_posix())
    return scrubbed


def texts_of(p: Path) -> list[tuple[str, str]]:
    """(part, text) pairs to scan in one file."""
    suffix = p.suffix.lower()
    if suffix in OFFICE:
        with zipfile.ZipFile(p) as z:
            return [
                (n, z.read(n).decode("utf-8", "replace"))
                for n in z.namelist()
                if n.endswith((".xml", ".rels"))
            ]
    if suffix == ".pdf":
        parts = [("metadata", p.read_bytes().decode("latin-1"))]
        if shutil.which("pdftotext"):
            txt = subprocess.run(
                ["pdftotext", "-q", str(p), "-"], capture_output=True, text=True, check=False
            ).stdout
            parts.append(("text", txt))
        return parts
    text = read_text(p)
    if text is not None:
        return [("", text)]
    return [("bytes", p.read_bytes().decode("latin-1"))]  # .xls and other binaries


def scan(root: Path, cfg: Config) -> list[str]:
    hits = []
    for p in files_under(root):
        rel = p.relative_to(root).as_posix()
        allowed = [a for g, a in cfg.allow if matches(rel, g)]
        for part, text in texts_of(p):
            for pat in cfg.patterns:
                for m in pat.finditer(text):
                    line = text[max(0, m.start() - 60) : m.end() + 60]
                    if any(a.search(line) for a in allowed):
                        continue
                    lineno = text.count("\n", 0, m.start()) + 1
                    where = f"{rel}:{lineno}" + (f" [{part}]" if part else "")
                    hits.append(f"{where}: {m[0]!r} in ...{' '.join(line.split())[:100]}...")
    return hits


def init_repo(out: Path) -> None:
    env = {
        "GIT_AUTHOR_NAME": "Anonymous",
        "GIT_AUTHOR_EMAIL": "anonymous@example.invalid",
        "GIT_COMMITTER_NAME": "Anonymous",
        "GIT_COMMITTER_EMAIL": "anonymous@example.invalid",
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["commit", "-q", "-m", "Anonymized snapshot"],
    ):
        subprocess.run(["git", "-C", str(out), *args], check=True, env=env)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("repo", type=Path)
    e.add_argument("out", type=Path)
    e.add_argument("--ref", default="HEAD")
    e.add_argument("--config", type=Path)
    e.add_argument("--init", action="store_true")
    s = sub.add_parser("scan")
    s.add_argument("root", type=Path)
    s.add_argument("--config", type=Path, required=True)
    a = ap.parse_args()

    if a.cmd == "scan":
        hits = scan(a.root, load_config(a.config))
        print("\n".join(hits) or "scan clean")
        sys.exit(1 if hits else 0)

    config = a.config or a.repo / "anonymize.toml"
    cfg = load_config(config)
    export_tree(a.repo, a.out, a.ref)
    dropped = drop_excluded(a.out, cfg, config.name)
    errors = apply_replacements(a.out, cfg)
    scrubbed = scrub_metadata(a.out, cfg)
    hits = scan(a.out, cfg)
    print(
        f"exported {a.ref} to {a.out}: {len(files_under(a.out))} files, {len(dropped)} excluded, "
        f"{len(scrubbed)} with metadata blanked"
    )
    for line in errors + hits:
        print(line)
    if errors or hits:
        sys.exit(1)
    if a.init:
        init_repo(a.out)
        print(f"initialized {a.out} as a git repository with one anonymous commit")
    print("scan clean")


if __name__ == "__main__":
    main()
