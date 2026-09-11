from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple


ESP_IMAGE_MAGIC = b"\xe9"
ESP_APP_DESCRIPTOR_OFFSET = 0x20
ESP_APP_DESCRIPTOR_MAGIC = b"\x32\x54\xcd\xab"
ESP_APP_PROJECT_NAME_OFFSET = ESP_APP_DESCRIPTOR_OFFSET + 48
ESP_APP_PROJECT_NAME_SIZE = 32


class FirmwareError(RuntimeError):
    """A user-actionable catalog, download, validation, or staging failure."""


def load_catalog(path: Path) -> Dict[str, Any]:
    try:
        catalog = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise FirmwareError(f"cannot read catalog {path}: {error}") from error
    if catalog.get("schema_version") != 1 or not isinstance(catalog.get("apps"), dict):
        raise FirmwareError("unsupported or invalid firmware catalog")
    contract = catalog.get("partition_contract")
    if not isinstance(contract, list) or not contract:
        raise FirmwareError("firmware catalog has no partition contract")
    partition_fields = {"name", "type", "subtype", "offset", "size"}
    for partition in contract:
        if not isinstance(partition, dict) or not partition_fields.issubset(partition):
            raise FirmwareError("firmware catalog has an invalid partition contract")
    for app_id, app in catalog["apps"].items():
        required = {
            "repository",
            "local_repository",
            "build_command",
            "local_image",
            "release_asset",
            "partition",
            "partition_size",
            "project_name",
            "sd_path",
            "aliases",
        }
        missing = required.difference(app)
        if missing:
            raise FirmwareError(f"{app_id}: missing catalog fields: {', '.join(sorted(missing))}")
        build_environment = app.get("build_environment", {"unset": []})
        unset = (
            build_environment.get("unset")
            if isinstance(build_environment, dict)
            else None
        )
        if (
            not isinstance(unset, list)
            or any(not isinstance(variable, str) or not variable for variable in unset)
        ):
            raise FirmwareError(f"{app_id}: invalid build environment")
        try:
            project_name = app["project_name"].encode("ascii")
        except (AttributeError, UnicodeEncodeError) as error:
            raise FirmwareError(f"{app_id}: invalid ESP project name") from error
        if not project_name or len(project_name) > ESP_APP_PROJECT_NAME_SIZE:
            raise FirmwareError(f"{app_id}: invalid ESP project name")
    return catalog


def resolve_apps(catalog: Mapping[str, Any], requested: Iterable[str]) -> List[str]:
    selection = list(requested)
    if not selection:
        selection = ["all"]
    if "all" in selection:
        if len(selection) != 1:
            raise FirmwareError("cannot combine 'all' with individual applications")
        return list(catalog["apps"])
    unknown = [app_id for app_id in selection if app_id not in catalog["apps"]]
    if unknown:
        raise FirmwareError(f"unknown application: {unknown[0]}")
    result: List[str] = []
    for app_id in selection:
        if app_id not in result:
            result.append(app_id)
    return result


def parse_size(value: str) -> int:
    normalized = value.strip().upper()
    multiplier = 1
    if normalized.endswith("K"):
        normalized, multiplier = normalized[:-1], 1024
    elif normalized.endswith("M"):
        normalized, multiplier = normalized[:-1], 1024 * 1024
    return int(normalized, 0) * multiplier


def validate_layout(path: Path, flash_size: int) -> List[Dict[str, Any]]:
    try:
        with path.open(newline="") as layout_file:
            rows = csv.reader(
                line for line in layout_file if not line.lstrip().startswith("#")
            )
            partitions = [
                {
                    "name": row[0].strip(),
                    "type": row[1].strip(),
                    "subtype": row[2].strip(),
                    "offset": parse_size(row[3]),
                    "size": parse_size(row[4]),
                }
                for row in rows
                if row
            ]
    except (OSError, ValueError, IndexError) as error:
        raise FirmwareError(f"invalid partition layout {path}: {error}") from error
    if not partitions:
        raise FirmwareError("partition layout is empty")
    names = [partition["name"] for partition in partitions]
    if len(names) != len(set(names)):
        raise FirmwareError("partition layout contains duplicate labels")
    previous_end = 0
    for partition in sorted(partitions, key=lambda item: item["offset"]):
        if partition["offset"] < previous_end:
            raise FirmwareError(f"partition {partition['name']} overlaps its predecessor")
        previous_end = partition["offset"] + partition["size"]
        if previous_end > flash_size:
            raise FirmwareError(f"partition {partition['name']} exceeds flash size")
    return partitions


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_sd_root(sd_root: Path, require_mount: bool = True) -> Path:
    resolved = sd_root.expanduser().resolve()
    if resolved in {Path("/"), Path.home().resolve()} or not resolved.is_dir():
        raise FirmwareError(f"SD root must be an existing mounted directory: {resolved}")
    if require_mount and not resolved.is_mount():
        raise FirmwareError(f"SD root is not a mounted filesystem root: {resolved}")
    return resolved


