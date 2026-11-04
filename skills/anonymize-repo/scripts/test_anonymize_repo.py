"""Run: uv run --with pytest pytest skills/anonymize-repo/scripts"""

import os
import subprocess
import sys
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("anonymize_repo.py")
GIT = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Jane Doe",
    "GIT_AUTHOR_EMAIL": "jdoe@uni.example",
    "GIT_COMMITTER_NAME": "Jane Doe",
    "GIT_COMMITTER_EMAIL": "jdoe@uni.example",
}

CONFIG = r"""
exclude = ["notes/**", "CLAUDE.md"]

[[replace]]
files = "README.md"
pattern = '(?ms)^## Authors\n.*?(?=^## )'
with = ""

[[replace]]
files = "paper/*.tex"
pattern = '\\author\{[^}]*\}'
with = '\\author{Anonymous}'

[identity]
patterns = ['Jane Doe', '\bjdoe\b', 'State University']
allow = [{ files = "data/**", pattern = 'Jane Doe Creek' }]
"""


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "notes").mkdir(parents=True)
    (repo / "paper").mkdir()
    (repo / "data").mkdir()
    (repo / "README.md").write_text(
        "# Project\n\n## Authors\n\n- Jane Doe, State University\n\n## Usage\n\nRun it.\n"
    )
    (repo / "CLAUDE.md").write_text("Ask Jane Doe (jdoe) before merging.\n")
    (repo / "notes" / "todo.md").write_text("jdoe: fix the figure\n")
    (repo / "paper" / "main.tex").write_text("\\author{Jane Doe}\n\\title{T}\n")
    (repo / "data" / "places.csv").write_text("site\nJane Doe Creek\n")
    with zipfile.ZipFile(repo / "data" / "table.xlsx", "w") as z:
        z.writestr(
            "docProps/core.xml",
            "<cp:coreProperties><dc:creator>Jane Doe</dc:creator>"
            "<cp:lastModifiedBy>jdoe</cp:lastModifiedBy></cp:coreProperties>",
        )
        z.writestr("xl/workbook.xml", "<workbook/>")
    pdf = b"%PDF-1.4\n1 0 obj << /Author (Jane Doe) >> endobj\n2 0 obj << /T (Jane Doe) >> endobj\n%%EOF\n"
    (repo / "paper" / "notes.pdf").write_bytes(pdf)  # an annotation author and /Author
    (repo / "anonymize.toml").write_text(CONFIG)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=GIT, capture_output=True)
    (repo / "untracked.txt").write_text("Jane Doe\n")  # untracked files are never exported
    return repo


def run(*args):
    return subprocess.run(
        [sys.executable, SCRIPT, *map(str, args)], capture_output=True, text=True, check=False
    )


def test_export_removes_identity_and_history(tmp_path):
    repo, out = make_repo(tmp_path), tmp_path / "out"
    r = run("export", repo, out, "--init")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (out / "notes").exists() and not (out / "CLAUDE.md").exists()
    assert not (out / "anonymize.toml").exists() and not (out / "untracked.txt").exists()
    assert (out / "README.md").read_text() == "# Project\n\n## Usage\n\nRun it.\n"
    assert (out / "paper" / "main.tex").read_text().startswith("\\author{Anonymous}")
    core = zipfile.ZipFile(out / "data" / "table.xlsx").read("docProps/core.xml").decode()
    assert "Jane" not in core and "jdoe" not in core
    pdf = (out / "paper" / "notes.pdf").read_bytes()
    assert b"Jane" not in pdf and len(pdf) == len((repo / "paper" / "notes.pdf").read_bytes())
    log = subprocess.run(
        ["git", "log", "--format=%an <%ae>"], cwd=out, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    assert log[0] == "Anonymous <anonymous@example.invalid>" and len(log) == 2  # one commit


def test_scan_reports_what_is_left(tmp_path):
    repo = make_repo(tmp_path)
    r = run("scan", repo, "--config", repo / "anonymize.toml")
    assert r.returncode == 1
    for where in (
        "README.md:5",
        "CLAUDE.md:1",
        "notes/todo.md:1",
        "paper/main.tex:1",
        "data/table.xlsx:1 [docProps/core.xml]",
    ):
        assert where in r.stdout, where
    assert "places.csv" not in r.stdout  # allowed false positive


def test_export_fails_when_a_rule_no_longer_matches(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "README.md").write_text("# Project\n\nJane Doe wrote this.\n")
    subprocess.run(["git", "commit", "-qam", "edit"], cwd=repo, check=True, env=GIT)
    r = run("export", repo, tmp_path / "out")
    assert r.returncode == 1
    assert "replace rule matched nothing: README.md" in r.stdout
    assert "README.md:3" in r.stdout


def git_in(cwd: Path, *args) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    return r.stdout.strip()


def mirror_log(bare: Path) -> list[str]:
    """Author and parents of each commit on main, newest first."""
    return git_in(bare, "log", "--format=%an <%ae> %P", "main").splitlines()


def test_push_creates_then_updates_mirror(tmp_path):
    repo, bare = make_repo(tmp_path), tmp_path / "anon.git"
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    r = run("export", repo, tmp_path / "out1", "--push", bare)
    assert r.returncode == 0, r.stdout + r.stderr
    assert mirror_log(bare) == ["Anonymous <anonymous@example.invalid>"]  # a root commit

    (repo / "paper" / "main.tex").write_text("\\author{Jane Doe}\n\\title{T2}\n")
    subprocess.run(["git", "commit", "-qam", "edit"], cwd=repo, check=True, env=GIT)
    r = run("export", repo, tmp_path / "out2", "--push", bare)
    assert r.returncode == 0, r.stdout + r.stderr
    first = git_in(bare, "rev-parse", "main~1")
    assert mirror_log(bare) == [
        f"Anonymous <anonymous@example.invalid> {first}",
        "Anonymous <anonymous@example.invalid>",
    ]
    assert git_in(bare, "rev-parse", "main^{tree}") == git_in(
        tmp_path / "out2", "rev-parse", "HEAD^{tree}"
    )

    r = run("export", repo, tmp_path / "out3", "--push", bare)
    assert r.returncode == 0 and "nothing to push" in r.stdout, r.stdout + r.stderr
    assert len(mirror_log(bare)) == 2


def test_push_refuses_on_scan_hit(tmp_path):
    repo, bare = make_repo(tmp_path), tmp_path / "anon.git"
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    (repo / "paper" / "main.tex").write_text("\\author{Jane Doe}\n% thanks jdoe\n")
    subprocess.run(["git", "commit", "-qam", "edit"], cwd=repo, check=True, env=GIT)
    r = run("export", repo, tmp_path / "out", "--push", bare)
    assert r.returncode == 1 and "paper/main.tex:2" in r.stdout
    assert mirror_log(bare) == []


def test_push_refuses_a_remote_with_named_commits(tmp_path):
    repo, bare = make_repo(tmp_path), tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    subprocess.run(["git", "push", "-q", bare, "HEAD:main"], cwd=repo, check=True, env=GIT)
    r = run("export", repo, tmp_path / "out", "--push", bare)
    assert r.returncode == 1 and "Jane Doe <jdoe@uni.example>" in r.stderr
    assert mirror_log(bare) == ["Jane Doe <jdoe@uni.example>"]
