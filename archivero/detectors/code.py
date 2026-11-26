"""Code project roots."""

from .base import UNIT, Detection

MARKER_FILES = {
    "package.json", "setup.py", "pyproject.toml", "requirements.txt", "pipfile", "pom.xml",
    "build.gradle", "build.gradle.kts", "cmakelists.txt", "cargo.toml", "go.mod", "composer.json",
    "gemfile", "hardhat.config.js", "hardhat.config.ts", "truffle-config.js", "foundry.toml",
    "makefile", "configure.ac", "build.xml", "mix.exs", "pubspec.yaml", "angular.json",
}
MARKER_DIRS = {".git", ".svn", ".hg", "node_modules"}
MARKER_SUFFIXES = (".xcodeproj", ".xcworkspace", ".sln", ".csproj", ".iml")


def detect_dir(path, entries, names_low, cfg):
    markers = (names_low & MARKER_FILES) | (names_low & MARKER_DIRS)
    markers |= {n for n in names_low if n.endswith(MARKER_SUFFIXES)}
    if markers:
        return Detection("code_project", UNIT, ", ".join(sorted(markers))[:300])
    return None
