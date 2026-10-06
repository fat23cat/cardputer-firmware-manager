"""Exercise SD workflow ordering without touching a card or hardware."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MakeWorkflowTest(unittest.TestCase):
    def run_flash(self, fail_doctor=False):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            log = directory / "calls.jsonl"
            driver = directory / "manager.py"
            driver.write_text(
                "import json,sys\n"
                f"with open({str(log)!r}, 'a') as log: log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                + ("sys.exit(8)\n" if fail_doctor else "")
            )
            result = subprocess.run(
                ["make", "-C", str(ROOT), "flash", "APP=hub", "SD=/Volumes/My Card",
                 f"MANAGER=python3 {driver}"],
                capture_output=True, text=True,
            )
            return result, [json.loads(line) for line in log.read_text().splitlines()]

    def test_flash_validates_before_build_and_after_staging(self):
        result, calls = self.run_flash()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in calls], ["doctor", "local", "doctor"])
        self.assertIn("--build", calls[1])
        self.assertIn("hub", calls[1])
        self.assertTrue(all("/Volumes/My Card" in call for call in calls))

    def test_failed_preflight_does_not_build_or_stage(self):
        result, calls = self.run_flash(fail_doctor=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "doctor")
