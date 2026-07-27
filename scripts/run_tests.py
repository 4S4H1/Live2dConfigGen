"""Run every unittest module in an isolated, fail-closed child process."""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
MODULE_RUNNER = ROOT / "scripts" / "run_test_module.py"


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _declared_test_count(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(tree)
    )


def _selected_files(module_names: list[str] | None) -> list[Path]:
    all_files = sorted(TESTS.rglob("test*.py"))
    if not module_names:
        return all_files
    by_module = {_module_name(path): path for path in all_files}
    selected: list[Path] = []
    for raw_name in module_names:
        module = raw_name if raw_name.startswith("tests.") else f"tests.{raw_name}"
        path = by_module.get(module)
        if path is None:
            available = ", ".join(sorted(by_module))
            raise ValueError(f"unknown test module {raw_name!r}; available: {available}")
        if path not in selected:
            selected.append(path)
    return selected


def _load_result(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"missing or invalid atomic result marker: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("result marker must contain a JSON object")
    return payload


def _validate_result(
    payload: dict[str, object],
    *,
    module: str,
    expected_count: int,
) -> None:
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported result marker schema")
    if payload.get("module") != module:
        raise ValueError("result marker module does not match the child")
    if payload.get("tests_run") != expected_count:
        raise ValueError(
            f"expected {expected_count} tests, child completed "
            f"{payload.get('tests_run')!r}"
        )
    failures = payload.get("failures")
    errors = payload.get("errors")
    if not isinstance(failures, list) or not isinstance(errors, list):
        raise ValueError("result marker failure/error fields are invalid")
    if payload.get("successful") is not True or failures or errors:
        raise ValueError(
            f"child reported unsuccessful result "
            f"({len(failures)} failures, {len(errors)} errors)"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--module",
        action="append",
        help="run only this tests.<name> module (repeatable)",
    )
    arguments = parser.parse_args()

    try:
        files = _selected_files(arguments.module)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if not files:
        print("No tests/test*.py files were found.", file=sys.stderr)
        return 2

    expected_total = sum(_declared_test_count(path) for path in files)
    build_root = ROOT / "build" / "test-results"
    build_root.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="run-", dir=build_root))
    try:
        for index, path in enumerate(files, start=1):
            module = _module_name(path)
            expected_count = _declared_test_count(path)
            print(
                f"\n=== [{index}/{len(files)}] {module} "
                f"({expected_count} tests) ===",
                flush=True,
            )

            module_root = work_root / path.stem
            settings_root = module_root / "settings"
            settings_root.mkdir(parents=True)
            result_path = module_root / "result.json"
            environment = dict(os.environ)
            environment["PYTHONFAULTHANDLER"] = "1"
            environment["L2D_CONFIG_EDITOR_SETTINGS_DIR"] = str(settings_root)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "faulthandler",
                    str(MODULE_RUNNER),
                    module,
                    "--result",
                    str(result_path),
                ],
                cwd=ROOT,
                env=environment,
                check=False,
            )

            try:
                payload = _load_result(result_path)
                _validate_result(
                    payload,
                    module=module,
                    expected_count=expected_count,
                )
            except ValueError as error:
                print(
                    f"{module} failed closed: {error}; "
                    f"process exit code {completed.returncode}",
                    file=sys.stderr,
                    flush=True,
                )
                return completed.returncode or 1
            if completed.returncode != 0:
                print(
                    f"{module} produced a success marker but exited with "
                    f"code {completed.returncode}",
                    file=sys.stderr,
                    flush=True,
                )
                return completed.returncode or 1
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    print(
        f"\nAll {expected_total} tests passed "
        f"across {len(files)} isolated modules.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
