import configparser
import re
from pathlib import Path

def load_unit(path):
    text = path.read_text()
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.read_string(text)
    return cp

def test_service():
    base = Path(__file__).resolve().parents[1] / "configuration" / "autostart"
    svc = load_unit(base / "autoos-combo-drift.service")
    assert svc["Service"]["Type"] == "oneshot"
    exec_start = svc["Service"]["ExecStart"]
    assert "unit_breaker.py --unit autoos-combo-drift.service" in exec_start
    assert "combo_drift.py check --alert" in exec_start.split(" -- ")[-1]
    assert "cron" not in exec_start
    for key in ["Service"]:
        for val in svc[key].values():
            assert not re.search(r"/home/|10\.\d+\.\d+\.\d+|192\.168\.", val)

def test_timer():
    base = Path(__file__).resolve().parents[1] / "configuration" / "autostart"
    tmr = load_unit(base / "autoos-combo-drift.timer")
    assert tmr["Timer"]["OnCalendar"] == "daily"
    assert tmr["Timer"]["Persistent"] == "true"
    assert tmr["Timer"]["Unit"] == "autoos-combo-drift.service"
    assert tmr["Install"]["WantedBy"] == "timers.target"
    for key in ["Timer", "Install"]:
        for val in tmr[key].values():
            assert not re.search(r"/home/|10\.\d+\.\d+\.\d+|192\.168\.", val)

