#!/usr/bin/env python3
"""Midscene Task Platform server entrypoint.

The legacy monolith lives in ``legacy/midscene-upload.legacy.py`` for audit
and rollback reference. Runtime code should be changed under ``task_server/``.
"""

from task_server.app import main


if __name__ == "__main__":
    main()
