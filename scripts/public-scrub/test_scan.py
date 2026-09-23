from pathlib import Path
import subprocess
import sys

SCAN = Path(__file__).parent / "scan.py"
PATTERN_LINE = "host\tsecret\\.example\\.lan\n"


def write_patterns(path: Path, extra_prefix: str = "") -> Path:
    path.write_text(extra_prefix + PATTERN_LINE, encoding="utf-8")
    return path


def run_scan(args, cwd):
    return subprocess.run(
        [sys.executable, str(SCAN), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_hit_reports_and_exit_1(tmp_path):
    pat = write_patterns(tmp_path / "pats.txt")
    doc = tmp_path / "doc.md"
    doc.write_text("ok\nsee secret.example.lan\n", encoding="utf-8")
    proc = run_scan(["--patterns", str(pat), "doc.md"], cwd=tmp_path)
    assert proc.returncode == 1
    assert "doc.md:2: host" in proc.stdout


def test_clean_file_exits_0(tmp_path):
    pat = write_patterns(tmp_path / "pats.txt")
    doc = tmp_path / "clean.md"
    doc.write_text("nothing to see here\n", encoding="utf-8")
    proc = run_scan(["--patterns", str(pat), "clean.md"], cwd=tmp_path)
    assert proc.returncode == 0


def test_never_prints_matched_text(tmp_path):
    pat = write_patterns(tmp_path / "pats.txt")
    doc = tmp_path / "doc.md"
    line = "see secret.example.lan"
    doc.write_text("ok\n" + line + "\n", encoding="utf-8")
    proc = run_scan(["--patterns", str(pat), "doc.md"], cwd=tmp_path)
    assert proc.returncode == 1
    assert "secret.example.lan" not in proc.stdout
    assert "secret.example.lan" not in proc.stderr
    assert line not in proc.stdout
    assert line not in proc.stderr


def test_exclude_file_prefixes(tmp_path):
    pat = write_patterns(tmp_path / "pats.txt")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.md").write_text("hit secret.example.lan\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("hit secret.example.lan\n", encoding="utf-8")
    excl = tmp_path / "excl.txt"
    excl.write_text("sub\n", encoding="utf-8")
    proc = run_scan(
        ["--patterns", str(pat), "--exclude-file", str(excl)], cwd=tmp_path
    )
    assert proc.returncode == 1
    assert "b.md" in proc.stdout
    assert "a.md" not in proc.stdout

    excl.write_text("su\n", encoding="utf-8")
    proc2 = run_scan(
        ["--patterns", str(pat), "--exclude-file", str(excl)], cwd=tmp_path
    )
    assert proc2.returncode == 1
    assert "a.md" in proc2.stdout
    assert "b.md" in proc2.stdout


def git(*args, cwd):
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def test_git_tree_scans_committed_content(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", cwd=repo)
    pat = repo / "pats.txt"
    pat.write_text(PATTERN_LINE, encoding="utf-8")
    tracked = repo / "tracked.md"
    tracked.write_text("hello secret.example.lan\n", encoding="utf-8")
    git("add", "tracked.md", cwd=repo)
    git(
        "-c",
        "user.email=t@t.t",
        "-c",
        "user.name=t",
        "commit",
        "-m",
        "init",
        cwd=repo,
    )
    # working copy is now clean; committed blob still has the hit
    tracked.write_text("clean now\n", encoding="utf-8")
    proc = run_scan(
        ["--patterns", str(pat), "--git-tree", "HEAD"], cwd=repo
    )
    assert proc.returncode == 1
    assert "tracked.md" in proc.stdout
    assert "secret.example.lan" not in proc.stdout
    assert "secret.example.lan" not in proc.stderr

    excl = repo / "excl.txt"
    excl.write_text("tracked.md\n", encoding="utf-8")
    proc2 = run_scan(
        [
            "--patterns",
            str(pat),
            "--git-tree",
            "HEAD",
            "--exclude-file",
            str(excl),
        ],
        cwd=repo,
    )
    assert proc2.returncode == 0
    assert "tracked.md" not in proc2.stdout


def test_patterns_comments_and_blanks_ignored(tmp_path):
    pat = tmp_path / "pats.txt"
    pat.write_text(
        "# a comment\n\n   \n" + PATTERN_LINE + "# trailing\n\n",
        encoding="utf-8",
    )
    doc = tmp_path / "doc.md"
    doc.write_text("see secret.example.lan\n", encoding="utf-8")
    proc = run_scan(["--patterns", str(pat), "doc.md"], cwd=tmp_path)
    assert proc.returncode == 1
    assert "doc.md:1: host" in proc.stdout


def test_git_tree_non_ascii_name_is_scanned(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "patterns.txt").write_text("host\tsecret\.example\.lan\n", encoding="utf-8")
    (repo / "sécret.md").write_text("see secret.example.lan\n", encoding="utf-8")
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run(git + ["init", "-q"], cwd=repo, check=True)
    subprocess.run(git + ["add", "."], cwd=repo, check=True)
    subprocess.run(git + ["commit", "-q", "-m", "x"], cwd=repo, check=True)
    r = subprocess.run([sys.executable, str(SCAN), "--patterns", "patterns.txt",
                        "--git-tree", "HEAD"], cwd=repo, capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 1
    assert "sécret.md:1: host" in r.stdout
    # the tracked --patterns file itself is skipped
    assert "patterns.txt" not in r.stdout


def test_unknown_rev_exits_2(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "p.txt").write_text("host\tx\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCAN), "--patterns", "p.txt",
                        "--git-tree", "no-such-rev"], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 2


def test_missing_path_is_not_clean(tmp_path):
    (tmp_path / "p.txt").write_text("host\tx\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCAN), "--patterns", str(tmp_path / "p.txt"),
                        str(tmp_path / "nope.md")], capture_output=True, text=True)
    assert r.returncode == 2
    assert "unreadable" in r.stderr
