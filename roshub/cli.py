"""`roshub` command for pip installs.

First run (or `roshub setup [--yes]`) runs the bundled install.sh, which copies the app into
~/.roshub and asks the usual questions. After `pip install -U roshub` the app files are refreshed
automatically. Then it starts the server through the same launcher git installs use.
"""
import os
import subprocess
import sys

from roshub import __version__

PKG = os.path.dirname(os.path.abspath(__file__))


def main():
    rh = os.environ.get("ROSHUB_HOME", os.path.expanduser("~/.roshub"))
    env = dict(os.environ, ROSHUB_PIP="1")
    setup = ["bash", os.path.join(PKG, "install.sh")]
    stamp = os.path.join(rh, "VERSION")
    args = sys.argv[1:]

    if args[:1] in (["--version"], ["-V"]):
        print(f"roshub {__version__}")
        return 0
    if args[:1] == ["setup"] or not os.path.isfile(os.path.join(rh, "config.yaml")):
        rc = subprocess.call(setup + args[1:], env=env)
        if rc or args[:1] == ["setup"]:
            _stamp(stamp, rc)
            return rc
    else:
        try:
            with open(stamp) as f:
                current = f.read().strip()
        except OSError:
            current = ""
        if current != __version__:
            subprocess.check_call(setup + ["--update"], env=env)
    _stamp(stamp, 0)
    os.execvp("bash", ["bash", os.path.join(rh, "bin", "roshub")])


def _stamp(path, rc):
    if rc == 0 and os.path.isdir(os.path.dirname(path)):
        with open(path, "w") as f:
            f.write(__version__ + "\n")


if __name__ == "__main__":
    sys.exit(main())