def sd_path(sd_root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise FirmwareError(f"path points outside the SD root: {relative_path}")
    destination = (sd_root / relative).resolve()
    try:
        destination.relative_to(sd_root)
    except ValueError as error:
        raise FirmwareError(f"path points outside the SD root: {relative_path}") from error
    return destination


def validate_image(
    app_id: str,
    image: Path,
    partition_size: int,
    expected_project_name: str,
) -> None:
    if not image.is_file():
        raise FirmwareError(f"{app_id}: image does not exist: {image}")
    with image.open("rb") as image_file:
        magic = image_file.read(1)
        image_file.seek(ESP_APP_DESCRIPTOR_OFFSET)
        descriptor_magic = image_file.read(len(ESP_APP_DESCRIPTOR_MAGIC))
        image_file.seek(ESP_APP_PROJECT_NAME_OFFSET)
        project_name = image_file.read(ESP_APP_PROJECT_NAME_SIZE).split(b"\0", 1)[0]
    if magic != ESP_IMAGE_MAGIC:
        raise FirmwareError(f"{app_id}: image is not a raw ESP application image")
    if descriptor_magic != ESP_APP_DESCRIPTOR_MAGIC:
        raise FirmwareError(f"{app_id}: image has no ESP application descriptor")
    if project_name != expected_project_name.encode("ascii"):
        actual_project_name = project_name.decode("ascii", errors="replace")
        raise FirmwareError(
            f"{app_id}: image project is {actual_project_name!r}; "
            f"expected {expected_project_name!r}"
        )
    image_size = image.stat().st_size
    if image_size > partition_size:
        raise FirmwareError(
            f"{app_id}: image is {image_size} bytes; partition limit is {partition_size}"
        )


def _read_aliases(path: Path) -> List[Tuple[str, str]]:
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    if any(not line.strip() for line in lines) or len(lines) % 2:
        raise FirmwareError(f"invalid CRUB aliases file: {path}")
    return [
        (lines[index].strip(), lines[index + 1].strip())
        for index in range(0, len(lines), 2)
    ]


def _managed_aliases(catalog: Mapping[str, Any]) -> List[Tuple[str, str]]:
    aliases: List[Tuple[str, str]] = []
    for app in catalog["apps"].values():
        aliases.extend((name, command) for name, command in app["aliases"].items())
    aliases.sort(key=lambda pair: pair[0].startswith("up"))
    for name, command in aliases:
        if not name or len(name) > 15 or not command or len(command) > 63:
            raise FirmwareError(f"CRUB alias exceeds loader limits: {name}")
    return aliases


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_copy(source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=str(destination.parent)
    )
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary_name)
        with open(temporary_name, "rb") as temporary_file:
            os.fsync(temporary_file.fileno())
        if sha256(Path(temporary_name)) != expected_sha256:
            raise FirmwareError(f"copy verification failed for {destination.name}")
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def stage_images(
    catalog: Mapping[str, Any],
    images: Mapping[str, Path],
    sd_root: Path,
    sources: Optional[Mapping[str, Mapping[str, str]]] = None,
    *,
    require_mount: bool = True,
) -> List[Path]:
    sd_root = resolve_sd_root(sd_root, require_mount=require_mount)
    if not images:
        raise FirmwareError("no application images selected")
    source_digests: Dict[str, str] = {}
    for app_id, image in images.items():
        if app_id not in catalog["apps"]:
            raise FirmwareError(f"unknown application: {app_id}")
        app = catalog["apps"][app_id]
        validate_image(
            app_id,
            image,
            app["partition_size"],
            app["project_name"],
        )
        source_digests[app_id] = sha256(image)

    destinations = {
        app_id: sd_path(sd_root, app["sd_path"])
        for app_id, app in catalog["apps"].items()
    }

    aliases_path = sd_path(sd_root, ".crub/aliases")
    managed = _managed_aliases(catalog)
    managed_names = {name for name, _command in managed}
    preserved = [
        pair for pair in _read_aliases(aliases_path) if pair[0] not in managed_names
    ]
    merged_aliases = preserved + managed

    lock_path = sd_path(sd_root, "firmware/firmware-manager-lock.json")
    checksums_path = sd_path(sd_root, "firmware/SHA256SUMS")
    try:
        lock = (
            json.loads(lock_path.read_text())
            if lock_path.exists()
            else {"schema_version": 1, "apps": {}}
        )
    except json.JSONDecodeError as error:
        raise FirmwareError(f"invalid firmware lock file: {lock_path}") from error
    if not isinstance(lock, dict) or not isinstance(lock.get("apps", {}), dict):
        raise FirmwareError(f"invalid firmware lock file: {lock_path}")
    lock.setdefault("schema_version", 1)
    lock.setdefault("apps", {})

    staged: List[Path] = []
    for app_id, source in images.items():
        destination = destinations[app_id]
        _atomic_copy(source, destination, source_digests[app_id])
        staged.append(destination)

    aliases_data = "".join(f"{name}\n{command}\n" for name, command in merged_aliases)
    _atomic_write(aliases_path, aliases_data.encode())

    checksum_lines: List[str] = []
    for app_id, app in catalog["apps"].items():
        destination = destinations[app_id]
        if destination.is_file():
            digest = sha256(destination)
            checksum_lines.append(f"{digest}  {app['sd_path']}\n")
            if app_id in images:
                entry = {
                    "sha256": digest,
                    "file": app["sd_path"],
                    "size": destination.stat().st_size,
                }
                if sources and app_id in sources:
                    entry.update(sources[app_id])
                lock["apps"][app_id] = entry
    _atomic_write(
        checksums_path, "".join(checksum_lines).encode()
    )
    _atomic_write(lock_path, (json.dumps(lock, indent=2, sort_keys=True) + "\n").encode())
    return staged


