import os
import json
import re
from pathlib import Path

import runner_pool_core as rpc

# --------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------
SLOT_STATES = ('idle', 'running', 'breaker-open')
HEARTBEAT_T = 120

# --------------------------------------------------------------------
# Directory handling
# --------------------------------------------------------------------
def ensure_dirs(root: Path) -> None:
    """
    Ensure the required pool directories exist under ``root``.
    Idempotent – calling multiple times does not raise.
    """
    required = ('queue', 'slots', 'done', 'failed', 'log', 'submitters')
    for name in required:
        (root / name).mkdir(parents=True, exist_ok=True)

def spool_root(env: dict) -> Path:
    """
    Determine the spool root based on environment values.
    Prefer ``LOCALAPPDATA`` on Windows, otherwise fallback to ``HOME``.
    """
    if 'LOCALAPPDATA' in env:
        return Path(env['LOCALAPPDATA']) / 'autoos' / 'pool'
    home = env.get('HOME', '')
    return Path(home) / '.local' / 'state' / 'autoos' / 'pool'

# --------------------------------------------------------------------
# Slot state persistence
# --------------------------------------------------------------------
def write_slot_state(
    root: Path,
    n,
    state,
    job,
    now,
    pid,
    ft,
    *,
    since=None,
) -> bool:
    """
    Write a slot's JSON state atomically.
    Raises ``ValueError`` for invalid slot numbers or states.
    Returns ``True`` on success.
    """
    # validate slot number
    if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > 6:
        raise ValueError("slot number must be an integer from 1 to 6")
    # validate state
    if state not in SLOT_STATES:
        raise ValueError(f"invalid state: {state}")

    slot_dir = root / 'slots' / str(n)
    slot_dir.mkdir(parents=True, exist_ok=True)
    slot_path = slot_dir / 'slot.json'

    payload = {
        'slot': n,
        'state': state,
        'job': job,
        'since': since if since is not None else now,
        'heartbeat': now,
        'pid': pid,
        'filetime': ft,
    }

    rpc.write_json_atomic(slot_path, payload)
    return True

# --------------------------------------------------------------------
# Status building
# --------------------------------------------------------------------
def build_status(root: Path, now, pid_alive, filetime_of, *, T=HEARTBEAT_T):
    """
    Compile a status dictionary describing slots and queue.
    """
    slots_dir = root / 'slots'
    slots = []

    if slots_dir.is_dir():
        entries = []
        for p in slots_dir.iterdir():
            if p.is_dir() and p.name.isdigit():
                entries.append((int(p.name), p))
        for n, entry in sorted(entries, key=lambda x: x[0]):
            slot_file = entry / 'slot.json'
            try:
                data = json.loads(slot_file.read_text())
                required = {'slot', 'state', 'job', 'since', 'heartbeat', 'pid', 'filetime'}
                if not required.issubset(data):
                    raise ValueError
                pid = data['pid']
                hb = data['heartbeat']
                state = data['state']
                age = now - hb
                ft_match = filetime_of(pid) == data['filetime']
                alive = pid_alive(pid)
                if rpc.dead_slot(alive, ft_match, age, T):
                    state = 'slot-dead'
                slot_rec = {
                    'slot': n,
                    'state': state,
                    'job': data['job'],
                    'since': data['since'],
                    'heartbeat': hb,
                }
            except Exception:
                slot_rec = {
                    'slot': n,
                    'state': 'slot-dead',
                    'job': None,
                    'since': None,
                    'heartbeat': None,
                }
            slots.append(slot_rec)

    queue_dir = root / 'queue'
    queue_depth = 0
    oldest_wait = 0
    if queue_dir.is_dir():
        files = [p for p in queue_dir.iterdir() if p.is_file() and p.name.endswith('.json')]
        queue_depth = len(files)
        if files:
            oldest_mtime = min(p.stat().st_mtime for p in files)
            oldest_wait = int(now - oldest_mtime)
            if oldest_wait < 0:
                oldest_wait = 0

    return {
        'slots': slots,
        'queue_depth': queue_depth,
        'oldest_wait_s': oldest_wait,
    }

# --------------------------------------------------------------------
# Job argument construction
# --------------------------------------------------------------------
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

