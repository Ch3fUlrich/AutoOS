"""API client helpers for runner pool."""
import hashlib
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from autoos_tokens import DEFAULT_MAX_TOKENS, clamp_max_tokens

API_MODELS = ('deepseek-v4.1-flash', 'ovh/gpt-oss-120b', 'qoder/glm-5.2')
MAX_TOKENS_CAP = DEFAULT_MAX_TOKENS
MODEL_ID_RE = re.compile(r'[A-Za-z0-9._:/-]{1,100}')
DEFAULT_MODEL = 'deepseek-v4.1-flash'
TITLE_RE = re.compile(r'[A-Za-z0-9_-]{1,64}')
NOW_HEX_RE = re.compile(r'[0-9a-fA-F]+')

def _valid_model(model):
    return isinstance(model, str) and model in API_MODELS and MODEL_ID_RE.fullmatch(model) is not None

def pool_root(env):
    if env is None:
        env = os.environ
    local = env.get('LOCALAPPDATA')
    if local:
        return os.path.join(local, 'autoos', 'pool')
    home = env.get('HOME') or env.get('USERPROFILE') or os.path.expanduser('~')
    return os.path.join(home, '.local', 'state', 'autoos', 'pool')

def confine(path, root):
    if not isinstance(path, str) or not isinstance(root, str):
        raise ValueError('path and root must be strings')
    if not path or not root:
        raise ValueError('empty path or root')
    if '\x00' in path or '\x00' in root:
        raise ValueError('NUL in path or root')
    root_abs = os.path.abspath(root)
    path_abs = os.path.abspath(path)
    try:
        common = os.path.commonpath([root_abs, path_abs])
    except ValueError:
        raise ValueError('path outside root')
    if common != root_abs:
        raise ValueError('path outside root')
    rel = os.path.relpath(path_abs, root_abs)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        raise ValueError('path outside root')
    cur = path_abs
    while True:
        try:
            info = os.lstat(cur)
            if (stat.S_ISLNK(info.st_mode) or
                    getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError('symlink or junction component')
        except FileNotFoundError:
            pass
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    real_root = os.path.realpath(root_abs)
    real_path = os.path.realpath(path_abs)
    try:
        common_real = os.path.commonpath([real_root, real_path])
    except ValueError:
        raise ValueError('path outside root')
    if common_real != real_root:
        raise ValueError('path outside root')
    return real_path

def build_argv(model, prompt, out_dir, root, title='api', max_tokens=None, python='python', script=None):
    if not _valid_model(model):
        raise ValueError('bad model')
    if max_tokens is None:
        max_tokens = clamp_max_tokens(model, DEFAULT_MAX_TOKENS)
    else:
        if type(max_tokens) is not int or not (1 <= max_tokens <= MAX_TOKENS_CAP):
            raise ValueError('bad max_tokens')
        max_tokens = clamp_max_tokens(model, max_tokens)
    if not isinstance(title, str) or TITLE_RE.fullmatch(title) is None:
        raise ValueError('bad title')
    if not isinstance(python, str) or not python:
        raise ValueError('bad python')
    if script is None:
        script = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'review-call.py')
    elif not isinstance(script, str) or not script:
        raise ValueError('bad script')
    prompt_real = confine(prompt, root)
    out_real = confine(out_dir, root)
    return [
        python, script,
        '--model', model,
        '--prompt-file', prompt_real,
        '--out-dir', out_real,
        '--title', title,
        '--max-tokens', str(max_tokens),
    ]

def build_command_api(task, model, env, now_hex=None):
    if not isinstance(task, str):
        raise ValueError('task must be a string')
    if model is None:
        model = DEFAULT_MODEL
    if not _valid_model(model):
        raise ValueError('bad model')
    if now_hex is not None:
        if not isinstance(now_hex, str) or NOW_HEX_RE.fullmatch(now_hex) is None:
            raise ValueError('bad now_hex')
    ident = 'a' + hashlib.sha256(task.encode('utf-8')).hexdigest()[:12]
    if now_hex is not None:
        ident += '-' + now_hex
    if TITLE_RE.fullmatch(ident) is None:
        raise ValueError('bad ident')
    root = pool_root(env)
    prompt_dir = os.path.join(root, 'api-prompts')
    prompt = os.path.join(prompt_dir, ident + '.md')
    out_dir = os.path.join(root, 'api-out', ident)
    confine(prompt, root)
    confine(out_dir, root)
    os.makedirs(prompt_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    flags = os.O_CREAT | os.O_WRONLY
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    flags |= getattr(os, 'O_BINARY', 0)
    fd = os.open(prompt, flags, 0o600)
    try:
        # O_NOFOLLOW protects only the leaf; a parent may have changed at open.
        confine(prompt, root)
        os.ftruncate(fd, 0)
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
        data = task.encode('utf-8')
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)
    return build_argv(model, prompt, out_dir, root, title=ident)
