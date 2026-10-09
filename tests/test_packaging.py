from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
try:
    CAN_BUILD = int(version("setuptools").split(".")[0]) >= 68
except PackageNotFoundError:
    CAN_BUILD = False


@unittest.skipUnless(CAN_BUILD, "wheel smoke test requires setuptools>=68")
class PackagingTest(unittest.TestCase):
    def command(self, args: list, cwd: Path, env: dict = None) -> str:
        result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_wheel_from_sdist_runs_cli_with_bundled_catalog_and_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(
                ".git", ".claude", ".DS_Store", ".pycache", "__pycache__",
                ".venv", "dist", "build", "*.egg-info",
            ))
            self.command([
                sys.executable, "-c",
                "from setuptools.build_meta import build_sdist; build_sdist('dist')",
            ], source)
            with tarfile.open(next((source / "dist").glob("*.tar.gz"))) as archive:
                if hasattr(tarfile, "data_filter"):
                    archive.extractall(root / "sdist", filter="data")
                else:
                    archive.extractall(root / "sdist")
            sdist = next((root / "sdist").iterdir())
            self.command([
                sys.executable, "-c",
                "from setuptools.build_meta import build_wheel; build_wheel('dist')",
            ], sdist)
            installed = root / "installed"
            with zipfile.ZipFile(next((sdist / "dist").glob("*.whl"))) as archive:
                archive.extractall(installed)
                entry_points = next(name for name in archive.namelist()
                                    if name.endswith("entry_points.txt"))
                self.assertIn("cardputer-firmware = firmware_manager.cli:main",
                              archive.read(entry_points).decode())
            env = dict(os.environ, PYTHONPATH=str(installed))
            entry = [sys.executable, "-c", "from firmware_manager.cli import main; main()"]
            listing = self.command(entry + ["list"], root, env)
            self.assertIn("meshtastic", listing)
            self.assertIn("meshcore", listing)
            self.assertIn("layout: ok", self.command(entry + ["doctor"], root, env))

            # A supplied catalog resolves its layout relative to that catalog.
            custom = root / "custom"
            custom.mkdir()
            catalog = json.loads((ROOT / "firmware-manager.json").read_text())
            catalog["layout"] = "custom.csv"
            (custom / "catalog.json").write_text(json.dumps(catalog))
            shutil.copyfile(ROOT / "layouts/cardputer-adv-8mb.csv", custom / "custom.csv")
            self.assertIn("layout: ok", self.command(
                entry + ["--catalog", str(custom / "catalog.json"), "doctor"], root, env
            ))
