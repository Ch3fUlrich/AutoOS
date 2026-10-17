#!/usr/bin/env bash
# Compatibility shim. The real implementation is ../../setup.sh.
#
# This script used to be a second, diverging copy of the Linux setup. It carried
# three `apt Install` typos (capital I), a non-existent `initx` package, and an
# `exec zsh` in the middle that replaced the shell process -- so Docker, the
# docker group and the Portainer agent were never installed at all. Rather than
# maintain two implementations that disagree, it now forwards.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Linux/bash/install.sh is deprecated; running $ROOT/setup.sh instead."
echo
exec bash "$ROOT/setup.sh" "$@"
