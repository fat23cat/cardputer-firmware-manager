from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
from copy import deepcopy
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from firmware_manager.cli import _build_application, _doctor, _print_staged, run
from firmware_manager.core import (
    FirmwareError,
    GitHubReleaseClient,
    load_catalog,
    resolve_apps,
    stage_images,
    validate_image,
    validate_layout,
)


ROOT = Path(__file__).resolve().parents[1]


def fake_app(
    payload: bytes = b"payload", project_name: str = "cardputer_hub"
) -> bytes:
    image = bytearray(0x70)
    image[0] = 0xE9
    image[0x20:0x24] = b"\x32\x54\xcd\xab"
    encoded_project_name = project_name.encode("ascii")
    image[0x50 : 0x50 + len(encoded_project_name)] = encoded_project_name
    return bytes(image) + payload


def segmented_app(
    payload: bytes = b"payload", project_name: str = "arduino-lib-builder"
) -> bytes:
    descriptor = bytearray(0x100)
    descriptor[0:4] = b"\x32\x54\xcd\xab"
    encoded_project_name = project_name.encode("ascii")
    descriptor[0x30 : 0x30 + len(encoded_project_name)] = encoded_project_name
    segment = bytes(descriptor) + payload
    segment += b"\0" * (-len(segment) % 4)
    header = bytearray(24)
    header[0] = 0xE9
    header[1] = 1
    header[23] = 1
    image = bytes(header) + struct.pack("<II", 0x3C000020, len(segment)) + segment
    image += b"\0" * (15 - len(image) % 16) + b"\xa5"
    return image + hashlib.sha256(image).digest()


def merged_image(app: bytes) -> bytes:
    image = bytearray(b"\xff" * 0x10000)
    image[0] = 0xE9
    entries = [
        (1, 0x02, 0x9000, 0x6000, b"nvs"),
        (0, 0x00, 0x10000, 0x4E0000, b"factory"),
        (1, 0x82, 0x4F0000, 0x300000, b"spiffs"),
    ]
    for index, (kind, subtype, offset, size, label) in enumerate(entries):
        start = 0x8000 + index * 32
        image[start : start + 32] = struct.pack(
            "<BBBBII16sI", 0xAA, 0x50, kind, subtype, offset, size, label, 0
        )
    return bytes(image) + app


class CatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT / "firmware-manager.json")

    def test_resolves_all_or_one_application_in_catalog_order(self) -> None:
        self.assertEqual(
            resolve_apps(self.catalog, ["all"]), ["hub", "codex", "bruce"]
        )
        self.assertEqual(resolve_apps(self.catalog, ["codex"]), ["codex"])

    def test_rejects_unknown_or_mixed_all_selection(self) -> None:
        with self.assertRaisesRegex(FirmwareError, "unknown application"):
            resolve_apps(self.catalog, ["missing"])
        with self.assertRaisesRegex(FirmwareError, "cannot combine"):
            resolve_apps(self.catalog, ["all", "hub"])

    def test_shared_layout_matches_catalog_and_fits_eight_megabytes(self) -> None:
        partitions = validate_layout(ROOT / self.catalog["layout"], 0x800000)
        by_name = {partition["name"]: partition for partition in partitions}

        self.assertEqual(by_name["test"]["offset"], 0x10000)
        self.assertEqual(by_name["test"]["size"], 0xC0000)
        for app_id, app in self.catalog["apps"].items():
            partition = by_name[app["partition"]]
            self.assertEqual(partition["size"], app["partition_size"])
            self.assertEqual(partition["type"], "app")

    def test_hub_keeps_two_megabytes_and_other_firmware_shares_extra(self) -> None:
        partitions = validate_layout(ROOT / self.catalog["layout"], 0x800000)
        by_name = {
            partition["name"]: (partition["offset"], partition["size"])
            for partition in partitions
        }

        self.assertEqual(by_name["hub"], (0xD0000, 0x200000))
        self.assertEqual(by_name["extra"], (0x2D0000, 0x4C0000))
        self.assertEqual(by_name["apps_nvs"], (0x790000, 0x10000))
        self.assertEqual(by_name["hub_config"], (0x7A0000, 0x10000))
        self.assertEqual(by_name["spiffs"], (0x7B0000, 0x20000))
        self.assertNotIn("codex", by_name)
        self.assertNotIn("vfs", by_name)
        self.assertEqual(self.catalog["apps"]["codex"]["partition"], "extra")
        self.assertEqual(self.catalog["apps"]["bruce"]["partition"], "extra")


