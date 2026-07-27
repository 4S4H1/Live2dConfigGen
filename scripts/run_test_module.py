"""Run one unittest module and persist its authoritative result atomically.

Some PySide6/Qt combinations on Windows can access-violate during Python
interpreter finalization after unittest has already completed successfully.
The release test runner therefore executes every module in a child process.
Once TextTestRunner returns, this helper writes and fsyncs a result marker,
flushes both output streams, and exits without running interpreter teardown.

Crashes during a test, loader errors, assertion failures, and incomplete runs
cannot produce a successful marker, so the parent runner still fails closed.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_result(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    with temporary.open("wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _failure_details(
    failures: list[tuple[unittest.case.TestCase, str]],
) -> list[dict[str, str]]:
    return [
        {
            "test": test.id(),
            "traceback": traceback,
        }
        for test, traceback in failures
    ]


def _terminate_without_runtime_finalization(exit_code: int) -> None:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p
        terminate_process = kernel32.TerminateProcess
        terminate_process.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        terminate_process.restype = ctypes.c_int
        if terminate_process(get_current_process(), exit_code):
            raise AssertionError("TerminateProcess unexpectedly returned")
        raise OSError(
            ctypes.get_last_error(),
            "TerminateProcess failed after writing the test result",
        )
    os._exit(exit_code)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("module")
    parser.add_argument("--result", required=True, type=Path)
    arguments = parser.parse_args()

    suite = unittest.defaultTestLoader.loadTestsFromName(arguments.module)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    successful = result.wasSuccessful()
    payload: dict[str, object] = {
        "schema_version": 1,
        "module": arguments.module,
        "successful": successful,
        "tests_run": result.testsRun,
        "failures": _failure_details(result.failures),
        "errors": _failure_details(result.errors),
        "skipped": [
            {"test": test.id(), "reason": reason}
            for test, reason in result.skipped
        ],
    }
    _write_result(arguments.result, payload)
    sys.stdout.flush()
    sys.stderr.flush()

    # Do not return through Py_FinalizeEx. The result above is authoritative
    # and durable; bypassing only the known-bad post-test Qt finalization keeps
    # real assertion, loader, and native in-test failures visible to the parent.
    _terminate_without_runtime_finalization(0 if successful else 1)


if __name__ == "__main__":
    main()
