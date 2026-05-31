"""Pack paper benchmark artifacts into one zip, then remove loose files."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_ROOT = REPO / "Doc" / "benchmark_outputs"
ZIP_PATH = OUT_ROOT / "paper_benchmark_results.zip"

RUNS = ("paper_comparison_main_2", "paper_comparison_main_3")
INCLUDE_SUBDIRS = ("csv", "plots")
README = """Paper benchmark results (NP=20, example_input.json)
================================================================

Contents:
  paper_comparison_main_2/  — hho, hmpa, cssrank (540 jobs)
  paper_comparison_main_3/  — ga, tabu, gatabu, ga_tabu_inline, ga_tabu_topk (900 jobs)
  tables_report.xlsx        — merged 8-method Excel tables

Each run folder contains manifest.json, csv/, plots/.
Excluded from archive: runs/, state/, logs (regenerable / debug only).

To rebuild Excel locally:
  1. Unzip this file under Doc/benchmark_outputs/
  2. python Doc/build_tables_xlsx.py
"""


def collect_files() -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for run in RUNS:
        run_dir = OUT_ROOT / run
        manifest = run_dir / "manifest.json"
        if manifest.is_file():
            items.append((manifest, f"{run}/manifest.json"))
        for sub in INCLUDE_SUBDIRS:
            sub_dir = run_dir / sub
            if not sub_dir.is_dir():
                continue
            for path in sorted(sub_dir.rglob("*")):
                if path.is_file():
                    arc = f"{run}/{sub}/{path.relative_to(sub_dir).as_posix()}"
                    items.append((path, arc))
    xlsx = OUT_ROOT / "tables_report.xlsx"
    if xlsx.is_file():
        items.append((xlsx, "tables_report.xlsx"))
    return items


def build_zip() -> None:
    items = collect_files()
    if not items:
        raise SystemExit("No files to pack.")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("README.txt", README)
        for src, arc in items:
            zf.write(src, arc)
    size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
    print(f"Created {ZIP_PATH} ({len(items) + 1} entries, {size_mb:.1f} MB)")


def verify_zip() -> None:
    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        names = zf.namelist()
        for req in (
            "README.txt",
            "paper_comparison_main_2/manifest.json",
            "paper_comparison_main_3/manifest.json",
            "tables_report.xlsx",
        ):
            if req not in names:
                raise SystemExit(f"Zip missing required entry: {req}")
        bad = zf.testzip()
        if bad:
            raise SystemExit(f"Zip corrupt at: {bad}")
    print("Zip verification OK.")


def cleanup() -> None:
    removed_dirs = 0
    removed_files = 0
    for run in RUNS:
        run_dir = OUT_ROOT / run
        if not run_dir.exists():
            continue
        for sub in ("runs", "state"):
            p = run_dir / sub
            if p.exists():
                shutil.rmtree(p)
                removed_dirs += 1
        for sub in INCLUDE_SUBDIRS:
            p = run_dir / sub
            if p.exists():
                shutil.rmtree(p)
                removed_dirs += 1
        manifest = run_dir / "manifest.json"
        if manifest.is_file():
            manifest.unlink()
            removed_files += 1
        # remove leftover zip inside main_2
        inner_zip = run_dir / "hho-hmpa-cssrank-csv.zip"
        if inner_zip.is_file():
            inner_zip.unlink()
            removed_files += 1
        if run_dir.exists() and not any(run_dir.iterdir()):
            run_dir.rmdir()
            removed_dirs += 1
    for name in (
        "api_server.log",
        "api_server.err",
        "paper_comparison_main_3_run.log",
        "tables_report.xlsx",
    ):
        p = OUT_ROOT / name
        if p.is_file():
            try:
                p.unlink()
                removed_files += 1
            except OSError as exc:
                print(f"  skip locked file {name}: {exc}")
    for d in ("benchmark_20260218_130320", "_orchestration_logs"):
        p = OUT_ROOT / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed_dirs += 1
    print(f"Cleanup done: removed {removed_dirs} dirs, {removed_files} files.")
    print(f"Remaining in {OUT_ROOT}:")
    for p in sorted(OUT_ROOT.iterdir()):
        if p.is_file():
            print(f"  {p.name} ({p.stat().st_size / 1024 / 1024:.1f} MB)")
        else:
            print(f"  {p.name}/")


if __name__ == "__main__":
    build_zip()
    verify_zip()
    cleanup()
