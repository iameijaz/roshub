#!/usr/bin/env bash
# ROSHub setup from a git checkout (pip users run: roshub setup). See roshub/install.sh.
exec bash "$(cd "$(dirname "$0")" && pwd)/roshub/install.sh" "$@"
