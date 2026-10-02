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
from tools.prepare_partition_table import (
    ENTRY,
    FLASH_SIZE,
    TABLE_OFFSET,
    encode_table,
    prepare_table,
)


ROOT = Path(__file__).resolve().parents[1]
CURRENT_TABLE_SHA256 = (
    "5c58e277a18e12a593da289becd9206441e759329df4bbb024758b05ff0ec16c"
)


def fake_app(
    payload: bytes = b"payload", project_name: str = "cardputer_hub"
) -> bytes:
    return segmented_app(payload, project_name)


def segmented_app(
    payload: bytes = b"payload", project_name: str = "arduino-lib-builder",
    *, hash_appended: bool = True, extra_segments: tuple = (),
) -> bytes:
    descriptor = bytearray(0x100)
    descriptor[0:4] = b"\x32\x54\xcd\xab"
    encoded_project_name = project_name.encode("ascii")
    descriptor[0x30 : 0x30 + len(encoded_project_name)] = encoded_project_name
    segment = bytes(descriptor) + payload
    segment += b"\0" * (-len(segment) % 4)
    header = bytearray(24)
    header[0] = 0xE9
    header[1] = 1 + len(extra_segments)
    header[23] = int(hash_appended)
    image = bytes(header)
    checksum = 0xEF
    for data in (segment,) + extra_segments:
        image += struct.pack("<II", 0x3C000020, len(data)) + data
        for byte in data:
            checksum ^= byte
    image += b"\0" * (15 - len(image) % 16) + bytes([checksum])
    return image + hashlib.sha256(image).digest() if hash_appended else image


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
            resolve_apps(self.catalog, ["all"]),
            ["hub", "codex", "bruce", "brucecompact", "marauder", "gpsinfo",
             "meshtastic"],
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
        self.assertEqual(by_name["extra"], (0x2D0000, 0x480000))
        self.assertEqual(by_name["mesh_fs"], (0x750000, 0x40000))
        self.assertEqual(by_name["apps_nvs"], (0x790000, 0x10000))
        self.assertEqual(by_name["hub_config"], (0x7A0000, 0x10000))
        self.assertEqual(by_name["spiffs"], (0x7B0000, 0x20000))
        self.assertNotIn("codex", by_name)
        self.assertNotIn("vfs", by_name)
        self.assertEqual(self.catalog["apps"]["codex"]["partition"], "extra")
        self.assertEqual(self.catalog["apps"]["bruce"]["partition"], "extra")

    def test_marauder_has_isolated_settings_and_shares_extra(self) -> None:
        partitions = validate_layout(ROOT / self.catalog["layout"], 0x800000)
        by_name = {partition["name"]: partition for partition in partitions}
        marauder = self.catalog["apps"]["marauder"]

        self.assertEqual(marauder["partition"], "extra")
        self.assertEqual(marauder["partition_size"], 0x480000)
        self.assertEqual(marauder["repository"], "fat23cat/ESP32Marauder")
        self.assertEqual(marauder["source_revision"],
                         "940ebfd380a464dd09184b2d561c11e59898922c")
        self.assertEqual(marauder["aliases"]["upmarauder"],
                         "flash /firmware/Marauder.bin extra")
        self.assertEqual(by_name["marauder_fs"], {
            "name": "marauder_fs", "type": "data", "subtype": "spiffs",
            "offset": 0x7D0000, "size": 0x20000,
        })
        self.assertEqual(by_name["spiffs"]["offset"], 0x7B0000)
        self.assertNotIn("release_asset", marauder)
        self.assertEqual(marauder["required_image_marker"], "marauder_bond")

    def test_gps_info_is_an_explicit_local_app_in_shared_extra(self) -> None:
        gps = self.catalog["apps"]["gpsinfo"]
        self.assertEqual(gps["repository"], "DevinWatson/Cardputer-Adv-GPS-Info")
        self.assertEqual(gps["source_revision"],
                         "f16b636ec657b8d1c3fd264544c376a7e6c2a5ad")
        self.assertEqual(gps["partition"], "extra")
        self.assertEqual(gps["partition_size"], 0x480000)
        self.assertEqual(gps["project_name"], "arduino-lib-builder")
        self.assertEqual(gps["required_image_marker"], "Cardputer ADV GPS Info")
        self.assertEqual(gps["sd_path"], "firmware/GPSInfo.bin")
        self.assertEqual(gps["local_repository"], "cardputer-firmware-manager")
        self.assertEqual(gps["build_command"], ["./tools/build_gps_info.sh"])
        self.assertEqual(gps["local_image"], "dist/GPSInfo.bin")
        self.assertFalse(gps["default_local"])
        self.assertNotIn("release_asset", gps)
        self.assertEqual(gps["aliases"]["upgpsinfo"],
                         "flash /firmware/GPSInfo.bin extra")
        self.assertEqual(gps["start"], ["upgpsinfo", "go"])

    def test_meshtastic_has_isolated_settings_and_shares_extra(self) -> None:
        partitions = validate_layout(ROOT / self.catalog["layout"], 0x800000)
        by_name = {partition["name"]: partition for partition in partitions}
        mesh = self.catalog["apps"]["meshtastic"]

        self.assertEqual(mesh["repository"], "meshtastic/firmware")
        self.assertEqual(mesh["source_revision"],
                         "54e0d8d0ab2ff56b3a9ce967e53f79e49af560fb")
        self.assertEqual(mesh["partition"], "extra")
        self.assertEqual(mesh["partition_size"], 0x480000)
        self.assertEqual(mesh["project_name"], "arduino-lib-builder")
        self.assertEqual(mesh["required_image_marker"], "mesh_bond")
        self.assertEqual(mesh["sd_path"], "firmware/Meshtastic.bin")
        self.assertEqual(mesh["local_repository"], "cardputer-firmware-manager")
        self.assertEqual(mesh["build_command"], ["./tools/build_meshtastic.sh"])
        self.assertEqual(mesh["local_image"], "dist/Meshtastic.bin")
        self.assertFalse(mesh["default_local"])
        self.assertNotIn("release_asset", mesh)
        self.assertEqual(mesh["aliases"]["upmesh"],
                         "flash /firmware/Meshtastic.bin extra")
        self.assertEqual(mesh["start"], ["upmesh", "go"])
        # Settings live after the shrunk extra slot, never in Bruce's spiffs.
        self.assertEqual(by_name["mesh_fs"], {
            "name": "mesh_fs", "type": "data", "subtype": "spiffs",
            "offset": 0x750000, "size": 0x40000,
        })
        self.assertEqual(
            by_name["extra"]["offset"] + by_name["extra"]["size"],
            by_name["mesh_fs"]["offset"],
        )
        self.assertEqual(
            by_name["mesh_fs"]["offset"] + by_name["mesh_fs"]["size"],
            by_name["apps_nvs"]["offset"],
        )

    def test_meshtastic_build_pins_source_and_isolation_patch(self) -> None:
        script = (ROOT / "tools" / "build_meshtastic.sh").read_text()
        patch = (ROOT / "patches" / "meshtastic-crub.patch").read_text()

        self.assertIn(self.catalog["apps"]["meshtastic"]["source_revision"], script)
        self.assertIn("patches/meshtastic-crub.patch", script)
        self.assertIn('"mesh_fs"', patch)
        self.assertIn('"mesh_bond"', patch)
        # Factory reset must not erase the default NVS shared with other apps.
        self.assertIn("-        nvs_flash_erase();", patch)
        # The OTA loader lookup must not select another application slot.
        self.assertIn("+    return NULL;", patch)

    def test_bruce_compact_shares_extra_and_bruce_settings(self) -> None:
        partitions = validate_layout(ROOT / self.catalog["layout"], 0x800000)
        names = {partition["name"] for partition in partitions}
        bruce = self.catalog["apps"]["bruce"]
        compact = self.catalog["apps"]["brucecompact"]

        # Same slot and same project as Bruce, so it replaces Bruce in extra and
        # reads the same /bruce.conf on SD and the same spiffs LittleFS.
        self.assertEqual(compact["partition"], bruce["partition"])
        self.assertEqual(compact["partition_size"], bruce["partition_size"])
        self.assertEqual(compact["project_name"], bruce["project_name"])
        self.assertNotIn("brucecompact_fs", names)
        self.assertEqual(compact["aliases"]["upbrucec"],
                         "flash /firmware/BruceCompact.bin extra")
        self.assertEqual(compact["start"], ["upbrucec", "go"])
        self.assertEqual(bruce["aliases"]["upbruce"], "flash /firmware/Bruce.bin extra")
        # Local-only, opt-in build that must carry the Compact UI marker.
        self.assertNotIn("release_asset", compact)
        self.assertFalse(compact["default_local"])
        self.assertEqual(compact["local_repository"], "cardputer-firmware-manager")
        self.assertEqual(compact["build_command"], ["./tools/build_bruce_compact.sh"])
        self.assertEqual(compact["local_image"], "dist/BruceCompact.bin")
        self.assertEqual(compact["required_image_marker"], "Compact UI: ")

    def test_local_only_application_is_not_downloaded_by_release_all(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch("firmware_manager.cli.GitHubReleaseClient") as client:
                client.return_value.download.side_effect = FirmwareError("stop before staging")
                with self.assertRaisesRegex(FirmwareError, "stop before staging"):
                    run(["release", "--sd", temporary_directory])
                self.assertEqual(client.return_value.download.call_args.args[0], "hub")

        with self.assertRaisesRegex(FirmwareError, "no published release"):
            run(["release", "--app", "marauder", "--sd", temporary_directory])


class PartitionTableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_catalog(ROOT / "firmware-manager.json")
        self.current = self.catalog["partition_contract"]

    def earlier(self, *removed: str) -> list:
        return [
            dict(partition, size=0x4C0000) if partition["name"] == "extra"
            else dict(partition)
            for partition in self.current
            if partition["name"] not in removed
        ]

    def make_backup(self, destination: Path, partitions: list,
                    extra_image: bytes = b"") -> None:
        data = bytearray(b"\xff" * FLASH_SIZE)
        table = encode_table(partitions)
        data[TABLE_OFFSET : TABLE_OFFSET + len(table)] = table
        data[0x2D0000 : 0x2D0000 + len(extra_image)] = extra_image
        destination.write_bytes(bytes(data))

    def entries(self, table: bytes) -> list:
        rows = []
        for index in range(len(self.current)):
            row = ENTRY.unpack_from(table, index * ENTRY.size)
            rows.append((row[5].rstrip(b"\0").decode(), row[3], row[4]))
        return rows

    def test_shrinks_extra_and_adds_mesh_fs_to_marauder_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup.bin"
            result = Path(directory) / "partitions.bin"
            self.make_backup(backup, self.earlier("mesh_fs"),
                             segmented_app(b"x" * 0x1000))
            table = prepare_table(backup, result)
            self.assertEqual(table, result.read_bytes())
            self.assertEqual(self.entries(table), [
                (part["name"], part["offset"], part["size"])
                for part in self.current
            ])
            self.assertIn(("extra", 0x2D0000, 0x480000), self.entries(table))
            self.assertIn(("mesh_fs", 0x750000, 0x40000), self.entries(table))
            self.assertIn(("spiffs", 0x7B0000, 0x20000), self.entries(table))
            self.assertEqual(hashlib.sha256(table).hexdigest(),
                             CURRENT_TABLE_SHA256)

    def test_migrates_table_from_before_marauder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup.bin"
            result = Path(directory) / "partitions.bin"
            self.make_backup(backup, self.earlier("mesh_fs", "marauder_fs"))
            table = prepare_table(backup, result)
            self.assertEqual(hashlib.sha256(table).hexdigest(),
                             CURRENT_TABLE_SHA256)

    def test_rejects_nonblank_new_range_and_unexpected_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup.bin"
            result = Path(directory) / "partitions.bin"
            for removed, offset in ((("mesh_fs",), 0x750000),
                                    (("mesh_fs", "marauder_fs"), 0x7D0000)):
                self.make_backup(backup, self.earlier(*removed))
                with backup.open("r+b") as data:
                    data.seek(offset + 0x100)
                    data.write(b"x")
                with self.assertRaisesRegex(FirmwareError, "not blank"):
                    prepare_table(backup, result)
                self.assertFalse(result.exists())
            self.make_backup(backup, self.earlier("mesh_fs"))
            with backup.open("r+b") as data:
                data.seek(TABLE_OFFSET + 4 * ENTRY.size)
                data.write(b"x")
            with self.assertRaisesRegex(FirmwareError, "differs"):
                prepare_table(backup, result)
            self.assertFalse(result.exists())

    def test_rejects_application_in_extra_that_does_not_fit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup.bin"
            result = Path(directory) / "partitions.bin"
            self.make_backup(backup, self.earlier("mesh_fs"),
                             segmented_app(b"x" * 0x480000))
            with self.assertRaisesRegex(FirmwareError, "does not fit"):
                prepare_table(backup, result)
            self.assertFalse(result.exists())

    def test_rejects_backup_that_already_uses_the_current_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup.bin"
            result = Path(directory) / "partitions.bin"
            self.make_backup(backup, self.current)
            with self.assertRaisesRegex(FirmwareError, "already"):
                prepare_table(backup, result)
            self.assertFalse(result.exists())

    def test_preserves_backup_when_output_refers_to_the_same_file(self) -> None:
        for kind in ("same path", "symlink", "hard link"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                backup = Path(directory) / "backup.bin"
                self.make_backup(backup, self.earlier("mesh_fs"))
                original = backup.read_bytes()
                output = Path(directory) / "output.bin"
                if kind == "same path":
                    output = backup
                elif kind == "symlink":
                    output.symlink_to(backup)
                else:
                    os.link(backup, output)

                with self.assertRaisesRegex(FirmwareError, "backup"):
                    prepare_table(backup, output)

                self.assertEqual(backup.read_bytes(), original)


class ImageIntegrityTest(unittest.TestCase):
    def test_rejects_truncated_and_corrupt_images_before_changing_sd(self) -> None:
        catalog = load_catalog(ROOT / "firmware-manager.json")
        valid = fake_app(b"x" * 256)
        corrupt_payload = bytearray(valid)
        corrupt_payload[0x120] ^= 1
        corrupt_digest = bytearray(valid)
        corrupt_digest[-1] ^= 1
        corrupt_checksum = bytearray(valid)
        corrupt_checksum[-33] ^= 1
        # Recompute the hash so the XOR checksum must be checked independently.
        corrupt_checksum[-32:] = hashlib.sha256(corrupt_checksum[:-32]).digest()
        malformed = []
        for index, value in ((1, 0), (1, 17), (23, 2)):
            data = bytearray(valid)
            data[index] = value
            malformed.append(bytes(data))
        for data in (valid[:0x130], valid[:-33], valid[:-1],
                     bytes(corrupt_payload), bytes(corrupt_digest),
                     bytes(corrupt_checksum), *malformed):
            with self.subTest(size=len(data)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                sd = root / "card"
                sd.mkdir()
                image = root / "hub.bin"
                image.write_bytes(data)
                previous = sd / catalog["apps"]["hub"]["sd_path"]
                previous.parent.mkdir()
                previous.write_bytes(valid)

                with self.assertRaises(FirmwareError):
                    stage_images(catalog, {"hub": image}, sd, require_mount=False)

                self.assertEqual(previous.read_bytes(), valid)
                self.assertFalse((sd / ".crub").exists())
                self.assertFalse((sd / "firmwares.txt").exists())

    def test_accepts_multiple_segments_with_and_without_appended_hash(self) -> None:
        for hashed in (False, True):
            with self.subTest(hashed=hashed), tempfile.TemporaryDirectory() as directory:
                image = Path(directory) / "hub.bin"
                image.write_bytes(segmented_app(
                    project_name="cardputer_hub", hash_appended=hashed,
                    extra_segments=(b"\x01\x02\x03\x04", b"\x05\x06\x07\x08"),
                ))
                validate_image("hub", image, 0x200000, "cardputer_hub")

    def test_doctor_rejects_truncated_image_even_with_matching_sd_metadata(self) -> None:
        catalog = load_catalog(ROOT / "firmware-manager.json")
        with tempfile.TemporaryDirectory() as directory:
            sd = Path(directory)
            image = sd / catalog["apps"]["hub"]["sd_path"]
            image.parent.mkdir()
            data = fake_app(b"x" * 256)[:0x130]
            image.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            (image.parent / "SHA256SUMS").write_text(f"{digest}  {image.name}\n")
            (image.parent / "firmware-manager-lock.json").write_text(json.dumps({
                "schema_version": 1, "apps": {"hub": {"sha256": digest}}
            }))

            with self.assertRaisesRegex(FirmwareError, "truncated"):
                _doctor(catalog, sd, require_mount=False)


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

    def test_local_bruce_compact_uses_manager_build_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            (workspace / "cardputer-firmware-manager").mkdir()
            with mock.patch("firmware_manager.cli.stage_images") as stage:
                with redirect_stdout(io.StringIO()):
                    run([
                        "local", "--app", "brucecompact", "--workspace",
                        str(workspace), "--sd", str(workspace),
                    ])
            self.assertEqual(
                stage.call_args.args[1]["brucecompact"],
                workspace.resolve() / "cardputer-firmware-manager" / "dist" / "BruceCompact.bin",
            )

    def test_release_bruce_compact_has_no_published_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(FirmwareError, "no published release"):
                run(["release", "--app", "brucecompact", "--sd", temporary_directory])

    def test_local_marauder_uses_isolated_build_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            (workspace / "cardputer-firmware-manager").mkdir()
            with mock.patch("firmware_manager.cli.stage_images") as stage:
                with redirect_stdout(io.StringIO()):
                    run([
                        "local", "--app", "marauder", "--workspace",
                        str(workspace), "--sd", str(workspace),
                    ])
            self.assertEqual(
                stage.call_args.args[1]["marauder"],
                workspace.resolve() / "cardputer-firmware-manager" / "dist" / "Marauder.bin",
            )

    def test_local_gps_info_uses_pinned_build_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            (workspace / "cardputer-firmware-manager").mkdir()
            with mock.patch("firmware_manager.cli.stage_images") as stage:
                with redirect_stdout(io.StringIO()):
                    run([
                        "local", "--app", "gpsinfo", "--workspace",
                        str(workspace), "--sd", str(workspace),
                    ])
            self.assertEqual(
                stage.call_args.args[1]["gpsinfo"],
                workspace.resolve() / "cardputer-firmware-manager" / "dist" / "GPSInfo.bin",
            )

    def test_local_meshtastic_uses_isolated_build_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            (workspace / "cardputer-firmware-manager").mkdir()
            with mock.patch("firmware_manager.cli.stage_images") as stage:
                with redirect_stdout(io.StringIO()):
                    run([
                        "local", "--app", "meshtastic", "--workspace",
                        str(workspace), "--sd", str(workspace),
                    ])
            self.assertEqual(
                stage.call_args.args[1]["meshtastic"],
                workspace.resolve() / "cardputer-firmware-manager" / "dist" / "Meshtastic.bin",
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

    def test_staging_marauder_keeps_bruce_image_on_sd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sd = root / "card"
            sd.mkdir()
            bruce = sd / "firmware" / "Bruce.bin"
            bruce.parent.mkdir()
            bruce.write_bytes(fake_app(b"Bruce", "arduino-lib-builder"))
            marauder = root / "Marauder.bin"
            marauder.write_bytes(fake_app(b"Marauder marauder_fs marauder_bond", "arduino-lib-builder"))

            stage_images(self.catalog, {"marauder": marauder}, sd,
                         require_mount=False)

            self.assertEqual(bruce.read_bytes(),
                             fake_app(b"Bruce", "arduino-lib-builder"))
            self.assertEqual((sd / "firmware" / "Marauder.bin").read_bytes(),
                             marauder.read_bytes())

    def test_staging_gps_info_preserves_unselected_images_and_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sd = root / "card"
            (sd / "firmware").mkdir(parents=True)
            (sd / ".crub").mkdir()
            codex = sd / "firmware" / "Codex.bin"
            codex.write_bytes(fake_app(b"Codex", "codex_microputer_adv"))
            (sd / ".crub" / "aliases").write_text("custom\necho hello\n")
            gps = root / "GPSInfo.bin"
            gps.write_bytes(fake_app(b"Cardputer ADV GPS Info", "arduino-lib-builder"))

            stage_images(self.catalog, {"gpsinfo": gps}, sd,
                         require_mount=False)

            self.assertEqual(codex.read_bytes(),
                             fake_app(b"Codex", "codex_microputer_adv"))
            self.assertEqual((sd / "firmware" / "GPSInfo.bin").read_bytes(),
                             gps.read_bytes())
            self.assertIn("custom\necho hello\n", (sd / ".crub" / "aliases").read_text())
            self.assertIn("upgpsinfo\nflash /firmware/GPSInfo.bin extra\n",
                          (sd / ".crub" / "aliases").read_text())
            self.assertIn("GPSINFO\nstart: upgpsinfo -> go",
                          (sd / "firmwares.txt").read_text())

    def test_rejects_unidentified_gps_info_image_before_writing_sd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sd = root / "card"
            sd.mkdir()
            image = root / "ordinary-firmware.bin"
            image.write_bytes(fake_app(b"unrelated firmware", "arduino-lib-builder"))

            with self.assertRaisesRegex(FirmwareError, "marker"):
                stage_images(self.catalog, {"gpsinfo": image}, sd,
                             require_mount=False)

            self.assertFalse((sd / "firmware").exists())

    def test_staging_meshtastic_preserves_other_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sd = root / "card"
            (sd / "firmware").mkdir(parents=True)
            bruce = sd / "firmware" / "Bruce.bin"
            bruce.write_bytes(fake_app(b"Bruce", "arduino-lib-builder"))
            mesh = root / "Meshtastic.bin"
            mesh.write_bytes(fake_app(b"Meshtastic mesh_fs mesh_bond", "arduino-lib-builder"))

            stage_images(self.catalog, {"meshtastic": mesh}, sd,
                         require_mount=False)

            self.assertEqual(bruce.read_bytes(), fake_app(b"Bruce", "arduino-lib-builder"))
            self.assertEqual((sd / "firmware" / "Meshtastic.bin").read_bytes(),
                             mesh.read_bytes())
            self.assertIn("upmesh\nflash /firmware/Meshtastic.bin extra\n",
                          (sd / ".crub" / "aliases").read_text())
            self.assertIn("MESHTASTIC\nstart: upmesh -> go",
                          (sd / "firmwares.txt").read_text())

    def test_rejects_official_meshtastic_image_before_writing_sd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sd = root / "card"
            sd.mkdir()
            image = root / "firmware-m5stack-cardputer-adv.bin"
            image.write_bytes(fake_app(b"Meshtastic spiffs", "arduino-lib-builder"))

            with self.assertRaisesRegex(FirmwareError, "marker"):
                stage_images(self.catalog, {"meshtastic": image}, sd,
                             require_mount=False)

            self.assertFalse((sd / "firmware").exists())

    def test_staging_bruce_compact_keeps_release_bruce_on_sd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sd = root / "card"
            sd.mkdir()
            bruce = sd / "firmware" / "Bruce.bin"
            bruce.parent.mkdir()
            bruce.write_bytes(fake_app(b"Bruce", "arduino-lib-builder"))
            compact = root / "BruceCompact.bin"
            compact.write_bytes(fake_app(b"Bruce Compact UI: ON", "arduino-lib-builder"))

            stage_images(self.catalog, {"brucecompact": compact}, sd,
                         require_mount=False)

            self.assertEqual(bruce.read_bytes(),
                             fake_app(b"Bruce", "arduino-lib-builder"))
            self.assertEqual((sd / "firmware" / "BruceCompact.bin").read_bytes(),
                             compact.read_bytes())
            listing = (sd / "firmwares.txt").read_text()
            self.assertIn("start: upbrucec -> go", listing)

    def test_rejects_release_bruce_image_as_bruce_compact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sd = root / "card"
            sd.mkdir()
            release = root / "Bruce.bin"
            release.write_bytes(fake_app(b"Bruce without compact UI", "arduino-lib-builder"))

            with self.assertRaisesRegex(FirmwareError, "marker"):
                stage_images(self.catalog, {"brucecompact": release}, sd,
                             require_mount=False)

            self.assertFalse((sd / "firmware" / "BruceCompact.bin").exists())

    def test_stages_readable_list_of_firmware_present_on_sd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sd = root / "card"
            sd.mkdir()
            bruce = sd / "firmware" / "Bruce.bin"
            bruce.parent.mkdir()
            previous_bruce = fake_app(b"Bruce", "arduino-lib-builder")
            bruce.write_bytes(previous_bruce)
            hub = root / "hub.bin"
            hub.write_bytes(fake_app())

            stage_images(self.catalog, {"hub": hub}, sd, require_mount=False)

            self.assertEqual(bruce.read_bytes(), previous_bruce)
            self.assertEqual(
                (sd / "firmwares.txt").read_text(),
                "FIRMWARES ON SD\n\n"
                "HUB\nstart: uphub -> hub\n\n"
                "BRUCE\nstart: upbruce -> go\n\n"
                "BOOT MODES (apply on reset)\n"
                "hubfast: auto-boot Hub (USB log)\n"
                "gofast: auto-boot extra (USB log)\n"
                "crubmenu: CRUB menu; boot w/o SD 1st\n",
            )
            self.assertIn(
                "fw\ncat /firmwares.txt\n",
                (sd / ".crub" / "aliases").read_text(),
            )

    def test_rejects_firmware_list_command_without_a_managed_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            catalog = deepcopy(self.catalog)
            catalog["firmware_list_commands"]["typo"] = "does nothing"
            catalog_path = Path(temporary_directory) / "catalog.json"
            catalog_path.write_text(json.dumps(catalog))

            with self.assertRaisesRegex(FirmwareError, "unknown alias"):
                load_catalog(catalog_path)

    def test_legacy_v1_catalog_still_stages_without_a_firmware_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog = deepcopy(self.catalog)
            del catalog["firmware_list_path"]
            del catalog["aliases"]["fw"]
            for app in catalog["apps"].values():
                del app["start"]
            catalog_path = root / "legacy.json"
            catalog_path.write_text(json.dumps(catalog))
            loaded = load_catalog(catalog_path)
            sd = root / "card"
            sd.mkdir()
            hub = root / "hub.bin"
            hub.write_bytes(fake_app())

            stage_images(loaded, {"hub": hub}, sd, require_mount=False)

            self.assertFalse((sd / "firmwares.txt").exists())
            self.assertNotIn("fw\n", (sd / ".crub" / "aliases").read_text())
            output = io.StringIO()
            with redirect_stdout(output):
                _print_staged(loaded, ["hub"])
            self.assertNotIn("fw (list firmware on SD)", output.getvalue())

    def test_rejects_firmware_list_path_overlapping_sd_files_before_copy(self) -> None:
        for list_path in ("firmware/Bruce.bin", ".crub/boot"):
            with self.subTest(list_path=list_path), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                sd = root / "card"
                sd.mkdir()
                protected = sd / list_path
                protected.parent.mkdir(parents=True)
                protected.write_bytes(b"keep this file")
                hub = root / "hub.bin"
                hub.write_bytes(fake_app())
                catalog = deepcopy(self.catalog)
                catalog["firmware_list_path"] = list_path
                catalog["aliases"]["fw"] = f"cat /{list_path}"

                with self.assertRaisesRegex(FirmwareError, "firmware list path"):
                    stage_images(catalog, {"hub": hub}, sd, require_mount=False)

                self.assertEqual(protected.read_bytes(), b"keep this file")
                self.assertFalse((sd / "firmware" / "cardputer-hub.bin").exists())

    def test_rejects_list_path_matching_an_application_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sd = root / "card"
            sd.mkdir()
            protected = sd / "firmwares.txt"
            protected.write_bytes(b"existing firmware")
            hub = root / "hub.bin"
            hub.write_bytes(fake_app())
            catalog = deepcopy(self.catalog)
            catalog["apps"]["bruce"]["sd_path"] = "firmwares.txt"

            with self.assertRaisesRegex(FirmwareError, "firmware list path"):
                stage_images(catalog, {"hub": hub}, sd, require_mount=False)

            self.assertEqual(protected.read_bytes(), b"existing firmware")
            self.assertFalse((sd / "firmware" / "cardputer-hub.bin").exists())

    def test_rejects_firmware_list_symlink_before_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sd = root / "card"
            sd.mkdir()
            protected = sd / "notes.txt"
            protected.write_text("keep these notes")
            (sd / "firmwares.txt").symlink_to(protected)
            hub = root / "hub.bin"
            hub.write_bytes(fake_app())

            with self.assertRaisesRegex(FirmwareError, "firmware list path"):
                stage_images(self.catalog, {"hub": hub}, sd, require_mount=False)

            self.assertEqual(protected.read_text(), "keep these notes")
            self.assertFalse((sd / "firmware" / "cardputer-hub.bin").exists())

    def test_rejects_unisolated_marauder_before_writing_sd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sd = root / "card"
            sd.mkdir()
            image = root / "official.bin"
            image.write_bytes(fake_app(b"ordinary Marauder marauder_fs", "arduino-lib-builder"))
            with self.assertRaisesRegex(FirmwareError, "required image marker"):
                stage_images(self.catalog, {"marauder": image}, sd,
                             require_mount=False)
            self.assertEqual(list(sd.iterdir()), [])

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
                "extra\nlaunch -f extra\n"
                "extrafast\necho launch -f extra > /.crub/boot\n"
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
                    "fw",
                    "cat /firmwares.txt",
                    "go",
                    "launch -f extra",
                    "gofast",
                    "echo launch -f extra > /.crub/boot",
                    "uphub",
                    "flash /firmware/cardputer-hub.bin hub",
                    "upcodex",
                    "flash /firmware/Codex.bin extra",
                    "upbruce",
                    "flash /firmware/Bruce.bin extra",
                    "upbrucec",
                    "flash /firmware/BruceCompact.bin extra",
                    "upmarauder",
                    "flash /firmware/Marauder.bin extra",
                    "upgpsinfo",
                    "flash /firmware/GPSInfo.bin extra",
                    "upmesh",
                    "flash /firmware/Meshtastic.bin extra",
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
        self.assertIn("  fw (list firmware on SD)", lines)

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


    def test_post_stage_instructions_use_the_catalog_update_alias(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            _print_staged(self.catalog, ["brucecompact"])

        self.assertEqual(output.getvalue().splitlines()[-1], "  upbrucec")


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
            f"https://api.github.com/repos/BruceDevices/firmware/releases/tags/{tag}": json.dumps(
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
