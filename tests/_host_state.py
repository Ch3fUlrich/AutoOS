"""_host_state.py — pin the spawner's live-host reads for unit tests.

Before it starts a worker the spawner measures three things off the machine it
happens to run on: the daily cost gate, how many workers the host already runs,
and how much memory is free. A unit test that inherits them passes or fails on
the state of that machine rather than on the code under test. Measured 2026-10-09
(lane AO-ADMISSION-2): with the day budget over cap and the host near its memory
floor, six tests in ``tests/test_autoos_spawner.py``, one in
``tests/test_run_budget_spawner.py`` and four in ``tests/test_t2_record_pin.py``
refused for a reason none of them was about — while CI, a machine with no spend
report at all, stayed green.

Every name here is an env knob ``tools/autoos-agent.py`` already reads; nothing
in production changed to make a test pass:

===========================  ==================================================
``AUTOOS_DAILY_GATE_FILE``   the spend report the run budget gate reads — else
                             the default ``$XDG_STATE_HOME/autoos/daily-gate.json``
``AUTOOS_WORKERS_DIR``       the worker records the live-worker count reads
``AUTOOS_MEMINFO_PATH``      the file ``MemAvailable`` is read from (else
                             ``/proc/meminfo``)
``AUTOOS_ADMISSION_OFF``     the spawner's own test-only escape for host admission
===========================  ==================================================

Use:

    import _host_state as host_state

    state = host_state.install(self)              # restored on addCleanup
    env = {"AUTOOS_STATE_DIR": tmp, **host_state.pin_values(tmp)}   # clear=True dicts
"""
import datetime
import io
import json
import os
import shutil
import tempfile

GATE_ENV = "AUTOOS_DAILY_GATE_FILE"
WORKERS_ENV = "AUTOOS_WORKERS_DIR"
MEMINFO_ENV = "AUTOOS_MEMINFO_PATH"
ADMISSION_OFF_ENV = "AUTOOS_ADMISSION_OFF"
STATE_HOME_ENV = "XDG_STATE_HOME"
LOCALAPPDATA_ENV = "LOCALAPPDATA"

# A host with this much free clears every floor the shipped registry names
# (catalog/ai-registry.json `host_admission.mem_available_floor_mb` is 4608).
HEALTHY_MEMINFO_MB = 65536
# The default budget of the gate document itself; the shipped refresh script
# writes the real number. A test that wants a refusal names its own numbers.
DEFAULT_BUDGET_USD = 14.0


def utc_day(offset_days=0):
    """The UTC day a gate document has to carry to speak about today."""
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=offset_days)).strftime("%Y-%m-%d")


def write_gate(directory, verdict="ok", usd=0.0, budget=DEFAULT_BUDGET_USD,
               day=None, name="daily-gate.json"):
    """Write a spend report into `directory` and return its path.

    Same shape `tools/cost-gate-refresh.py` writes, and same verdict spelling, so
    the gate's own parsing is what is under test — not a fixture that happens to
    be unreadable.
    """
    data = {"day": day if day is not None else utc_day(), "usd": usd,
            "budget": budget, "verdict": verdict, "by_provider": {},
            "unverified": False}
    path = os.path.join(str(directory), name)
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def write_meminfo(directory, available_mb=HEALTHY_MEMINFO_MB, name="meminfo"):
    """A /proc/meminfo stand-in whose only field the spawner reads."""
    path = os.path.join(str(directory), name)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("MemTotal:       %d kB\nMemFree:        1 kB\n"
                 "MemAvailable:   %d kB\n" % (available_mb * 1024, available_mb * 1024))
    return path


def pin_values(directory, gate="allow", workers=True, meminfo="healthy",
               admission_off=False):
    """The env-var assignments that pin the host reads, pointing at `directory`.

    Returns a plain dict, so a test that already builds its own environment —
    notably one installed with ``mock.patch.dict(..., clear=True)``, which drops
    every module-level pin with the rest of the environment — can fold these in
    next to its own names.

    `gate` is "allow" (a report that says the day is inside budget), "block" (one
    that says it is not) or "absent" (no report anywhere: the variable is unset
    and the *default* path is a throwaway state home with no file in it, which is
    what a fresh CI machine is). `workers` names a fresh, empty records dir;
    pass False when the test has one of its own to name. `meminfo` is "healthy"
    or None (None leaves the real ``/proc/meminfo`` read in place).
    `admission_off` sets the spawner's own test-only escape for host admission —
    the escape, not a fake measurement, for a test whose subject is not the room
    on the host (``tools/autoos-agent.py`` ``host_admission_refusal``).
    """
    directory = str(directory)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    env = {}
    if gate == "absent":
        # The variable unset AND the default path somewhere that holds no file:
        # a state home this test made and never wrote a report into.
        env[GATE_ENV] = ""
        env[STATE_HOME_ENV] = os.path.join(directory, "empty-state-home")
        env[LOCALAPPDATA_ENV] = os.path.join(directory, "empty-localappdata")
    elif gate in ("allow", "block"):
        env[GATE_ENV] = write_gate(directory, verdict="ok" if gate == "allow" else "block",
                                   usd=0.0 if gate == "allow" else DEFAULT_BUDGET_USD + 1,
                                   name="host-state-gate.json")
    else:
        raise ValueError("host_state: unknown gate mode %r" % (gate,))
    if workers:
        env[WORKERS_ENV] = os.path.join(directory, "workers")
        os.makedirs(env[WORKERS_ENV], exist_ok=True)
    if meminfo == "healthy":
        env[MEMINFO_ENV] = write_meminfo(directory)
    elif meminfo not in (None, "none"):
        raise ValueError("host_state: unknown meminfo mode %r" % (meminfo,))
    if admission_off:
        env[ADMISSION_OFF_ENV] = "1"
    return env


def install(case=None, directory=None, **kw):
    """Apply `pin_values` to ``os.environ`` and return the state to restore with.

    `case` is anything with `addCleanup` (a ``unittest.TestCase``): the pins then
    come off by themselves. A module (``setUpModule``) has no such hook, so it
    keeps the return value and calls `uninstall` in ``tearDownModule``.

    `directory` is where the fixtures go; the default is a fresh temp dir, which
    `uninstall` removes — so pass the test's own dir when it cleans that up too.
    """
    owns_tmp = directory is None
    directory = tempfile.mkdtemp(prefix="autoos-host-state-") if owns_tmp else str(directory)
    env = pin_values(directory, **kw)
    keys = [GATE_ENV, STATE_HOME_ENV, LOCALAPPDATA_ENV, WORKERS_ENV, MEMINFO_ENV,
            ADMISSION_OFF_ENV]
    saved = {k: os.environ.get(k) for k in keys}
    for k, v in env.items():
        if v == "":
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    state = {"saved": saved, "dir": directory, "owns_tmp": owns_tmp}
    if case is not None:
        case.addCleanup(uninstall, state)
    return state


def uninstall(state):
    """Put back exactly what `install` took: an absent name stays absent."""
    for key, old in state["saved"].items():
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old
    if state["owns_tmp"]:
        shutil.rmtree(state["dir"], ignore_errors=True)
