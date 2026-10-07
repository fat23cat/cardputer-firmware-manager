from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MarauderSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.name", "Build test")
        self.git("config", "user.email", "build@example.invalid")
        (self.repository / "README").write_text("reviewed revision\n")
        self.git("add", "README")
        self.git("commit", "--quiet", "-m", "Reviewed revision")
        self.pinned = self.git("rev-parse", "HEAD").strip()
        (self.repository / "README").write_text("newer main\n")
        self.git("commit", "--quiet", "-am", "Advance main")
        self.newer = self.git("rev-parse", "HEAD").strip()
        self.git("branch", "codex/cardputer-crub-extra")

        project = self.root / "project"
        (project / "tools").mkdir(parents=True)
        self.script = project / "tools" / "build_marauder.sh"
        script = (ROOT / "tools" / "build_marauder.sh").read_text()
        self.script.write_text(re.sub(
            r"^source_commit=.*$", "source_commit=" + self.pinned,
            script, flags=re.MULTILINE,
        ))
        self.libraries = self.root / "libraries"
        self.libraries.mkdir()

    def git(self, *args: str) -> str:
        return subprocess.check_output([
            "git", "-c", "core.hooksPath=/dev/null", "-c",
            "commit.gpgsign=false", "-C", str(self.repository), *args,
        ], text=True)

    def run_recipe(self, source: Path | None = None) -> tuple:
        environment = os.environ.copy()
        environment.update({
            "MARAUDER_KEEP_BUILD": "1",
            "MARAUDER_LIBRARIES_DIR": str(self.libraries),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "url." + self.repository.as_uri() + ".insteadOf",
            "GIT_CONFIG_VALUE_0": "https://github.com/fat23cat/ESP32Marauder.git",
            "GIT_ALLOW_PROTOCOL": "file",
        })
        environment.pop("MARAUDER_SOURCE_DIR", None)
        if source is not None:
            environment["MARAUDER_SOURCE_DIR"] = str(source)
        result = subprocess.run(
            ["bash", str(self.script)], env=environment,
            text=True, capture_output=True, timeout=30,
        )
        workspace_line = result.stdout.splitlines()[0]
        self.assertTrue(workspace_line.startswith("Marauder build workspace: "))
        workspace = Path(workspace_line.split(": ", 1)[1])
        self.addCleanup(shutil.rmtree, workspace)
        return result, workspace

    def test_fetches_reviewed_revision_after_remote_branches_advance(self) -> None:
        result, workspace = self.run_recipe()
        actual = subprocess.check_output([
            "git", "-C", str(workspace / "ESP32Marauder"), "rev-parse", "HEAD",
        ], text=True).strip()
        self.assertEqual(actual, self.pinned)
        # Empty test libraries stop the recipe before any compiler is invoked.
        self.assertIn("Unexpected NimBLE source revision", result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git("rev-parse", "main").strip(), self.newer)

    def test_rejects_wrong_local_revision_without_touching_the_source(self) -> None:
        readme = self.repository / "README"
        readme.write_text("user changes\n")
        result, _workspace = self.run_recipe(self.repository)
        self.assertIn("Unexpected Marauder source revision", result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.newer)
        self.assertEqual(readme.read_text(), "user changes\n")
