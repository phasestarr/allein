"""Validate allein's repository tooling without application or cluster credentials.

Run with Python 3.14 after installing validation/requirements.txt.
The CLI accepts lint, build, test, or all (the default), in that order.
lint checks YAML, Python, and whitespace in existing nonignored source files.
build compiles validation Python sources into ignored .validation/bytecode/.
test checks the CI/Dependabot contract and runs validation/test_validate.py.
Failures return a nonzero exit code; later categories do not run after failure.

.github/workflows/ci.yaml runs the same categories in its validation job on
pull requests targeting main and pushes to main, using a GitHub-hosted runner.
.github/dependabot.yaml updates Actions and the validation Python dependencies.
Branch rules and repository security switches remain GitHub settings.
This tooling does not build or test the application, images, or Helm charts.
"""

import argparse
import os
import py_compile
import re
import subprocess
import sys
from pathlib import Path

import pathspec
import yaml

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ("lint", "build", "test")
INSTALL = "python -m pip install -r validation/requirements.txt"
COMMANDS = [f"python validation/validate.py {category}" for category in CATEGORIES]
YAMLLINT = (
    "{extends: default, rules: {document-start: disable, line-length: disable, "
    "truthy: {check-keys: false}, comments: {min-spaces-from-content: 1}}}"
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


class UniqueKeyLoader(yaml.BaseLoader):
    """Keep YAML scalars as strings (including GitHub's 'on') and reject duplicates."""

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            require(isinstance(key, str), "YAML mapping keys must be scalar strings")
            require(key not in result, f"Duplicate YAML key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def load_mapping(path):
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    require(isinstance(value, dict), f"{path.name}: expected a YAML mapping")
    return value


def files(root=ROOT):
    """Include untracked source files, without running Git or walking ignored trees."""
    ignored = pathspec.GitIgnoreSpec.from_lines(
        (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    )
    paths = []
    for directory, subdirectories, filenames in os.walk(root):
        relative = Path(directory).relative_to(root)
        subdirectories[:] = [
            name
            for name in subdirectories
            if name != ".git"
            and not ignored.match_file((relative / name).as_posix() + "/")
        ]
        for name in subdirectories + filenames:
            path = Path(directory) / name
            if not ignored.match_file(path.relative_to(root).as_posix()):
                require(not path.is_symlink(), f"Unsupported source symlink: {path}")
        paths.extend(
            Path(directory) / name
            for name in filenames
            if not ignored.match_file((relative / name).as_posix())
        )
    return sorted(paths)


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def lint():
    paths = files()
    yaml_paths = [str(path) for path in paths if path.suffix in {".yaml", ".yml"}]
    require(yaml_paths, "No YAML configuration found")
    run(sys.executable, "-m", "yamllint", "--strict", "-d", YAMLLINT, *yaml_paths)
    for path in paths:
        if path.suffix in {".yaml", ".yml"}:
            load_mapping(path)
        if path.suffix not in {".md", ".yaml", ".yml", ".py", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8")
        require(text.endswith("\n"), f"{path.relative_to(ROOT)}: missing final newline")
        for number, line in enumerate(text.splitlines(), 1):
            require(
                line == line.rstrip(), f"{path.relative_to(ROOT)}:{number}: whitespace"
            )
    run(sys.executable, "-m", "ruff", "check", "--isolated", "validation")
    run(sys.executable, "-m", "ruff", "format", "--isolated", "--check", "validation")
    print(f"PASS: YAML, Python, and whitespace ({len(paths)} source files)")


def compile_sources(paths, root, output):
    require(paths, "No validation Python sources found")
    for source in paths:
        target = (output / source.relative_to(root)).with_suffix(".pyc")
        target.parent.mkdir(parents=True, exist_ok=True)
        py_compile.compile(str(source), cfile=str(target), doraise=True)


def build():
    sources = [
        path
        for path in files()
        if path.suffix == ".py" and path.is_relative_to(ROOT / "validation")
    ]
    compile_sources(sources, ROOT, ROOT / ".validation" / "bytecode")
    print(f"PASS: compiled {len(sources)} validation tools (no application build)")


def validate_ci(workflow):
    """Check the bootstrap job's trigger, permission, and ordered execution contract."""
    require(workflow.get("name") == "CI", "CI workflow name must be CI")
    require(
        workflow.get("on")
        == {"pull_request": {"branches": ["main"]}, "push": {"branches": ["main"]}},
        "CI must validate every main PR and main push without path or type filters",
    )
    require(
        workflow.get("permissions") == {"contents": "read"},
        "CI permissions must be contents: read",
    )
    jobs = workflow.get("jobs", {})
    require(
        isinstance(jobs, dict) and set(jobs) == {"validation"},
        "Expected validation job",
    )
    job = jobs["validation"]
    require(
        job.get("name") == "validation", "Required check name must remain validation"
    )
    require(job.get("runs-on") == "ubuntu-24.04", "Use GitHub-hosted ubuntu-24.04")
    require(
        job.get("timeout-minutes") == "15", "Validation job must have a 15 minute limit"
    )
    require("if" not in job, "Validation job must not be conditional")
    require("continue-on-error" not in job, "Validation job must not ignore failures")
    require("permissions" not in job, "Job must inherit read-only permissions")
    require("strategy" not in job, "Validation must report one stable check")
    require(
        "container" not in job, "Validation must use the selected hosted environment"
    )
    steps = job.get("steps", [])
    require(
        isinstance(steps, list) and len(steps) == 6, "Expected six validation steps"
    )
    for step in steps:
        require(isinstance(step, dict), "Each CI step must be a mapping")
        require("if" not in step, "Validation steps must not be conditional")
        require(
            "continue-on-error" not in step, "Validation steps must not ignore failures"
        )
    for step, action in zip(steps[:2], ("actions/checkout", "actions/setup-python")):
        require(
            re.fullmatch(re.escape(action) + r"@[0-9a-f]{40}", step.get("uses", "")),
            f"{action} must be pinned to a full commit SHA",
        )
    require(
        steps[0].get("with", {}).get("persist-credentials") == "false",
        "Checkout must not persist credentials",
    )
    require(
        steps[1].get("with", {}).get("python-version") == "3.14",
        "Use Python 3.14 for validation",
    )
    require(
        [step.get("run") for step in steps[2:]] == [INSTALL, *COMMANDS],
        "Install dependencies, then run lint, build, test in order",
    )


def validate_dependabot(config):
    require(config.get("version") == "2", "Dependabot configuration must use version 2")
    updates = config.get("updates", [])
    require(
        isinstance(updates, list) and len(updates) == 2, "Expected two update targets"
    )
    targets = set()
    for update in updates:
        require(isinstance(update, dict), "Each Dependabot target must be a mapping")
        targets.add((update.get("package-ecosystem"), update.get("directory")))
        require(
            update.get("schedule", {}).get("interval") == "weekly",
            "Dependabot must check weekly",
        )
        require("target-branch" not in update, "Dependabot must use the default branch")
        require(
            update.get("open-pull-requests-limit") == "5",
            "Allow five version update PRs per ecosystem",
        )
    require(
        targets == {("github-actions", "/"), ("pip", "/validation")},
        "Dependabot must cover Actions and validation Python dependencies",
    )


def test():
    validate_ci(load_mapping(ROOT / ".github" / "workflows" / "ci.yaml"))
    validate_dependabot(load_mapping(ROOT / ".github" / "dependabot.yaml"))
    print("PASS: CI and Dependabot policies", flush=True)
    run(
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "validation",
        "-p",
        "test_*.py",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "category", choices=(*CATEGORIES, "all"), nargs="?", default="all"
    )
    category = parser.parse_args(argv).category
    try:
        require(sys.version_info[:2] == (3, 14), "Use Python 3.14 for validation")
        for name in CATEGORIES if category == "all" else (category,):
            print(f"== {name} ==", flush=True)
            {"lint": lint, "build": build, "test": test}[name]()
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        yaml.YAMLError,
        py_compile.PyCompileError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