def job_argv(job: dict, repo_root: Path, python: str):
    """
    Build the command line array used to invoke a job.
    """
    # Validate required fields
    job_id = job.get('id')
    if not isinstance(job_id, str) or not _ID_RE.fullmatch(job_id):
        raise ValueError('invalid job id')
    card = job.get('card', {})
    client = card.get('client')
    if not isinstance(client, str) or not client:
        raise ValueError('missing client')
    # Optional fields
    model = card.get('model')
    title = card.get('title')
    if any(v is not None and not isinstance(v, str) for v in (model, title)):
        raise ValueError('model and title must be strings')
    if not title:
        title = job_id
    brief = job.get('brief', '')
    if not isinstance(brief, str):
        raise ValueError('brief must be a string')

    argv = [
        python,
        str(repo_root / 'tools' / 'autoos-agent.py'),
        'run',
        '--client', client,
    ]
    if model:
        argv += ['--model', model]
    argv += ['--isolate', '--run-id', job_id]
    argv += ['--title', title]
    argv += ['--', brief]

    # Total command line length check
    total = sum(len(a.encode('utf-16-le')) // 2 for a in argv) + len(argv) - 1
    if total > 30000:
        raise ValueError('argv-too-long')

    return argv

# --------------------------------------------------------------------
# Done record creation
# --------------------------------------------------------------------
def done_record(job: dict, rc: int, started: int, ended: int, deep: dict):
    """
    Produce a sanitized record for a finished job.
    """
    rec = {
        'id': job.get('id'),
        'submitter': job.get('submitter'),
        'rc': rc,
        'started': started,
        'ended': ended,
        'deep': deep,
        'state': 'done' if rc == 0 else 'failed',
    }
    return rec

# slot loop + CLI appended below
import subprocess
import sys
import time

def default_risk_of(job):
    """Fail-safe risk class for a pool job: R0 only when the job cannot have a diff.

    The `api` client is tool-less (prompt file in, review.txt out), so there is no
    worktree to classify. Every other or unreadable job is R2 and gets deep
    sampling -- an unclassified job is never reported as R0.
    """
    try:
        if job['card']['client'] == 'api':
            return 'R0'
    except (KeyError, TypeError):
        pass
    return 'R2'

class Deps:
    def __init__(self, *, spawn, admission_refusal=lambda: None, mem_mb=lambda: 99999,
                 floor_mb=6144, breaker_open=lambda: False, risk_of=default_risk_of,
                 diff_info=lambda job: (False, False), sample_every=4,
                 now=time.time, pid=os.getpid(), filetime=0,
                 repo_root=Path(__file__).resolve().parent.parent, python=sys.executable,
                 sleep=time.sleep,
                 environ=None):
        self.spawn = spawn
        self.admission_refusal = admission_refusal
        self.mem_mb = mem_mb
        self.floor_mb = floor_mb
        self.breaker_open = breaker_open
        self.risk_of = risk_of
        self.diff_info = diff_info
        self.sample_every = sample_every
        self.now = now
        self.pid = pid
        self.filetime = filetime
        self.repo_root = repo_root
        self.python = python
        self.sleep = sleep
        self.environ = dict(os.environ) if environ is None else environ

def _oldest_queue_file(queue_dir):
    files = [p for p in queue_dir.glob('*.json')]
    if not files:
        return None
    return min(files, key=lambda p: (p.stat().st_mtime, p.name))

def _merge_decision(deep, decision):
    if isinstance(decision, dict):
        for k, v in decision.items():
            if k == 'reasons' and isinstance(v, list):
                deep.setdefault('reasons', []).extend(v)
            else:
                deep[k] = v
    elif isinstance(decision, list):
        deep.setdefault('reasons', []).extend(decision)
    elif decision is not None:
        deep.setdefault('reasons', []).append(decision)

def run_one(root, n, deps):
    root = Path(root)
    ensure_dirs(root)
    queue_dir = root / 'queue'
    slot_dir = root / 'slots' / str(n)
    slot_dir.mkdir(parents=True, exist_ok=True)
    done_dir = root / 'done'
    failed_dir = root / 'failed'
    log_dir = root / 'log'
    now = deps.now()

    if deps.breaker_open():
        write_slot_state(root, n, 'breaker-open', None, now, deps.pid, deps.filetime)
        return 'breaker-open'

    mem = deps.mem_mb()
    if mem is None or mem < deps.floor_mb:
        write_slot_state(root, n, 'idle', None, now, deps.pid, deps.filetime)
        return 'memory'

    refusal = deps.admission_refusal()
    if refusal:
        write_slot_state(root, n, 'idle', None, now, deps.pid, deps.filetime)
        return 'refused'

    claimed_files = [p for p in slot_dir.glob('*.json') if p.name != 'slot.json']
    if claimed_files:
        job_path = claimed_files[0]
        res = rpc.requeue_dead(str(job_path), str(queue_dir), str(failed_dir))
        if job_path.exists():
            try:
                job_path.unlink()
            except OSError:
                pass
        write_slot_state(root, n, 'idle', None, now, deps.pid, deps.filetime)
        if res == 'requeued':
            return 'recovered-requeued'
        return 'recovered-failed'

    qfile = _oldest_queue_file(queue_dir)
    if qfile is None:
        write_slot_state(root, n, 'idle', None, now, deps.pid, deps.filetime)
        return 'idle'

    claimed_path = rpc.claim(str(queue_dir), str(slot_dir))
    if claimed_path is None:
        write_slot_state(root, n, 'idle', None, now, deps.pid, deps.filetime)
        return 'claim-failed'
    claimed_path = Path(claimed_path)
    try:
        return _run_claimed(root, n, deps, claimed_path, now)
    except Exception:
        # Quarantine before recording: a broken job must never poison a restart.
        failed_path = failed_dir / claimed_path.name
        if claimed_path.exists():
            os.replace(claimed_path, failed_path)
        rec = done_record({'id': claimed_path.stem}, 2, now, time.time(),
                          {'reasons': ['exception']})
        rpc.write_json_atomic(done_dir / claimed_path.name, rec)
        write_slot_state(root, n, 'idle', None, now, deps.pid, deps.filetime)
        return 'failed'

def _run_claimed(root, n, deps, claimed_path, now):
    queue_dir, done_dir, failed_dir, log_dir = (root / name for name in
                                               ('queue', 'done', 'failed', 'log'))
    try:
        job = json.loads(claimed_path.read_text(encoding='utf-8'))
        if (not isinstance(job, dict) or not isinstance(job.get('id'), str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]+', job['id'])
                or job['id'] != claimed_path.stem):
            raise ValueError('invalid claimed job id')
    except (ValueError, OSError):
        os.replace(str(claimed_path), str(failed_dir / claimed_path.name))
        write_slot_state(root, n, 'idle', None, now, deps.pid, deps.filetime)
        return 'bad-job'

    mem2 = deps.mem_mb()
    if mem2 is None or mem2 < deps.floor_mb:
        qpath = queue_dir / claimed_path.name
        rpc.write_json_atomic(str(qpath), job)
        if claimed_path.exists():
            try:
                claimed_path.unlink()
            except OSError:
                pass
        write_slot_state(root, n, 'idle', None, now, deps.pid, deps.filetime)
        return 'memory-recheck'

    argv = job_argv(job, deps.repo_root, deps.python)

    log_path = log_dir / f"{job['id']}.log"
    env = rpc.strip_env(deps.environ)
    write_slot_state(root, n, 'running', job, now, deps.pid, deps.filetime)
    started = deps.now()
    rc = deps.spawn(argv, str(log_path), env)
    ended = deps.now()

    if rc == 13:
        qpath = queue_dir / f"{job['id']}.json"
        rpc.write_json_atomic(str(qpath), job)
        if claimed_path.exists():
            try:
                claimed_path.unlink()
            except OSError:
                pass
        write_slot_state(root, n, 'idle', None, now, deps.pid, deps.filetime)
        return 'requeued-rc13'

    risk = deps.risk_of(job)
    diff_empty, tests_skipped = deps.diff_info(job)
    decision = rpc.sample_decision(str(root), job.get('submitter'), rc, risk,
                                   diff_empty, tests_skipped, deps.sample_every)
    deep = {}
    _merge_decision(deep, decision)
    if rc != 0:
        deep.setdefault('reasons', []).append('rc')
    if risk != 'R0':
        deep.setdefault('reasons', []).append('risk')
    rec = done_record(job, rc, started, ended, deep)
    rpc.write_json_atomic(str(done_dir / f"{job['id']}.json"), rec)
    if claimed_path.exists():
        try:
            claimed_path.unlink()
        except OSError:
            pass
    qpath = queue_dir / f"{job['id']}.json"
    if qpath.exists():
        try:
            qpath.unlink()
        except OSError:
            pass
    write_slot_state(root, n, 'idle', None, now, deps.pid, deps.filetime)
    return 'done' if rc == 0 else 'failed'

_USAGE = "usage: runner_pool.py {status|ssh-gate|kill|slot} ...\n"

def popen_spawn(argv, log_path, env, slot_dir=None):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, 'wb') as logf:
        p = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT, env=env)
        if slot_dir is not None:
            _write_run_private(slot_dir, p.pid, log_path.stem)
        return p.wait()

