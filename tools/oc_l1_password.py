"""Fail-closed lane password source shared by the lane tools."""
import os
import sys


def read_lane_password(password_env):
    """Read the owner-only password file, or the unchanged env-only source."""
    path = os.environ.get("AUTOOS_OCL1_PW_FILE")
    if path is None:
        return os.environ.get(password_env)
    failed = False
    try:
        with open(path, "rb") as source:
            if os.name == "nt":
                # Fail closed, including unavailable PowerShell or a NULL DACL.
                import subprocess
                script = (
                    "$ErrorActionPreference='Stop'; "
                    "$a=Get-Acl -LiteralPath $args[0]; "
                    "$o=$a.GetOwner([System.Security.Principal.SecurityIdentifier]); "
                    "$r=$a.GetAccessRules($true,$true,"
                    "[System.Security.Principal.SecurityIdentifier]); "
                    "if (!$r.Count) {exit 1}; foreach ($e in $r) {"
                    "if ($e.AccessControlType -eq 'Allow' -and "
                    "$e.IdentityReference -ne $o) {exit 1}}; exit 0"
                )
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script, path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                if result.returncode:
                    raise ValueError
            elif os.fstat(source.fileno()).st_mode & 0o077:
                raise ValueError
            value = source.read().decode("utf-8")
    except Exception:
        failed = True
    # Outside the handler: no decoder exception can disclose file contents.
    if failed:
        print(path or "AUTOOS_OCL1_PW_FILE", file=sys.stderr)
        raise SystemExit(2)
    return value.removesuffix("\n")