Fetch = Callable[[str, Mapping[str, str]], bytes]


def _url_fetch(url: str, headers: Mapping[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except (urllib.error.HTTPError, urllib.error.URLError) as error:
        raise FirmwareError(f"GitHub request failed for {url}: {error}") from error


class GitHubReleaseClient:
    def __init__(self, fetch: Fetch = _url_fetch) -> None:
        self._fetch = fetch

    @staticmethod
    def _headers() -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "cardputer-firmware-manager",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def download(
        self,
        app_id: str,
        app: Mapping[str, Any],
        tag: Optional[str],
        destination: Path,
    ) -> Tuple[Path, str]:
        repository = app["repository"]
        if tag:
            endpoint = (
                f"https://api.github.com/repos/{repository}/releases/tags/"
                f"{urllib.parse.quote(tag, safe='')}"
            )
        else:
            endpoint = f"https://api.github.com/repos/{repository}/releases/latest"
        try:
            release = json.loads(self._fetch(endpoint, self._headers()))
        except (json.JSONDecodeError, KeyError) as error:
            raise FirmwareError(f"{app_id}: invalid GitHub release response") from error
        pattern = re.compile(app["release_asset"])
        matches = [
            asset
            for asset in release.get("assets", [])
            if pattern.fullmatch(asset.get("name", ""))
        ]
        if len(matches) != 1:
            if not matches:
                release_name = release.get("tag_name", tag or "latest")
                raise FirmwareError(
                    f"{app_id}: no matching firmware asset in release {release_name}"
                )
            raise FirmwareError(f"{app_id}: multiple matching firmware assets in release")
        asset = matches[0]
        data = self._fetch(asset["browser_download_url"], self._headers())
        actual_digest = hashlib.sha256(data).hexdigest()
        expected_digest = asset.get("digest")
        if expected_digest and expected_digest != f"sha256:{actual_digest}":
            raise FirmwareError(f"{app_id}: GitHub asset SHA-256 mismatch")
        checksum_verified = False
        if not expected_digest and app.get("checksum_asset"):
            checksum = next(
                (
                    item
                    for item in release.get("assets", [])
                    if item.get("name") == app["checksum_asset"]
                ),
                None,
            )
            if checksum:
                checksum_data = self._fetch(
                    checksum["browser_download_url"], self._headers()
                ).decode()
                expected = next(
                    (
                        line.split()[0]
                        for line in checksum_data.splitlines()
                        if line.split()
                        and line.split()[-1].lstrip("*") == asset["name"]
                    ),
                    None,
                )
                if expected:
                    if expected.lower() != actual_digest:
                        raise FirmwareError(f"{app_id}: release checksum mismatch")
                    checksum_verified = True
        if not expected_digest and not checksum_verified:
            raise FirmwareError(f"{app_id}: release has no verifiable SHA-256")
        release_tag = str(release.get("tag_name") or tag or "unknown")
        safe_tag = re.sub(r"[^A-Za-z0-9._-]", "_", release_tag)
        output = destination / app_id / safe_tag / asset["name"]
        _atomic_write(output, data)
        return output, release_tag