def _write_run_private(slot_dir, pid, run_id):
    import runner_pool_proc as proc
    Path(slot_dir).mkdir(parents=True, exist_ok=True)
    proc.write_run_private(slot_dir, run_id, pid, proc.filetime_of(pid) or 0, 'tooluse')

def _parse_slot_args(args):
    n = 1
    once = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--n' and i + 1 < len(args):
            try:
                n = int(args[i + 1])
            except ValueError:
                return None, None
            i += 2
        elif a == '--once':
            once = True
            i += 1
        else:
            i += 1
    if n < 1:
        return None, None
    return n, once

def _cmd_status(args, env, stdout):
    import runner_pool_proc as proc
    root = spool_root(env)
    data = build_status(root, time.time(), proc.pid_alive, proc.filetime_of)
    stdout.write(json.dumps(data, sort_keys=True) + '\n')
    return 0

def _cmd_ssh_gate(args, env, stdin, stdout, deps, gate):
    if gate is None:
        return 2
    return gate.run_gate(args, env, getattr(stdin, 'buffer', stdin), stdout,
                         spool_root(env))

def _find_slot(root, jobid):
    slots = root / 'slots'
    if not slots.exists():
        return None
    for d in slots.iterdir():
        if not d.is_dir():
            continue
        sf = d / 'slot.json'
        if not sf.exists():
            continue
        try:
            data = json.loads(sf.read_text())
        except Exception:
            continue
        job = data.get('job')
        if (job.get('id') if isinstance(job, dict) else job) == jobid:
            return d
    return None

