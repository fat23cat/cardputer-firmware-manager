from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .core import (
    FirmwareError,
    GitHubReleaseClient,
    load_catalog,
    resolve_apps,
    resolve_sd_root,
    sd_path,
    sha256,
    stage_images,
    validate_image,
    validate_layout,
)


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = PROJECT / "firmware-manager.json"


def _build_application(
    app_id: str,
    app: Mapping[str, Any],
    repository: Path,
) -> None:
    environment = os.environ.copy()
    build_environment = app.get("build_environment", {"unset": []})
    for variable in build_environment["unset"]:
        environment.pop(variable, None)
    print(f"building {app_id} in {repository}")
    subprocess.run(
        app["build_command"],
        cwd=repository,
        check=True,
        env=environment,
    )


def _application_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--app",
        action="append",
        default=[],
        metavar="ID",
        help="application id; repeat for several, defaults to all",
    )
    parser.add_argument("--sd", type=Path, required=True, help="mounted FAT32 SD root")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cardputer-firmware")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="list managed applications")
    doctor = subparsers.add_parser(
        "doctor", help="validate catalog, layout, and staged images"
    )
    doctor.add_argument("--sd", type=Path)

    local = subparsers.add_parser(
        "local", help="stage firmware built from local repositories"
    )
    _application_arguments(local)
    local.add_argument(
        "--workspace",
        type=Path,
        default=PROJECT.parent,
        help="directory containing the application repositories",
    )
    local.add_argument(
        "--build", action="store_true", help="build selected repositories first"
    )

    release = subparsers.add_parser(
        "release", help="download and stage GitHub Release firmware"
    )
    _application_arguments(release)
    release.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="APP=TAG",
        help="pin an app to a release tag; otherwise use latest",
    )
    return parser


