from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from firmware_manager.core import (
    FirmwareError,
    GitHubReleaseClient,
    load_catalog,
    resolve_apps,
    stage_images,
    validate_layout,
)


ROOT = Path(__file__).resolve().parents[1]


def fake_app(payload: bytes = b"payload") -> bytes:
    return b"\xe9" + (b"\0" * 31) + b"\x32\x54\xcd\xab" + payload


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
            existing_codex.write_bytes(fake_app(b"old codex"))
            hub = temporary / "hub.bin"
            hub.write_bytes(fake_app(b"new hub"))

            staged = stage_images(self.catalog, {"hub": hub}, sd)

            self.assertEqual(
                staged, [sd.resolve() / "firmware" / "cardputer-hub.bin"]
            )
            self.assertEqual(existing_codex.read_bytes(), fake_app(b"old codex"))
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

            stage_images(self.catalog, {"hub": hub}, sd)

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
                stage_images(self.catalog, {"hub": invalid}, temporary / "card")

            oversized = temporary / "oversized.bin"
            oversized.write_bytes(fake_app())
            with oversized.open("r+b") as image:
                image.truncate(self.catalog["apps"]["codex"]["partition_size"] + 1)
            with self.assertRaisesRegex(FirmwareError, "partition limit"):
                stage_images(self.catalog, {"codex": oversized}, temporary / "card")


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
