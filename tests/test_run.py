import os

import run


def test_stop_only_ends_python_processes():
    """--stop checks that the pid file's number still belongs to Python (tasklist on Windows, /proc on Linux)."""
    assert run._is_python_process(os.getpid())
    assert not run._is_python_process(99999999)  # no such process