class LocalBuildTest(unittest.TestCase):
    def test_isolates_each_build_from_another_projects_esp_idf_environment(
        self,
    ) -> None:
        app = {
            "build_command": ["./tools/build.sh"],
            "build_environment": {
                "unset": ["IDF_PATH", "IDF_TOOLS_PATH", "OPENOCD_SCRIPTS"]
            },
        }
        inherited = {
            "IDF_PATH": "/foreign/esp-idf",
            "IDF_TOOLS_PATH": "/foreign/idf-tools",
            "OPENOCD_SCRIPTS": "/foreign/openocd",
            "CARDPUTER_HUB_IDF_PATH": "/wanted/esp-idf",
        }

        with mock.patch.dict(os.environ, inherited, clear=True):
            with mock.patch("firmware_manager.cli.subprocess.run") as run_build:
                _build_application("codex", app, Path("/workspace/codex"))

        environment = run_build.call_args.kwargs["env"]
        self.assertNotIn("IDF_PATH", environment)
        self.assertNotIn("IDF_TOOLS_PATH", environment)
        self.assertNotIn("OPENOCD_SCRIPTS", environment)
        self.assertEqual(
            environment["CARDPUTER_HUB_IDF_PATH"], "/wanted/esp-idf"
        )
        run_build.assert_called_once_with(
            ["./tools/build.sh"],
            cwd=Path("/workspace/codex"),
            check=True,
            env=environment,
        )

    def test_local_all_stages_only_applications_with_a_local_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            (workspace / "cardputer-hub" / "build").mkdir(parents=True)
            (workspace / "codex-microputer-adv" / "dist").mkdir(parents=True)

            with mock.patch("firmware_manager.cli.stage_images") as stage:
                with redirect_stdout(io.StringIO()):
                    run(
                        [
                            "local",
                            "--workspace",
                            str(workspace),
                            "--sd",
                            str(workspace),
                        ]
                    )

            self.assertEqual(list(stage.call_args.args[1]), ["hub", "codex"])

    def test_local_rejects_release_only_application(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch("firmware_manager.cli.stage_images") as stage:
                with self.assertRaisesRegex(FirmwareError, "bruce: no local build"):
                    run(
                        [
                            "local",
                            "--app",
                            "bruce",
                            "--workspace",
                            temporary_directory,
                            "--sd",
                            temporary_directory,
                        ]
                    )

            stage.assert_not_called()


class StagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT / "firmware-manager.json")

    def test_stages_selected_image_and_preserves_other_firmware(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            sd = temporary / "card"
            sd.mkdir()
            existing_codex = sd / "firmware" / "Codex.bin"
            existing_codex.parent.mkdir(parents=True)
            existing_codex.write_bytes(
                fake_app(b"old codex", project_name="codex_microputer_adv")
            )
            hub = temporary / "hub.bin"
            hub.write_bytes(fake_app(b"new hub"))

            staged = stage_images(
                self.catalog, {"hub": hub}, sd, require_mount=False
            )

            self.assertEqual(
                staged, [sd.resolve() / "firmware" / "cardputer-hub.bin"]
            )
            self.assertEqual(
                existing_codex.read_bytes(),
                fake_app(b"old codex", project_name="codex_microputer_adv"),
            )
            sums = (sd / "firmware" / "SHA256SUMS").read_text()
            self.assertIn("cardputer-hub.bin", sums)
            self.assertIn("Codex.bin", sums)

    def test_merges_managed_aliases_without_destroying_user_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            sd = temporary / "card"
            sd.mkdir()
            aliases = sd / ".crub" / "aliases"
            aliases.parent.mkdir(parents=True)
            aliases.write_text(
                "music\nbeep 440 100\n"
                "hub\nold command\n"
                "codex\nlaunch -f codex\n"
                "codexfast\necho launch -f codex > /.crub/boot\n"
            )
            hub = temporary / "hub.bin"
            hub.write_bytes(fake_app())

            stage_images(self.catalog, {"hub": hub}, sd, require_mount=False)

            self.assertEqual(
                aliases.read_text().splitlines(),
                [
                    "music",
                    "beep 440 100",
                    "hub",
                    "launch -f hub",
                    "hubfast",
                    "echo launch -f > /.crub/boot",
                    "crubmenu",
                    "echo boots 1500 > /.crub/boot && echo fetch >> /.crub/boot",
                    "extra",
                    "launch -f extra",
                    "extrafast",
                    "echo launch -f extra > /.crub/boot",
                    "uphub",
                    "flash /firmware/cardputer-hub.bin hub",
                    "upcodex",
                    "flash /firmware/Codex.bin extra",
                    "upbruce",
                    "flash /firmware/Bruce.bin extra",
                ],
            )

    def test_keeps_user_alias_that_reuses_a_retired_managed_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            sd = temporary / "card"
            sd.mkdir()
            aliases = sd / ".crub" / "aliases"
            aliases.parent.mkdir(parents=True)
            aliases.write_text("codex\necho my codex\n")
            hub = temporary / "hub.bin"
            hub.write_bytes(fake_app())

            stage_images(self.catalog, {"hub": hub}, sd, require_mount=False)

            self.assertEqual(
                aliases.read_text().splitlines()[:2], ["codex", "echo my codex"]
            )

    def test_rejects_invalid_and_oversized_images_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            invalid = temporary / "invalid.bin"
            invalid.write_bytes(b"not an app")
            (temporary / "card").mkdir()
            with self.assertRaisesRegex(FirmwareError, "raw ESP application"):
                stage_images(
                    self.catalog,
                    {"hub": invalid},
                    temporary / "card",
                    require_mount=False,
                )

            oversized = temporary / "oversized.bin"
            oversized.write_bytes(fake_app(project_name="codex_microputer_adv"))
            with oversized.open("r+b") as image:
                image.truncate(self.catalog["apps"]["codex"]["partition_size"] + 1)
            with self.assertRaisesRegex(FirmwareError, "partition limit"):
                stage_images(
                    self.catalog,
                    {"codex": oversized},
                    temporary / "card",
                    require_mount=False,
                )

    def test_rejects_image_for_another_managed_application_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            sd = temporary / "card"
            sd.mkdir()
            wrong_image = temporary / "codex-as-hub.bin"
            wrong_image.write_bytes(
                fake_app(project_name="codex_microputer_adv")
            )

            with self.assertRaisesRegex(FirmwareError, "project"):
                stage_images(
                    self.catalog,
                    {"hub": wrong_image},
                    sd,
                    require_mount=False,
                )

            self.assertFalse(
                (sd / self.catalog["apps"]["hub"]["sd_path"]).exists()
            )

    def test_rejects_catalog_path_that_escapes_the_sd_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            sd = temporary / "card"
            sd.mkdir()
            hub = temporary / "hub.bin"
            hub.write_bytes(fake_app())
            escaped = temporary / "outside.bin"
            catalog = deepcopy(self.catalog)
            catalog["apps"]["codex"]["sd_path"] = "../outside.bin"

            with self.assertRaisesRegex(FirmwareError, "outside the SD root"):
                stage_images(catalog, {"hub": hub}, sd, require_mount=False)

            self.assertFalse(escaped.exists())
            self.assertFalse(
                (sd / self.catalog["apps"]["hub"]["sd_path"]).exists()
            )

    def test_rejects_an_existing_directory_that_is_not_a_mount_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            sd = temporary / "ordinary-directory"
            sd.mkdir()
            hub = temporary / "hub.bin"
            hub.write_bytes(fake_app())

            with self.assertRaisesRegex(FirmwareError, "not a mounted filesystem"):
                stage_images(self.catalog, {"hub": hub}, sd)

    def test_rejects_copy_corruption_before_replacing_staged_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            sd = temporary / "card"
            sd.mkdir()
            destination = sd / "firmware" / "cardputer-hub.bin"
            destination.parent.mkdir(parents=True)
            original = fake_app(b"previous")
            destination.write_bytes(original)
            hub = temporary / "hub.bin"
            hub.write_bytes(fake_app(b"new"))

            def corrupt_copy(_source: Path, target: Path) -> None:
                Path(target).write_bytes(fake_app(b"corrupted"))

            with mock.patch("firmware_manager.core.shutil.copyfile", corrupt_copy):
                with self.assertRaisesRegex(FirmwareError, "copy verification failed"):
                    stage_images(
                        self.catalog, {"hub": hub}, sd, require_mount=False
                    )

            self.assertEqual(destination.read_bytes(), original)


class DoctorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT / "firmware-manager.json")

    def test_rejects_layout_missing_a_required_partition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            layout = Path(temporary_directory) / "incomplete.csv"
            layout.write_text(
                "# Name, Type, SubType, Offset, Size, Flags\n"
                "nvs,data,nvs,0x9000,0x5000,\n"
                "otadata,data,ota,0xe000,0x2000,\n"
                "hub,app,ota_0,0xd0000,0x200000,\n"
                "extra,app,ota_1,0x2d0000,0x4c0000,\n"
            )
            catalog = deepcopy(self.catalog)
            catalog["layout"] = str(layout)

            with self.assertRaisesRegex(FirmwareError, "required partition test"):
                _doctor(catalog, None)

    def test_rejects_staged_image_whose_digest_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            sd = temporary / "card"
            sd.mkdir()
            hub = temporary / "hub.bin"
            hub.write_bytes(fake_app(b"original"))
            stage_images(self.catalog, {"hub": hub}, sd, require_mount=False)
            staged = sd / self.catalog["apps"]["hub"]["sd_path"]
            damaged = bytearray(staged.read_bytes())
            damaged[-1] ^= 0xFF
            staged.write_bytes(damaged)

            with self.assertRaisesRegex(FirmwareError, "SHA-256 mismatch"):
                _doctor(self.catalog, sd, require_mount=False)

    def test_rejects_lock_recorded_image_that_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            sd = temporary / "card"
            sd.mkdir()
            hub = temporary / "hub.bin"
            hub.write_bytes(fake_app(b"original"))
            stage_images(self.catalog, {"hub": hub}, sd, require_mount=False)
            staged = sd / self.catalog["apps"]["hub"]["sd_path"]
            staged.unlink()

            with self.assertRaisesRegex(FirmwareError, "staged image is missing"):
                _doctor(self.catalog, sd, require_mount=False)

    def test_post_stage_instructions_remount_sd_before_using_aliases(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            _print_staged(self.catalog, ["hub", "codex"])

        lines = output.getvalue().splitlines()
        self.assertLess(
            lines.index("remount the card in CRUB with 'sd', then run:"),
            lines.index("  uphub"),
        )

    def test_post_stage_instructions_offer_one_image_for_the_shared_slot(
        self,
    ) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            _print_staged(self.catalog, ["hub", "codex", "bruce"])

        lines = output.getvalue().splitlines()
        self.assertEqual(
            lines[-2:],
            [
                "  uphub",
                "  upcodex or upbruce (shared partition extra; flash only one)",
            ],
        )


class ReleaseClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT / "firmware-manager.json")

    def test_downloads_matching_application_asset_and_checks_github_digest(self) -> None:
        image = fake_app(b"release")
        release = {
            "tag_name": "v1.2.3",
            "assets": [
                {
                    "name": "cardputer-hub-v1.2.3-partitions.bin",
                    "browser_download_url": "https://example.invalid/partitions",
                },
                {
                    "name": "cardputer-hub-v1.2.3.bin",
                    "browser_download_url": "https://example.invalid/app",
                    "digest": f"sha256:{hashlib.sha256(image).hexdigest()}",
                },
            ],
        }
        responses = {
            "https://api.github.com/repos/fat23cat/cardputer-hub/releases/latest": json.dumps(
                release
            ).encode(),
            "https://example.invalid/app": image,
        }
        client = GitHubReleaseClient(fetch=lambda url, _headers: responses[url])

        with tempfile.TemporaryDirectory() as temporary_directory:
            downloaded, tag = client.download(
                "hub", self.catalog["apps"]["hub"], None, Path(temporary_directory)
            )

            self.assertEqual(tag, "v1.2.3")
            self.assertEqual(downloaded.read_bytes(), image)

    def test_fails_when_release_does_not_have_the_application_asset(self) -> None:
        release = {"tag_name": "v1", "assets": []}
        client = GitHubReleaseClient(
            fetch=lambda _url, _headers: json.dumps(release).encode()
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(FirmwareError, "no matching firmware asset"):
                client.download(
                    "codex",
                    self.catalog["apps"]["codex"],
                    None,
                    Path(temporary_directory),
                )

    def test_rejects_release_without_verifiable_checksum(self) -> None:
        image = fake_app(b"unchecked")
        release = {
            "tag_name": "v1",
            "assets": [
                {
                    "name": "Codex.bin",
                    "browser_download_url": "https://example.invalid/app",
                }
            ],
        }
        responses = {
            "https://api.github.com/repos/fat23cat/codex-microputer-adv/releases/latest": json.dumps(
                release
            ).encode(),
            "https://example.invalid/app": image,
        }
        client = GitHubReleaseClient(fetch=lambda url, _headers: responses[url])
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(FirmwareError, "no verifiable SHA-256"):
                client.download(
                    "codex",
                    self.catalog["apps"]["codex"],
                    None,
                    Path(temporary_directory),
                )

    def _bruce_client(
        self, asset: bytes, tag: str = "1.16.1"
    ) -> GitHubReleaseClient:
        release = {
            "tag_name": tag,
            "assets": [
                {
                    "name": "Bruce-m5stack-cardputer.bin",
                    "browser_download_url": "https://example.invalid/bruce",
                    "digest": f"sha256:{hashlib.sha256(asset).hexdigest()}",
                }
            ],
        }
        responses = {
            f"https://api.github.com/repos/pr3y/Bruce/releases/tags/{tag}": json.dumps(
                release
            ).encode(),
            "https://example.invalid/bruce": asset,
        }
        return GitHubReleaseClient(fetch=lambda url, _headers: responses[url])

    def _pinned_bruce(self, asset: bytes) -> dict:
        bruce = deepcopy(self.catalog["apps"]["bruce"])
        bruce["release_pin"]["sha256"] = hashlib.sha256(asset).hexdigest()
        return bruce

    def test_catalog_pins_bruce_to_a_reviewed_release(self) -> None:
        self.assertEqual(
            self.catalog["apps"]["bruce"]["release_pin"],
            {
                "tag": "1.16.1",
                "sha256": "e0a06966e601fecdb78470168b423690392f5ca14502a703588d4d14d0722033",
            },
        )
        self.assertNotIn("release_pin", self.catalog["apps"]["hub"])
        self.assertNotIn("release_pin", self.catalog["apps"]["codex"])

    def test_rejects_invalid_release_pin(self) -> None:
        catalog = json.loads((ROOT / "firmware-manager.json").read_text())
        catalog["apps"]["bruce"]["release_pin"] = {"tag": "1.16.1", "sha256": "abc"}
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "catalog.json"
            path.write_text(json.dumps(catalog))
            with self.assertRaisesRegex(FirmwareError, "bruce: invalid release pin"):
                load_catalog(path)

    def test_downloads_pinned_tag_when_no_tag_is_requested(self) -> None:
        asset = merged_image(segmented_app(b"pinned"))
        client = self._bruce_client(asset)

        with tempfile.TemporaryDirectory() as temporary_directory:
            _downloaded, tag = client.download(
                "bruce", self._pinned_bruce(asset), None, Path(temporary_directory)
            )

        self.assertEqual(tag, "1.16.1")

    def test_rejects_pinned_release_whose_asset_changed(self) -> None:
        pinned = merged_image(segmented_app(b"reviewed"))
        replaced = merged_image(segmented_app(b"replaced"))
        bruce = self._pinned_bruce(pinned)
        for tag in (None, "1.16.1"):
            with self.subTest(tag=tag):
                client = self._bruce_client(replaced)
                with tempfile.TemporaryDirectory() as temporary_directory:
                    with self.assertRaisesRegex(
                        FirmwareError, "bruce: .*pinned SHA-256"
                    ):
                        client.download(
                            "bruce", bruce, tag, Path(temporary_directory)
                        )

    def test_explicit_other_tag_is_verified_by_github_digest_only(self) -> None:
        asset = merged_image(segmented_app(b"newer"))
        client = self._bruce_client(asset, tag="1.17")

        with tempfile.TemporaryDirectory() as temporary_directory:
            _downloaded, tag = client.download(
                "bruce",
                self.catalog["apps"]["bruce"],
                "1.17",
                Path(temporary_directory),
            )

        self.assertEqual(tag, "1.17")

    def test_extracts_raw_application_from_merged_release_image(self) -> None:
        app = segmented_app(b"bruce release")
        asset = merged_image(app) + b"\xff" * 64
        client = self._bruce_client(asset)
        bruce = self._pinned_bruce(asset)

        with tempfile.TemporaryDirectory() as temporary_directory:
            downloaded, tag = client.download(
                "bruce", bruce, None, Path(temporary_directory)
            )

            self.assertEqual(tag, "1.16.1")
            self.assertEqual(downloaded.read_bytes(), app)
            validate_image(
                "bruce", downloaded, bruce["partition_size"], bruce["project_name"]
            )

    def test_rejects_merged_release_without_a_complete_application(self) -> None:
        app = segmented_app(b"bruce release")
        cases = {
            "no partition table": app,
            "truncated": merged_image(app)[:-40],
        }
        for expected, asset in cases.items():
            with self.subTest(expected):
                client = self._bruce_client(asset)
                with tempfile.TemporaryDirectory() as temporary_directory:
                    with self.assertRaisesRegex(FirmwareError, f"bruce: .*{expected}"):
                        client.download(
                            "bruce",
                            self._pinned_bruce(asset),
                            None,
                            Path(temporary_directory),
                        )


if __name__ == "__main__":
    unittest.main()
