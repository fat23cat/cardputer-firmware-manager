"""Include the repository's canonical catalog and layout in built wheels."""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


ROOT = Path(__file__).resolve().parent
RESOURCES = [Path("firmware-manager.json")] + sorted(
    path.relative_to(ROOT) for path in (ROOT / "layouts").glob("*.csv")
)


class BuildPy(build_py):
    def run(self):
        super().run()
        for relative in RESOURCES:
            destination = Path(self.build_lib) / "firmware_manager" / "data" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.copy_file(str(ROOT / relative), str(destination))

    def get_outputs(self, include_bytecode=1):
        return super().get_outputs(include_bytecode) + [
            str(Path(self.build_lib) / "firmware_manager" / "data" / relative)
            for relative in RESOURCES
        ]


setup(cmdclass={"build_py": BuildPy})
