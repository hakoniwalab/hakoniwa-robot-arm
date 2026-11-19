#!/usr/bin/env python3
"""Forge the KUKA iiwa 14 runtime model (MuJoCo Menagerie) without a shell dependency."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PACK_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from _hakoniwa_composer import (  # noqa: E402
    HakoniwaComposerError,
    model_forge_root,
    model_install_dir,
    model_install_staging_dir,
    publish_model_install,
)

ROBOT_ID = "iiwa14"
RECIPE_DIR = PACK_ROOT / "recipes" / ROBOT_ID
SOURCE_YAML = PACK_ROOT / "sources" / "models" / ROBOT_ID / "source.yaml"


def _mbody_root() -> Path:
    configured = os.environ.get("HAKONIWA_MBODY_REGISTRY_ROOT", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (PACK_ROOT.parent / "hakoniwa-mbody-registry").resolve()
    )


def _run_tool(script: Path, *args: object) -> None:
    command = [sys.executable, str(script), *(str(arg) for arg in args)]
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=PACK_ROOT, check=True)


def _reset_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    path.mkdir(parents=True, exist_ok=True)


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise HakoniwaComposerError(f"required directory not found: {source}")
    shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise HakoniwaComposerError(f"required file not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _write_checksums(build_dir: Path) -> None:
    files = [build_dir / "LICENSE", build_dir / "iiwa14.xml"]
    files.extend(sorted((build_dir / "assets").glob("*.obj")))
    if len(files) == 2:
        raise HakoniwaComposerError(
            f"iiwa 14 mesh assets not found: {build_dir / 'assets'}"
        )

    lines = []
    for path in files:
        if not path.is_file():
            raise HakoniwaComposerError(f"checksum input not found: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(build_dir).as_posix()}")
    (build_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def forge() -> Path:
    mbody_root = _mbody_root()
    fetch_tool = mbody_root / "tools" / "fetch.py"
    if not fetch_tool.is_file():
        raise HakoniwaComposerError(
            "hakoniwa-mbody-registry sibling checkout was not found: "
            f"{mbody_root}"
        )

    forge_root = model_forge_root(ROBOT_ID)
    source_dir = forge_root / "source"
    build_dir = forge_root / "build"
    staging_dir = model_install_staging_dir(ROBOT_ID)
    output_dir = model_install_dir(ROBOT_ID)

    _reset_directory(source_dir)
    _reset_directory(build_dir)
    _reset_directory(staging_dir)

    print("=== iiwa 14 Forge: Fetch Pinned Source ===")
    _run_tool(fetch_tool, SOURCE_YAML, "--output-dir", source_dir)

    upstream = source_dir / "kuka_iiwa_14"
    assets_source = upstream / "assets"
    assets_dest = build_dir / "assets"
    assets_dest.mkdir(parents=True, exist_ok=True)
    mesh_files = sorted(assets_source.glob("*.obj"))
    if not mesh_files:
        raise HakoniwaComposerError(f"iiwa 14 mesh assets not found: {assets_source}")
    for source in mesh_files:
        _copy_file(source, assets_dest / source.name)
    _copy_file(upstream / "LICENSE", build_dir / "LICENSE")

    print("\n=== iiwa 14 Forge: Servos, Gravity Compensation and Wrist Payload ===")
    _run_tool(
        PACK_ROOT / "tools" / "configure_iiwa14_mjcf.py",
        upstream / "iiwa14.xml",
        "--payload",
        RECIPE_DIR / "config" / "model" / "wrist-payload.json",
        "--output",
        build_dir / "iiwa14.xml",
    )
    _run_tool(
        PACK_ROOT / "tools" / "configure_mjcf_options.py",
        build_dir / "iiwa14.xml",
        "--timestep",
        "0.001",
        "--integrator",
        "Euler",
        "--output",
        build_dir / "iiwa14.xml",
    )

    print("\n=== iiwa 14 Forge: Synchronize Joint Limits ===")
    _run_tool(
        PACK_ROOT / "tools" / "sync_arm_joint_limits.py",
        build_dir / "iiwa14.xml",
        "--runtime-config-dir",
        RECIPE_DIR / "config" / "actuator" / "joint",
        "--validate-mjcf",
        build_dir / "iiwa14.xml",
    )

    _write_checksums(build_dir)

    _copy_tree(build_dir, staging_dir)
    published = publish_model_install(ROBOT_ID)

    print("iiwa 14 forge complete.")
    print(f"  - runtime MJCF: {output_dir / 'iiwa14.xml'}")
    print(f"  - mesh assets:  {output_dir / 'assets'}")
    print(f"  - checksums:     {output_dir / 'SHA256SUMS'}")
    return published


def main() -> int:
    try:
        forge()
        return 0
    except subprocess.CalledProcessError as error:
        print(
            f"error: iiwa 14 forge tool failed with exit code {error.returncode}: "
            f"{subprocess.list2cmdline(error.cmd)}",
            file=sys.stderr,
        )
        return error.returncode or 1
    except (HakoniwaComposerError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
