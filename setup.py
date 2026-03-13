"""Custom build hook to copy root .env.example into atlas/ package.

The root .env.example is the single source of truth. This hook ensures
it gets included in the atlas package so atlas-init can ship it to users.
"""

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist


def _copy_env_example():
    """Copy root .env.example into atlas/ so it's included in the package."""
    root = Path(__file__).resolve().parent
    src = root / ".env.example"
    dst = root / "atlas" / ".env.example"
    if src.exists():
        shutil.copy2(src, dst)


class BuildPyWithEnvExample(build_py):
    """Copy root .env.example into the atlas package before building."""

    def run(self):
        _copy_env_example()
        super().run()


class SdistWithEnvExample(sdist):
    """Copy root .env.example into the atlas package before creating sdist."""

    def run(self):
        _copy_env_example()
        super().run()


setup(
    cmdclass={
        "build_py": BuildPyWithEnvExample,
        "sdist": SdistWithEnvExample,
    }
)
