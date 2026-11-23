"""Regression cases for unsafe CI changes, YAML parsing, and tooling compilation."""

import copy
import io
import py_compile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from validate import (
    ROOT,
    compile_sources,
    files,
    load_mapping,
    main,
    validate_ci,
    validate_dependabot,
)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load_mapping(ROOT / ".github/workflows/ci.yaml")

    def reject(self, mutate, message):
        workflow = copy.deepcopy(self.workflow)
        mutate(workflow)
        with self.assertRaisesRegex(ValueError, message):
            validate_ci(workflow)

    def test_main_push_and_pr_are_validated(self):
        validate_ci(self.workflow)

    def test_missing_pr_trigger_is_rejected(self):
        self.reject(lambda value: value["on"].pop("pull_request"), "every main PR")

    def test_path_filter_is_rejected(self):
        self.reject(
            lambda value: value["on"]["pull_request"].update(
                {"paths": ["validation/**"]}
            ),
            "without path or type filters",
        )

    def test_missing_synchronize_trigger_is_rejected(self):
        self.reject(
            lambda value: value["on"]["pull_request"].update({"types": ["opened"]}),
            "without path or type filters",
        )

    def test_privileged_pr_trigger_is_rejected(self):
        self.reject(
            lambda value: value["on"].update({"pull_request_target": {}}),
            "every main PR",
        )

    def test_write_permissions_are_rejected(self):
        self.reject(
            lambda value: value.update({"permissions": "write-all"}), "contents"
        )

    def test_job_permission_escalation_is_rejected(self):
        self.reject(
            lambda value: value["jobs"]["validation"].update(
                {"permissions": "write-all"}
            ),
            "inherit read-only",
        )

    def test_self_hosted_runner_is_rejected(self):
        self.reject(
            lambda value: value["jobs"]["validation"].update(
                {"runs-on": "self-hosted"}
            ),
            "GitHub-hosted",
        )

    def test_skipped_or_ignored_validation_is_rejected(self):
        for key in ("if", "continue-on-error"):
            with self.subTest(key=key):
                self.reject(
                    lambda value, key=key: value["jobs"]["validation"].update(
                        {key: "true"}
                    ),
                    "must not",
                )

    def test_renamed_required_check_is_rejected(self):
        self.reject(
            lambda value: value["jobs"]["validation"].update({"name": "renamed"}),
            "check name",
        )

    def test_unpinned_actions_are_rejected(self):
        for index, action in enumerate(("actions/checkout", "actions/setup-python")):
            with self.subTest(action=action):
                self.reject(
                    lambda value, index=index, action=action: value["jobs"][
                        "validation"
                    ]["steps"][index].update({"uses": f"{action}@v1"}),
                    "full commit SHA",
                )

    def test_dependabot_can_update_action_shas(self):
        self.workflow["jobs"]["validation"]["steps"][0]["uses"] = (
            "actions/checkout@" + "a" * 40
        )
        validate_ci(self.workflow)

    def test_checkout_credentials_are_not_retained(self):
        self.reject(
            lambda value: value["jobs"]["validation"]["steps"][0]["with"].update(
                {"persist-credentials": "true"}
            ),
            "persist credentials",
        )

    def test_reordered_categories_are_rejected(self):
        def reorder(value):
            steps = value["jobs"]["validation"]["steps"]
            steps[3], steps[4] = steps[4], steps[3]

        self.reject(reorder, "in order")

    def test_placeholder_build_is_rejected(self):
        self.reject(
            lambda value: value["jobs"]["validation"]["steps"][4].update(
                {"run": "echo success"}
            ),
            "in order",
        )

    def test_individual_step_cannot_skip_or_ignore_failure(self):
        for key in ("if", "continue-on-error"):
            with self.subTest(key=key):
                self.reject(
                    lambda value, key=key: value["jobs"]["validation"]["steps"][
                        4
                    ].update({key: "true"}),
                    "must not",
                )


class DependabotTests(unittest.TestCase):
    def setUp(self):
        self.config = load_mapping(ROOT / ".github/dependabot.yaml")

    def test_actions_and_python_are_covered(self):
        validate_dependabot(self.config)

    def test_wrong_python_directory_is_rejected(self):
        self.config["updates"][1]["directory"] = "/missing"
        with self.assertRaisesRegex(ValueError, "cover Actions"):
            validate_dependabot(self.config)

    def test_disabled_version_updates_are_rejected(self):
        self.config["updates"][0]["open-pull-requests-limit"] = "0"
        with self.assertRaisesRegex(ValueError, "five version update PRs"):
            validate_dependabot(self.config)

    def test_nondefault_branch_is_rejected(self):
        self.config["updates"][0]["target-branch"] = "other"
        with self.assertRaisesRegex(ValueError, "default branch"):
            validate_dependabot(self.config)


class SourceTests(unittest.TestCase):
    def test_yaml_keeps_github_on_as_a_string(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.yaml"
            path.write_text("on:\n  push: {}\n", encoding="utf-8")
            self.assertEqual(load_mapping(path), {"on": {"push": {}}})

    def test_duplicate_yaml_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.yaml"
            path.write_text(
                "jobs:\n  validation: {}\n  validation: {}\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "Duplicate YAML key: validation"):
                load_mapping(path)

    def test_untracked_sources_are_included_and_generated_trees_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".gitignore").write_text(".venv/\n.validation/\n", encoding="utf-8")
            (root / "new.py").write_text("value = 1\n", encoding="utf-8")
            for name in (".git", ".venv", ".validation"):
                (root / name).mkdir()
                (root / name / "ignored.py").write_text(
                    "invalid source", encoding="utf-8"
                )
            self.assertEqual(
                [path.name for path in files(root)], [".gitignore", "new.py"]
            )

    def test_build_produces_bytecode_and_rejects_syntax_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tool.py"
            source.write_text("value = 1\n", encoding="utf-8")
            output = root / "output"
            compile_sources([source], root, output)
            self.assertTrue((output / "tool.pyc").is_file())
            source.write_text("def broken(\n", encoding="utf-8")
            with self.assertRaises(py_compile.PyCompileError):
                compile_sources([source], root, output)

    def test_empty_build_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "No validation Python sources"):
            compile_sources([], ROOT, ROOT / ".validation")

    def test_all_stops_after_failure(self):
        with (
            patch("sys.stdout", new_callable=io.StringIO),
            patch("sys.stderr", new_callable=io.StringIO) as errors,
            patch("validate.lint", side_effect=ValueError("broken source")) as lint,
            patch("validate.build") as build,
            patch("validate.test") as test,
        ):
            self.assertEqual(main(["all"]), 1)
            lint.assert_called_once()
            build.assert_not_called()
            test.assert_not_called()
            self.assertIn("FAIL: broken source", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