def _cmd_kill(args, env, stdout, deps, proc):
    if len(args) != 1:
        return 2
    jobid = args[0]
    if not jobid or not re.match(r'^[A-Za-z0-9_.-]+$', jobid):
        return 2
    root = spool_root(env)
    slot = _find_slot(root, jobid)
    if slot is None:
        stdout.write('no such job\n')
        return 1
    filetime_of = deps.get('filetime_of') if deps else None
    if filetime_of is None:
        filetime_of = proc.filetime_of
    kill_tree = deps.get('kill_tree') if deps else None
    if kill_tree is None:
        kill_tree = getattr(proc, 'kill_tree', lambda argv: None)
    if deps is not None:
        os_name = deps.get('os_name', 'nt')
    else:
        os_name = getattr(rpc, 'os_name', lambda: os.name)()
    try:
        out = proc.kill_job(slot, filetime_of, kill_tree, os_name=os_name)
    except (ValueError, AttributeError, KeyError, TypeError, OSError):
        stdout.write('refused: bad run.private\n')
        return 1
    stdout.write(str(out) + '\n')
    return 0

def _build_real_deps(root, n, env):
    def spawn(argv, log, e):
        return popen_spawn(argv, log, e, slot_dir=root / 'slots' / str(n))
    def admission_refusal():
        try:
            import importlib.util
            base = Path(__file__).resolve().parent
            path = base / 'autoos-agent.py'
            spec = importlib.util.spec_from_file_location('autoos_agent_mod', path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.host_admission_refusal()
        except Exception:
            return 'admission-unreadable'
    def breaker_open():
        return (root / 'slots' / str(n) / '.tripped').exists()
    return Deps(
        spawn=spawn,
        mem_mb=lambda: rpc.mem_available_mb(),
        admission_refusal=admission_refusal,
        breaker_open=breaker_open,
        environ=rpc.strip_env(env),
    )

def _cmd_slot(args, env, stdin, stdout, deps):
    n, once = _parse_slot_args(args)
    if n is None:
        return 2
    raw = env.get('AUTOOS_POOL_N', '6')
    try:
        requested = int(raw)
    except ValueError:
        return 2
    if requested < 1:
        return 2
    used = min(requested, 6)
    if requested > 6:
        stdout.write('clamp requested=%d used=%d\n' % (requested, used))
    if n > used:
        return 3
    root = spool_root(env)
    try:
        ensure_dirs(root)
    except Exception:
        pass
    slot_deps = deps.get('slot_deps') if deps else None
    if slot_deps is None:
        slot_deps = _build_real_deps(root, n, env)
    if once:
        res = run_one(root, n, slot_deps)
        stdout.write(str(res) + '\n')
        return 0
    max_iterations = deps.get('max_iterations') if deps else None
    sleep = deps.get('sleep') if deps else None
    if sleep is None:
        sleep = time.sleep
    i = 0
    while True:
        run_one(root, n, slot_deps)
        i += 1
        if max_iterations is not None and i >= max_iterations:
            break
        sleep(15)
    return 0

def main(argv, env=None, stdin=None, stdout=None, deps=None):
    if env is None:
        env = dict(os.environ)
    if stdin is None:
        stdin = sys.stdin.buffer
    if stdout is None:
        stdout = sys.stdout
    if not argv:
        stdout.write(_USAGE)
        return 2
    cmd = argv[0]
    rest = argv[1:]
    if cmd == 'status':
        return _cmd_status(rest, env, stdout)
    if cmd == 'ssh-gate':
        try:
            import runner_pool_gate as gate
        except Exception:
            gate = None
        return _cmd_ssh_gate(rest, env, stdin, stdout, deps, gate)
    if cmd == 'kill':
        import runner_pool_proc as proc
        return _cmd_kill(rest, env, stdout, deps, proc)
    if cmd == 'slot':
        return _cmd_slot(rest, env, stdin, stdout, deps)
    stdout.write(_USAGE)
    return 2

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:], dict(os.environ), sys.stdin.buffer, sys.stdout))
