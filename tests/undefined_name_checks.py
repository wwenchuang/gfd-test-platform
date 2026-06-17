#!/usr/bin/env python3
"""Fail the build on migrated-code runtime NameError risks.

``py_compile`` only validates syntax. During the migration away from the
monolithic server, missing imports show up as runtime ``NameError`` instead.
This check runs pyflakes and treats only ``undefined name`` findings as fatal,
leaving unused-import cleanup out of the critical path.
"""

import subprocess
import sys


def main() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", "task_server"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = proc.stdout or ""
    undefined = [line for line in output.splitlines() if "undefined name" in line]
    if undefined:
        print("\n".join(undefined))
        raise SystemExit(1)
    print({"ok": True, "check": "undefined-name", "scanned": "task_server"})


if __name__ == "__main__":
    main()
