#!/usr/bin/env python3
"""Assert the hardening contract of configuration/docker/ai-stack/compose.yml.

No PyYAML: the suite runs on machines where nothing is installed yet, so the
template is kept anchor-free and this reads it by indentation (services at two
spaces, their keys at four). Prints one line per violation; exit 1 on any.

    python3 tests/helpers/check_compose.py configuration/docker/ai-stack/compose.yml
"""
import re
import sys


def services(text):
    out, cur, in_services = {}, None, False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\S", line):
            in_services = line.startswith("services:")
            cur = None
            continue
        if not in_services:
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            cur = m.group(1)
            out[cur] = []
            continue
        if cur:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def main(path):
    text = open(path, encoding="utf-8").read()
    svc = services(text)
    bad = []
    for want in ("omniroute", "opencode", "openhands"):
        if want not in svc:
            bad.append("missing service " + want)
    for name, body in svc.items():
        def need(pattern, why):
            if not re.search(pattern, body, re.M):
                bad.append("%s: %s" % (name, why))
        need(r"^    restart: unless-stopped$", "restart must be unless-stopped")
        need(r"no-new-privileges:true", "no-new-privileges missing")
        need(r"^    cap_drop:\n      - ALL$", "cap_drop ALL missing")
        need(r"^    mem_limit: ", "mem_limit missing")
        need(r"^    pids_limit: ", "pids_limit missing")
        need(r"^    healthcheck:", "healthcheck missing")
        need(r"^    container_name: ", "container_name missing")
        need(r"^      - autoos-ai$|^      autoos-ai:", "not on the autoos-ai network")
        if re.search(r"^    privileged:", body, re.M):
            bad.append(name + ": privileged is forbidden")
        if re.search(r"network_mode:\s*host", body):
            bad.append(name + ": host networking is forbidden")
        img = re.search(r"^    image: (\S+)$", body, re.M)
        if not img:
            bad.append(name + ": no image")
        elif "build:" not in body and "@sha256:" not in img.group(1):
            bad.append(name + ": image not pinned by digest: " + img.group(1))
        elif img.group(1).endswith(":latest"):
            bad.append(name + ": :latest image")
        # Ports: every publish goes through the one bind variable.
        for port in re.findall(r'^      - "([^"]+)"$', body, re.M):
            if re.match(r"^\d", port) and ":" in port and "${" not in port:
                bad.append("%s: port %s bypasses AUTOOS_STACK_BIND" % (name, port))
        # Secrets only through env_file; nothing that looks like a key inline.
        if re.search(r"(sk-[A-Za-z0-9]{8,}|PASSWORD=\S|_KEY: ['\"]?[A-Za-z0-9]{12,})", body):
            bad.append(name + ": inline secret")
    om, oc, oh = svc.get("omniroute", ""), svc.get("opencode", ""), svc.get("openhands", "")
    for name, body in (("omniroute", om), ("opencode", oc)):
        if not re.search(r'^    user: "\$\{AUTOOS_UID:-1000\}:\$\{AUTOOS_GID:-1000\}"$', body, re.M):
            bad.append(name + ": must run as the host user (AUTOOS_UID:AUTOOS_GID)")
        if not re.search(r"^    read_only: true$", body, re.M):
            bad.append(name + ": root filesystem must be read-only")
        if re.search(r"^    cap_add:", body, re.M):
            bad.append(name + ": needs no capability back")
    if not re.search(r'REQUIRE_API_KEY: "true"', om):
        bad.append("omniroute: REQUIRE_API_KEY must be pinned to true")
    # The gateway runs qodercli for the Qoder PAT login: a derived image (the
    # upstream one has none) and a writable HOME - qodercli crashes at start
    # without one, and the rootfs is read-only.
    if not re.search(r"^    build:\n      context: \.\n      dockerfile: omniroute\.Dockerfile$", om, re.M):
        bad.append("omniroute: must build omniroute.Dockerfile (the qodercli layer)")
    if not re.search(r"^    image: autoos/omniroute:\d+\.\d+\.\d+-autoos\d+$", om, re.M):
        bad.append("omniroute: the local image must be autoos/omniroute:<upstream version>-autoos<n>")
    if not re.search(r"^      CLI_QODER_BIN: /usr/local/bin/qodercli$", om, re.M):
        bad.append("omniroute: CLI_QODER_BIN must be the absolute path /usr/local/bin/qodercli")
    home = re.search(r"^      HOME: (/\S+)$", om, re.M)
    if not home:
        bad.append("omniroute: HOME must be set to the writable qoder home")
    elif not re.search(r"^      - \$\{AUTOOS_STACK_DATA:\?[^}]*\}/qoder-home:%s$" % re.escape(home.group(1)), om, re.M):
        bad.append("omniroute: HOME %s must be the mount of ${AUTOOS_STACK_DATA}/qoder-home" % home.group(1))
    if not re.search(r"^      - \$\{AUTOOS_STACK_DATA:\?[^}]*\}/omniroute:/app/data$", om, re.M):
        bad.append("omniroute: the gateway data dir must stay mounted at /app/data")
    if "docker.sock" in om or "docker.sock" in oc:
        bad.append("only openhands may mount the docker socket")
    code = "${AUTOOS_CODE_DIR:?"
    if not re.search(r"- \$\{AUTOOS_CODE_DIR:\?[^}]*\}:\$\{AUTOOS_CODE_DIR\}:rw", oc):
        bad.append("opencode: the code dir must be mounted at the same path")
    if "SANDBOX_VOLUMES: " + code not in oh or ":${AUTOOS_CODE_DIR}:rw" not in oh:
        bad.append("openhands: SANDBOX_VOLUMES must mount the code dir at the same path")
    caps = re.search(r"^    cap_add:\n((?:      - \S+\n?)+)", oh, re.M)
    allowed = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID"}
    if caps:
        got = set(re.findall(r"- (\S+)", caps.group(1)))
        if not got <= allowed:
            bad.append("openhands: unexpected capabilities %s" % sorted(got - allowed))
    if "http://omniroute:20128/v1" not in oh:
        bad.append("openhands: must reach the gateway by name")
    for line in bad:
        print(line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
