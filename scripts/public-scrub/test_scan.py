from pathlib import Path
import importlib.util
import subprocess
import sys

SCAN = Path(__file__).parent / "scan.py"
REPO_PATTERNS = Path(__file__).parent / "patterns.txt"
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


# ─── the tracked generic shapes (patterns.txt) ───────────────────────────────
# Everything above feeds the scanner a pattern of its own invention. These test
# the shipped file: a shape that stops matching fails here instead of quietly
# un-guarding the public repo (SPEC-OMNI A2). Fixture values are invented and
# live in a temp dir, never in a tracked document.

def _scan_module():
    spec = importlib.util.spec_from_file_location("public_scrub_scan", SCAN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rule_names(text, rules):
    return {name for name, rx in rules if rx.search(text)}


# Each kind of site value the gate must catch, with an obviously fake specimen.
SHAPES = {
    "cgnat": ["bind = 100.64.5.6", "peer 100.127.255.255 down"],
    "link-local": ["169.254.1.1", "route via 169.254.0.1"],
    "ipv6-ula": ["fd00::1", "fd12:3456:7890::1", "fdab::42"],
    "email": ["ops@widget-factory.dev", "a.b+c@mail.acme-corp.io"],
    "private-host": [
        "http://nas.lan:8080/",
        "printer.home.arpa",
        "srv01.internal",
        "http://hub.local/api",
    ],
}

# Documentation and loopback values: the shapes every public document uses
# legitimately. None of them may hit any rule at all.
CLEAN = [
    # RFC 5737 documentation ranges and loopback.
    "192.0.2.1", "198.51.100.7", "203.0.113.42", "127.0.0.1", "0.0.0.0",
    "http://localhost:8080/ui",
    # IPv6 documentation and loopback.
    "2001:db8::1", "::1", "fe80::1",
    # Adjacent-but-public addresses (just outside CGNAT and link-local).
    "100.63.9.9", "100.128.0.1", "169.253.1.1", "169.255.0.1",
    # RFC 2606 reserved and published no-reply addresses.
    "user@example.com", "you@sub.example.org", "nobody@example.net",
    "autoos-worker@users.noreply.github.com", "noreply@anthropic.com",
    "root@localhost", "not-an-email",
    # Paths and hostnames that look like the shapes but are not site values:
    # a dotfile directory, a settings file, a dotenv filename, a glob, Docker's
    # host alias, and the scrubbed `<name>.example.internal` placeholder this
    # repo's infra/ docs already use.
    "~/.local/bin/tool", "settings.local.json", "*.local/share/app",
    "are gitignored (`.env.local`, `.env.client`)",
    "Runs on `coding.example.internal` from the single-source",
    "http://host.docker.internal:8080", "/home/you/project",
    "C:\\Users\\you\\project",
]


def test_every_documented_shape_is_matched_by_its_own_rule():
    rules = _scan_module().load_patterns(REPO_PATTERNS)
    for name, specimens in SHAPES.items():
        for text in specimens:
            assert name in _rule_names(text, rules), "%r is not caught by %s" % (
                text, name)


def test_clean_values_hit_no_rule_at_all():
    rules = _scan_module().load_patterns(REPO_PATTERNS)
    for text in CLEAN:
        assert not _rule_names(text, rules), "%r trips %s" % (
            text, ", ".join(sorted(_rule_names(text, rules))))


def test_planted_tree_is_caught_end_to_end(tmp_path):
    # The path CI takes: real patterns file, real subprocess, a planted file.
    (tmp_path / "leak.md").write_text(
        "\n".join(specimens for s in SHAPES.values() for specimens in s) + "\n",
        encoding="utf-8")
    proc = run_scan(["--patterns", str(REPO_PATTERNS), "leak.md"], cwd=tmp_path)
    assert proc.returncode == 1
    names = {ln.rsplit(": ", 1)[1] for ln in proc.stdout.splitlines()}
    assert names >= set(SHAPES), names
    for line in proc.stdout.splitlines():
        assert "100.64" not in line and "@" not in line


def test_clean_document_exits_0(tmp_path):
    (tmp_path / "ok.md").write_text("\n".join(CLEAN) + "\n", encoding="utf-8")
    proc = run_scan(["--patterns", str(REPO_PATTERNS), "ok.md"], cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout
