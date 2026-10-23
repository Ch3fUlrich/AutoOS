import os
import re
import json
import ctypes
from subprocess import list2cmdline

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runner_pool_core as _core

# constants
JOB_MEMORY_MB = 8192
JOB_FLAG_KILL_ON_CLOSE = 0x2000
JOB_FLAG_JOB_MEMORY = 0x200
JOB_FLAG_BREAKAWAY_OK = 0x800
JOB_FLAG_SILENT_BREAKAWAY = 0x1000

def job_limit_flags() -> int:
    """Return the limit flags for a Windows job object."""
    return JOB_FLAG_KILL_ON_CLOSE | JOB_FLAG_JOB_MEMORY

def job_memory_bytes() -> int:
    """Return default memory limit in bytes."""
    return JOB_MEMORY_MB * 1024 * 1024

# ----------------------------------------------------------------------
# run.private handling
# ----------------------------------------------------------------------
_RUN_PRIVATE_KEYS = {'run_id', 'pid', 'filetime', 'mode'}
_RUN_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_MODE_SET = {'toolless', 'tooluse'}

def _validate_params(run_id, pid, filetime, mode):
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError('invalid run_id')
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError('invalid pid')
    if not isinstance(filetime, int) or isinstance(filetime, bool) or filetime < 0:
        raise ValueError('invalid filetime')
    if mode not in _MODE_SET:
        raise ValueError('invalid mode')

def write_run_private(slot_dir, run_id, pid, filetime, mode, write=None) -> bool:
    """Validate and atomically write run.private."""
    _validate_params(run_id, pid, filetime, mode)
    data = {
        'run_id': run_id,
        'pid': pid,
        'filetime': filetime,
        'mode': mode,
    }
    path = os.path.join(slot_dir, 'run.private')
    return (write or _core.write_json_atomic)(path, data)

def read_run_private(slot_dir):
    """Read run.private and validate its exact contents."""
    path = os.path.join(slot_dir, 'run.private')
    try:
        raw = open(path, 'r', encoding='utf-8').read()
        data = json.loads(raw)
    except Exception as exc:  # JSONDecodeError or OSError
        raise ValueError('cannot read run.private') from exc

    if not isinstance(data, dict) or set(data.keys()) != _RUN_PRIVATE_KEYS:
        raise ValueError('unexpected keys in run.private')
    run_id = data['run_id']
    pid = data['pid']
    filetime = data['filetime']
    mode = data['mode']
    _validate_params(run_id, pid, filetime, mode)
    return data

# ----------------------------------------------------------------------
# kill_job
# ----------------------------------------------------------------------
def kill_job(slot_dir, filetime_of, kill_tree, os_name=os.name):
    """Terminate the job process according to stored metadata."""
    if os_name != 'nt':
        raise NotImplementedError
    info = read_run_private(slot_dir)  # may raise ValueError -> test expects
    pid = info['pid']
    stored_ft = info['filetime']
    cur_ft = filetime_of(pid)
    if cur_ft is None:
        return 'gone'
    if cur_ft != stored_ft:
        return 'filetime-mismatch'
    kill_tree(['taskkill', '/T', '/F', '/PID', str(pid)])
    return 'killed'

# ----------------------------------------------------------------------
# job creation (Windows only)
# ----------------------------------------------------------------------
# ctypes structures
import ctypes.wintypes as wintypes

# Constants
CREATE_SUSPENDED = 0x4
CREATE_UNICODE_ENVIRONMENT = 0x400
STARTF_USESTDHANDLES = 0x100
HANDLE_FLAG_INHERIT = 0x1
INFINITE = 0xFFFFFFFF
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_longlong),
        ('PerJobUserTimeLimit', ctypes.c_longlong),
        ('LimitFlags', wintypes.DWORD),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', wintypes.DWORD),
        ('SchedulingClass', wintypes.DWORD),
    ]

class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ('ReadOperationCount', ctypes.c_ulonglong),
        ('WriteOperationCount', ctypes.c_ulonglong),
        ('OtherOperationCount', ctypes.c_ulonglong),
        ('ReadTransferCount', ctypes.c_ulonglong),
        ('WriteTransferCount', ctypes.c_ulonglong),
        ('OtherTransferCount', ctypes.c_ulonglong),
    ]

class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ('IoInfo', IO_COUNTERS),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]

class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ('cb', wintypes.DWORD),
        ('lpReserved', wintypes.LPWSTR),
        ('lpDesktop', wintypes.LPWSTR),
        ('lpTitle', wintypes.LPWSTR),
        ('dwX', wintypes.DWORD),
        ('dwY', wintypes.DWORD),
        ('dwXSize', wintypes.DWORD),
        ('dwYSize', wintypes.DWORD),
        ('dwXCountChars', wintypes.DWORD),
        ('dwYCountChars', wintypes.DWORD),
        ('dwFillAttribute', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('wShowWindow', wintypes.WORD),
        ('cbReserved2', wintypes.WORD),
        ('lpReserved2', wintypes.LPVOID),
        ('hStdInput', wintypes.HANDLE),
        ('hStdOutput', wintypes.HANDLE),
        ('hStdError', wintypes.HANDLE),
    ]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('hProcess', wintypes.HANDLE),
        ('hThread', wintypes.HANDLE),
        ('dwProcessId', wintypes.DWORD),
        ('dwThreadId', wintypes.DWORD),
    ]

class FILETIME(ctypes.Structure):
    _fields_ = [
        ('dwLowDateTime', wintypes.DWORD),
        ('dwHighDateTime', wintypes.DWORD),
    ]

_k32_instance = None

def _k32():
    global _k32_instance
    if _k32_instance is not None:
        return _k32_instance
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    k32.CreateProcessW.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.ResumeThread.argtypes = [wintypes.HANDLE]
    k32.ResumeThread.restype = wintypes.DWORD
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateProcess.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    k32.SetHandleInformation.restype = wintypes.BOOL
    _k32_instance = k32
    return k32

def create_job_nt(memory_mb=None, os_name=os.name):
    """Create a Windows JobObject with memory limit."""
    if os_name != 'nt':
        raise NotImplementedError
    k32 = _k32()
    hJob = k32.CreateJobObjectW(None, None)
    if not hJob:
        raise RuntimeError('CreateJobObjectW failed')
    mem_bytes = (memory_mb if memory_mb is not None else JOB_MEMORY_MB) * 1024 * 1024
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = job_limit_flags()
    info.JobMemoryLimit = ctypes.c_size_t(mem_bytes)
    res = k32.SetInformationJobObject(
        hJob,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info)
    )
    if not res:
        k32.CloseHandle(hJob)
        raise RuntimeError('SetInformationJobObject failed')
    return int(hJob)

class NtChild:
    def __init__(self, pid, creation_filetime, process_handle):
        self.pid = pid
        self.creation_filetime = creation_filetime
        self._handle = process_handle
        self._closed = False
        self._exit_code = None

    def wait(self):
        if self._closed:
            if self._exit_code is not None:
                return self._exit_code
            raise RuntimeError('process handle already closed')
        k32 = _k32()
        res = k32.WaitForSingleObject(self._handle, INFINITE)
        if res != 0:
            raise RuntimeError('WaitForSingleObject failed')
        exit_code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(self._handle, ctypes.byref(exit_code)):
            raise RuntimeError('GetExitCodeProcess failed')
        self._exit_code = exit_code.value
        self.close()
        return self._exit_code

    def close(self):
        if not self._closed:
            k32 = _k32()
            k32.CloseHandle(self._handle)
            self._closed = True

