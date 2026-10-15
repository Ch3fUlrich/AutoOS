import sys
import os
import json
import re
import time
import secrets
import threading
from pathlib import Path

# ensure runner_pool_core can be imported when this file is run directly
try:
    from runner_pool_core import strip_env, write_json_atomic
except ImportError:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(__file__))
    from runner_pool_core import strip_env, write_json_atomic

class GateError(Exception):
    """Error that leads to a gate rejection."""
    exit_code = 2

    def __init__(self, message: str):
        super().__init__(message)

# ---------- helpers ----------
def parse_original_command(cmd: str) -> str:
    """Validate SSH_ORIGINAL_COMMAND and return the base command."""
    if not isinstance(cmd, str):
        raise GateError('command not a string')
    # only exact "submit" or "status --json" allowed
    if re.fullmatch(r'(submit|status --json)\Z', cmd):
        return cmd.split()[0]  # "submit" or "status"
    raise GateError('invalid original command')

def valid_id(s: str) -> bool:
    """Strict identifier validation."""
    if not isinstance(s, str):
        return False
    return bool(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', s))

def read_capped(stream, cap: int, timeout: float = 30.0) -> bytes:
    """Read up to cap bytes; raise if more data is present or the channel stalls.

    stream.read(cap + 1) blocks until cap + 1 bytes or EOF, so a client that
    sends a little and holds the channel open would pin the gate: read in a
    daemon thread and give up after `timeout` seconds.
    """
    box = []
    def _read():
        try:
            box.append(stream.read(cap + 1))
        except Exception as exc:  # surfaced below
            box.append(exc)
    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise GateError('read timeout')
    data = box[0]
    if isinstance(data, Exception):
        raise GateError('read failed')
    if len(data) > cap:
        raise GateError('input exceeds size limit')
    return data

def _check_str(value: str):
    if not isinstance(value, str):
        raise GateError('value not a string')
    if value.startswith('-') or '\0' in value:
        raise GateError('invalid string value')

_RESERVED_NT = {'CON', 'PRN', 'AUX', 'NUL', 'CLOCK$'} | \
    {'COM%d' % i for i in range(1, 10)} | {'LPT%d' % i for i in range(1, 10)}

def _check_path(value):
    """A card path is a plain relative path inside the pool: no .., no anchor, no NT tricks."""
    _check_str(value)
    if value == '' or value.startswith(('/', '\\', '~')) or re.match(r'^[A-Za-z]:', value):
        raise GateError('invalid path')
    for part in re.split(r'[\\/]', value):
        if part in ('', '..'):
            raise GateError('invalid path')
        if part != '.' and part.endswith((' ', '.')):
            raise GateError('invalid path')
        if part.split('.')[0].upper() in _RESERVED_NT:
            raise GateError('invalid path')

def validate_card(card):
    """Validate the job card."""
    if not isinstance(card, dict):
        raise GateError('card must be a dict')
    allowed = {'client', 'model', 'kind', 'task_type', 'title', 'paths', 'spec'}
    unknown = set(card) - allowed
    if unknown:
        raise GateError('unknown card fields')
    for k, v in card.items():
        if k == 'paths':
            if not isinstance(v, list):
                raise GateError('paths must be a list')
            for item in v:
                _check_path(item)
        elif isinstance(v, list):
            for item in v:
                _check_str(item)
        else:
            _check_str(v)
    # size limit
    if len(json.dumps(card).encode()) > 8192:
        raise GateError('card too large')
    return card

def parse_submit(raw: bytes, *, submitter: str, now=None, rand=None):
    """Parse a submit request JSON."""
    try:
        obj = json.loads(raw.decode())
    except Exception as e:
        raise GateError('invalid json') from e
    if not isinstance(obj, dict):
        raise GateError('submit payload not an object')
    if set(obj) != {'card', 'brief'}:
        raise GateError('extra or missing keys')
    brief = obj['brief']
    if not isinstance(brief, str):
        raise GateError('brief not a string')
    # brief validation per rules
    stripped = brief.strip()
    if stripped == '':
        raise GateError('invalid brief')
    if '\n' not in brief:
        line = stripped
        if (line.startswith('/') or line.startswith('~') or line.startswith('.') or
                line.startswith('\\') or re.match(r'^[A-Za-z]:[\\/]', line)):
            raise GateError('invalid brief')
    # submitter validation
    _check_str(submitter)
    if not valid_id(submitter):
        raise GateError('invalid submitter')
    if submitter.startswith('-'):
        raise GateError('invalid submitter')
    # validate card
    card = validate_card(obj['card'])
    # generate id
    now = now if now is not None else int(time.time())
    rand = rand if rand is not None else secrets.token_hex(4)
    job_id = f'j{now}-{rand}'
    if not valid_id(job_id):
        raise GateError('generated invalid id')
    result = {
        'id': job_id,
        'submitter': submitter,
        'brief': brief,
        'card': card,
        'submitted_at': now,
    }
    return result

def authorized_keys_line(lane: str, pubkey: str) -> str:
    if not valid_id(lane):
        raise ValueError('invalid lane')
    if not re.fullmatch(r'^[A-Za-z0-9+/=]+$', pubkey):
        raise ValueError('invalid pubkey')
    cmd = (
        f'C:\\\\Python\\\\python.exe C:\\\\autoos\\\\tools\\\\runner_pool.py '
        f'ssh-gate --submitter {lane}'
    )
    return f'restrict,command="{cmd}" ssh-ed25519 {pubkey} {lane}@coding'

def gate_env(env: dict) -> dict:
    """Return a copy of env with AUTOOS_ADMISSION_OFF stripped."""
    return strip_env(env)

def run_gate(argv, env, stdin_buf, stdout_text, spool_dir):
    try:
        # parse submitter
        if '--submitter' not in argv:
            raise GateError('missing submitter')
        idx = argv.index('--submitter')
        try:
            submitter = argv[idx + 1]
        except IndexError:
            raise GateError('submitter argument missing')
        # original command
        orig = env.get('SSH_ORIGINAL_COMMAND')
        if not orig:
            raise GateError('no original command')
        base = parse_original_command(orig)

        # status handling
        if base == 'status':
            status_path = Path(spool_dir) / 'status.json'
            if status_path.is_file():
                data = json.loads(status_path.read_text())
            else:
                data = {"slots": [], "queue_depth": 0, "oldest_wait_s": 0}
            json.dump(data, stdout_text)
            return 0

        # submit handling
        if base != 'submit':
            raise GateError('unsupported command')
        raw = read_capped(stdin_buf, cap=65536)
        job = parse_submit(
            raw,
            submitter=submitter,
            now=int(time.time()),
            rand=secrets.token_hex(4),
        )
        queue_dir = Path(spool_dir) / 'queue'
        queue_dir.mkdir(parents=True, exist_ok=True)
        path = queue_dir / f"{job['id']}.json"
        write_json_atomic(path, job)
        stdout_text.write(job['id'])
        return 0
    except GateError as e:
        stdout_text.write(f'rejected: {e}')
        return e.exit_code

def main():
    spool = (Path(os.environ.get('LOCALAPPDATA') or
                  os.path.expanduser('~/.local/state')) /
             'autoos' / 'pool')
    sys.exit(run_gate(sys.argv[1:], os.environ, sys.stdin.buffer,
                      sys.stdout, spool))

if __name__ == '__main__':
    main()
