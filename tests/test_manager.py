from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from copy import deepcopy
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from firmware_manager.cli import _build_application, _doctor, _print_staged
from firmware_manager.core import (
    FirmwareError,
    GitHubReleaseClient,
    load_catalog,
    resolve_apps,
    stage_images,
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


class CatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT / "firmware-manager.json")

    def test_resolves_all_or_one_application_in_catalog_order(self) -> None:
        self.assertEqual(resolve_apps(self.catalog, ["all"]), ["hub", "codex"])
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
            aliases.write_text("music\nbeep 440 100\nhub\nold command\n")
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
                    "codex",
                    "launch -f codex",
                    "uphub",
                    "flash /firmware/cardputer-hub.bin hub",
                    "upcodex",
                    "flash /firmware/Codex.bin codex",
                ],
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
                "hub,app,ota_0,0xd0000,0x280000,\n"
                "codex,app,ota_1,0x350000,0x200000,\n"
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
            _print_staged(["hub", "codex"])

        lines = output.getvalue().splitlines()
        self.assertLess(
            lines.index("remount the card in CRUB with 'sd', then run:"),
            lines.index("  uphub"),
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


if __name__ == "__main__":
    unittest.main()