def _tag_map(values: List[str], selected: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise FirmwareError("release tag must use APP=TAG")
        app_id, tag = value.split("=", 1)
        if app_id not in selected:
            raise FirmwareError(f"tag supplied for unselected application: {app_id}")
        if not tag:
            raise FirmwareError(f"empty release tag for {app_id}")
        result[app_id] = tag
    return result


def _print_staged(selected: List[str]) -> None:
    print("staged successfully; safely eject the card and exit CRUB usbsd")
    print("remount the card in CRUB with 'sd', then run:")
    for app_id in selected:
        print(f"  up{app_id}")


def _doctor(
    catalog: Mapping[str, Any],
    sd_root: Optional[Path],
    *,
    require_mount: bool = True,
) -> None:
    layout = validate_layout(PROJECT / catalog["layout"], catalog["flash_size"])
    by_name = {partition["name"]: partition for partition in layout}
    for expected in catalog["partition_contract"]:
        partition = by_name.get(expected["name"])
        if not partition:
            raise FirmwareError(
                f"required partition {expected['name']} is missing from layout"
            )
        mismatched = [
            field
            for field in ("type", "subtype", "offset", "size")
            if partition[field] != expected[field]
        ]
        if mismatched:
            raise FirmwareError(
                f"required partition {expected['name']} has an invalid "
                f"{mismatched[0]}"
            )
    launcher = catalog.get("crub", {}).get("launcher_partition")
    if (
        launcher not in by_name
        or by_name[launcher]["type"] != "app"
        or by_name[launcher]["subtype"] != "test"
    ):
        raise FirmwareError("CRUB launcher partition does not match the layout")
    for app_id, app in catalog["apps"].items():
        partition = by_name.get(app["partition"])
        if (
            not partition
            or partition["type"] != "app"
            or partition["size"] != app["partition_size"]
        ):
            raise FirmwareError(f"{app_id}: catalog does not match partition layout")
    print(
        f"layout: ok ({len(layout)} partitions, {catalog['flash_size']} bytes flash)"
    )
    if sd_root:
        sd_root = resolve_sd_root(sd_root, require_mount=require_mount)
        lock_path = sd_path(sd_root, "firmware/firmware-manager-lock.json")
        sums_path = sd_path(sd_root, "firmware/SHA256SUMS")
        try:
            lock = json.loads(lock_path.read_text()) if lock_path.exists() else {}
        except (OSError, json.JSONDecodeError) as error:
            raise FirmwareError(f"invalid firmware lock file: {lock_path}") from error
        lock_apps = lock.get("apps", {}) if isinstance(lock, dict) else {}
        if not isinstance(lock_apps, dict):
            raise FirmwareError(f"invalid firmware lock file: {lock_path}")
        checksums: Dict[str, str] = {}
        if sums_path.exists():
            try:
                for line in sums_path.read_text().splitlines():
                    fields = line.split()
                    if (
                        len(fields) != 2
                        or len(fields[0]) != 64
                        or any(
                            character not in "0123456789abcdefABCDEF"
                            for character in fields[0]
                        )
                    ):
                        raise ValueError("invalid checksum line")
                    checksums[fields[1].lstrip("*")] = fields[0].lower()
            except (OSError, ValueError) as error:
                raise FirmwareError(f"invalid SHA256SUMS file: {sums_path}") from error
        for app_id, app in catalog["apps"].items():
            image = sd_path(sd_root, app["sd_path"])
            entry = lock_apps.get(app_id, {})
            recorded_digest = checksums.get(app["sd_path"]) or checksums.get(
                Path(app["sd_path"]).name
            )
            if not image.exists():
                if app_id in lock_apps or recorded_digest:
                    raise FirmwareError(f"{app_id}: staged image is missing")
                print(f"{app_id}: not staged")
                continue
            validate_image(
                app_id,
                image,
                app["partition_size"],
                app["project_name"],
            )
            actual_digest = sha256(image)
            locked_digest = entry.get("sha256") if isinstance(entry, dict) else None
            if locked_digest is not None and (
                not isinstance(locked_digest, str)
                or len(locked_digest) != 64
                or any(
                    character not in "0123456789abcdefABCDEF"
                    for character in locked_digest
                )
            ):
                raise FirmwareError(f"{app_id}: invalid SHA-256 in firmware lock")
            if (
                isinstance(entry, dict)
                and entry.get("file", app["sd_path"]) != app["sd_path"]
            ):
                raise FirmwareError(f"{app_id}: firmware lock path does not match catalog")
            expected_digests = [
                digest for digest in (locked_digest, recorded_digest) if digest
            ]
            if not expected_digests:
                raise FirmwareError(f"{app_id}: no staged SHA-256 metadata")
            if any(digest.lower() != actual_digest for digest in expected_digests):
                raise FirmwareError(f"{app_id}: staged image SHA-256 mismatch")
            print(f"{app_id}: ok ({image.stat().st_size} bytes)")


def run(arguments: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(arguments)
    catalog = load_catalog(args.catalog)
    if args.command == "list":
        for app_id, app in catalog["apps"].items():
            print(f"{app_id:8} {app['name']}  {app['repository']}")
        return 0
    if args.command == "doctor":
        _doctor(catalog, args.sd)
        return 0

    selected = resolve_apps(catalog, args.app)
    if args.command == "local":
        workspace = args.workspace.expanduser().resolve()
        images: Dict[str, Path] = {}
        sources: Dict[str, Dict[str, str]] = {}
        for app_id in selected:
            app = catalog["apps"][app_id]
            repository = workspace / app["local_repository"]
            if not repository.is_dir():
                raise FirmwareError(
                    f"{app_id}: local repository not found: {repository}"
                )
            if args.build:
                _build_application(app_id, app, repository)
            images[app_id] = repository / app["local_image"]
            sources[app_id] = {"source": "local", "repository": str(repository)}
        stage_images(catalog, images, args.sd, sources)
        _print_staged(selected)
        return 0

    if args.command == "release":
        tags = _tag_map(args.tag, selected)
        client = GitHubReleaseClient()
        with tempfile.TemporaryDirectory(
            prefix="cardputer-firmware-"
        ) as temporary_directory:
            images: Dict[str, Path] = {}
            sources = {}
            for app_id in selected:
                app = catalog["apps"][app_id]
                image, tag = client.download(
                    app_id, app, tags.get(app_id), Path(temporary_directory)
                )
                images[app_id] = image
                sources[app_id] = {
                    "source": "github-release",
                    "repository": app["repository"],
                    "tag": tag,
                }
                print(f"downloaded {app_id} {tag}")
            stage_images(catalog, images, args.sd, sources)
        _print_staged(selected)
        return 0
    raise FirmwareError(f"unsupported command: {args.command}")


def main() -> None:
    try:
        raise SystemExit(run())
    except FirmwareError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
    except subprocess.CalledProcessError as error:
        print(
            f"error: build failed with exit status {error.returncode}", file=sys.stderr
        )
        raise SystemExit(error.returncode or 1)