def spawn_in_job(argv, job_handle, env=None, cwd=None, stdout_fd=None):
    """Spawn a process in the given job object (Windows only)."""
    if os.name != 'nt':
        raise NotImplementedError
    import msvcrt
    k32 = _k32()
    cmd = list2cmdline(argv)
    cmd_buf = ctypes.create_unicode_buffer(cmd)
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    proc_info = PROCESS_INFORMATION()
    lpEnvironment = None
    if env is not None:
        env_str = '\0'.join(f'{k}={v}' for k, v in env.items()) + '\0'
        lpEnvironment = ctypes.create_unicode_buffer(env_str)
    lpCurrentDirectory = None
    if cwd is not None:
        lpCurrentDirectory = ctypes.c_wchar_p(str(cwd))
    nul_handle = None
    stdout_handle = None
    bInheritHandles = False
    if stdout_fd is not None:
        stdout_handle = msvcrt.get_osfhandle(stdout_fd)
        if stdout_handle == -1:
            raise RuntimeError('invalid stdout_fd')
        if not k32.SetHandleInformation(stdout_handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT):
            raise RuntimeError('SetHandleInformation failed')
        nul_handle = k32.CreateFileW('NUL', 0x80000000, 0, None, 3, 0, None)
        if nul_handle == INVALID_HANDLE_VALUE:
            raise RuntimeError('CreateFileW NUL failed')
        startup.dwFlags |= STARTF_USESTDHANDLES
        startup.hStdInput = nul_handle
        startup.hStdOutput = stdout_handle
        startup.hStdError = stdout_handle
        bInheritHandles = True

    success = k32.CreateProcessW(
        None,
        cmd_buf,
        None,
        None,
        bInheritHandles,
        CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
        lpEnvironment,
        lpCurrentDirectory,
        ctypes.byref(startup),
        ctypes.byref(proc_info)
    )
    if not success:
        if nul_handle is not None:
            k32.CloseHandle(nul_handle)
        raise RuntimeError('CreateProcessW failed')

    if nul_handle is not None:
        k32.CloseHandle(nul_handle)

    if not k32.AssignProcessToJobObject(job_handle, proc_info.hProcess):
        k32.TerminateProcess(proc_info.hProcess, 1)
        k32.CloseHandle(proc_info.hProcess)
        k32.CloseHandle(proc_info.hThread)
        raise RuntimeError('AssignProcessToJobObject failed')

    if k32.ResumeThread(proc_info.hThread) == 0xFFFFFFFF:
        k32.TerminateProcess(proc_info.hProcess, 1)
        k32.CloseHandle(proc_info.hProcess)
        k32.CloseHandle(proc_info.hThread)
        raise RuntimeError('ResumeThread failed')

    ft_create = FILETIME()
    ft_exit = FILETIME()
    ft_kernel = FILETIME()
    ft_user = FILETIME()
    if not k32.GetProcessTimes(
        proc_info.hProcess,
        ctypes.byref(ft_create),
        ctypes.byref(ft_exit),
        ctypes.byref(ft_kernel),
        ctypes.byref(ft_user)
    ):
        k32.TerminateProcess(proc_info.hProcess, 1)
        k32.CloseHandle(proc_info.hProcess)
        k32.CloseHandle(proc_info.hThread)
        raise RuntimeError('GetProcessTimes failed')

    creation_ft = (ft_create.dwHighDateTime << 32) | ft_create.dwLowDateTime
    k32.CloseHandle(proc_info.hThread)

    return NtChild(proc_info.dwProcessId, creation_ft, proc_info.hProcess)

# ----------------------------------------------------------------------
# assign flow order
# ----------------------------------------------------------------------
ASSIGN_FLOW_ORDER = [
    "CreateProcess(CREATE_SUSPENDED)",
    "AssignProcessToJobObject",
    "ResumeThread",
]

def assign_flow_order():
    """Return a copy of the expected operation order list."""
    return list(ASSIGN_FLOW_ORDER)

def _read_proc_stat(pid):
    with open('/proc/%d/stat' % int(pid), 'r') as f:
        return f.read()

def _proc_stat_after_comm(pid):
    try:
        s = _read_proc_stat(pid)
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    r = s.rfind(')')
    if r < 0:
        return None
    return s[r + 2:]

def _posix_state_start(pid):
    rest = _proc_stat_after_comm(pid)
    if rest is None:
        return None, None
    fields = rest.split()
    if len(fields) < 20:
        return None, None
    return fields[0], fields[19]

def _nt_filetime(pid):
    if os.name != 'nt':
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return None
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return None
        if code.value != STILL_ACTIVE:
            return None
        class FILETIME(ctypes.Structure):
            _fields_ = [('dwLowDateTime', ctypes.c_ulong), ('dwHighDateTime', ctypes.c_ulong)]
        creation = FILETIME()
        exit_t = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(creation), ctypes.byref(exit_t), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    finally:
        k32.CloseHandle(h)

def filetime_of(pid):
    if os.name == 'nt':
        return _nt_filetime(pid)
    state, start = _posix_state_start(pid)
    if start is None:
        return None
    if state == 'Z':
        return None
    try:
        return int(start)
    except ValueError:
        return None

def pid_alive(pid):
    if os.name == 'nt':
        return _nt_filetime(pid) is not None
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    state, start = _posix_state_start(pid)
    if state is None or start is None:
        return False
    if state == 'Z':
        return False
    return True
