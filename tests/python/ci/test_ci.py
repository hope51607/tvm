# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import subprocess
import sys
import json
import textwrap
import pytest
import tvm.testing

from test_utils import REPO_ROOT


class TempGit:
    def __init__(self, cwd):
        self.cwd = cwd

    def run(self, *args):
        proc = subprocess.run(["git"] + list(args), cwd=self.cwd)
        if proc.returncode != 0:
            raise RuntimeError(f"git command failed: '{args}'")


def test_cc_reviewers(tmpdir_factory):
    reviewers_script = REPO_ROOT / "tests" / "scripts" / "github_cc_reviewers.py"

    def run(pr_body, requested_reviewers, existing_review_users, expected_reviewers):
        git = TempGit(tmpdir_factory.mktemp("tmp_git_dir"))
        git.run("init")
        git.run("checkout", "-b", "main")
        git.run("remote", "add", "origin", "https://github.com/apache/tvm.git")
        reviews = [{"user": {"login": r}} for r in existing_review_users]
        requested_reviewers = [{"login": r} for r in requested_reviewers]
        proc = subprocess.run(
            [str(reviewers_script), "--dry-run", "--testing-reviews-json", json.dumps(reviews)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PR": json.dumps(
                    {"number": 1, "body": pr_body, "requested_reviewers": requested_reviewers}
                )
            },
            encoding="utf-8",
            cwd=git.cwd,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Process failed:\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}")

        assert f"After filtering existing reviewers, adding: {expected_reviewers}" in proc.stdout

    run(pr_body="abc", requested_reviewers=[], existing_review_users=[], expected_reviewers=[])
    run(
        pr_body="cc @abc",
        requested_reviewers=[],
        existing_review_users=[],
        expected_reviewers=["abc"],
    )
    run(pr_body="cc @", requested_reviewers=[], existing_review_users=[], expected_reviewers=[])
    run(
        pr_body="cc @abc @def",
        requested_reviewers=[],
        existing_review_users=[],
        expected_reviewers=["abc", "def"],
    )
    run(
        pr_body="some text cc @abc @def something else",
        requested_reviewers=[],
        existing_review_users=[],
        expected_reviewers=["abc", "def"],
    )
    run(
        pr_body="some text cc @abc @def something else\n\n another cc @zzz z",
        requested_reviewers=[],
        existing_review_users=[],
        expected_reviewers=["abc", "def", "zzz"],
    )
    run(
        pr_body="some text cc @abc @def something else\n\n another cc @zzz z",
        requested_reviewers=["abc"],
        existing_review_users=[],
        expected_reviewers=["def", "zzz"],
    )
    run(
        pr_body="some text cc @abc @def something else\n\n another cc @zzz z",
        requested_reviewers=["abc"],
        existing_review_users=["abc"],
        expected_reviewers=["def", "zzz"],
    )
    run(
        pr_body="some text cc @abc @def something else\n\n another cc @zzz z",
        requested_reviewers=[],
        existing_review_users=["abc"],
        expected_reviewers=["def", "zzz"],
    )


def test_update_branch(tmpdir_factory):
    update_script = REPO_ROOT / "tests" / "scripts" / "update_branch.py"

    def run(statuses, expected_rc, expected_output):
        git = TempGit(tmpdir_factory.mktemp("tmp_git_dir"))
        git.run("init")
        git.run("checkout", "-b", "main")
        git.run("remote", "add", "origin", "https://github.com/apache/tvm.git")
        commit = {
            "statusCheckRollup": {"contexts": {"nodes": statuses}},
            "oid": "123",
            "messageHeadline": "hello",
        }
        data = {
            "data": {
                "repository": {
                    "defaultBranchRef": {"target": {"history": {"edges": [], "nodes": [commit]}}}
                }
            }
        }
        proc = subprocess.run(
            [str(update_script), "--dry-run", "--testonly-json", json.dumps(data)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            cwd=git.cwd,
        )

        if proc.returncode != expected_rc:
            raise RuntimeError(
                f"Wrong return code:\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
            )

        if expected_output not in proc.stdout:
            raise RuntimeError(
                f"Missing {expected_output}:\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
            )

    # Missing expected tvm-ci/branch test
    run(
        statuses=[
            {
                "context": "test",
                "state": "SUCCESS",
            }
        ],
        expected_rc=1,
        expected_output="No good commits found in the last 1 commits",
    )

    # Only has the right passing test
    run(
        statuses=[
            {
                "context": "tvm-ci/branch",
                "state": "SUCCESS",
            }
        ],
        expected_rc=0,
        expected_output="Found last good commit: 123: hello",
    )

    # Check with many statuses
    run(
        statuses=[
            {
                "context": "tvm-ci/branch",
                "state": "SUCCESS",
            },
            {
                "context": "tvm-ci/branch2",
                "state": "SUCCESS",
            },
            {
                "context": "tvm-ci/branch3",
                "state": "FAILED",
            },
        ],
        expected_rc=1,
        expected_output="No good commits found in the last 1 commits",
    )
    run(
        statuses=[
            {
                "context": "tvm-ci/branch",
                "state": "SUCCESS",
            },
            {
                "context": "tvm-ci/branch2",
                "state": "SUCCESS",
            },
            {
                "context": "tvm-ci/branch3",
                "state": "SUCCESS",
            },
        ],
        expected_rc=0,
        expected_output="Found last good commit: 123: hello",
    )


def test_skip_ci(tmpdir_factory):
    skip_ci_script = REPO_ROOT / "tests" / "scripts" / "git_skip_ci.py"

    def test(commands, should_skip, pr_title, why):
        git = TempGit(tmpdir_factory.mktemp("tmp_git_dir"))
        # Jenkins git is too old and doesn't have 'git init --initial-branch'
        git.run("init")
        git.run("checkout", "-b", "main")
        git.run("remote", "add", "origin", "https://github.com/apache/tvm.git")
        git.run("config", "user.name", "ci")
        git.run("config", "user.email", "email@example.com")
        git.run("commit", "--allow-empty", "--message", "base commit")
        for command in commands:
            git.run(*command)
        pr_number = "1234"
        proc = subprocess.run(
            [str(skip_ci_script), "--pr", pr_number, "--pr-title", pr_title], cwd=git.cwd
        )
        expected = 0 if should_skip else 1
        assert proc.returncode == expected, why

    test(
        commands=[],
        should_skip=False,
        pr_title="[skip ci] test",
        why="ci should not be skipped",
    )

    test(
        commands=[
            ["commit", "--allow-empty", "--message", "[skip ci] commit 1"],
        ],
        should_skip=False,
        pr_title="[skip ci] test",
        why="ci should not be skipped on main",
    )

    test(
        commands=[
            ["checkout", "-b", "some_new_branch"],
            ["commit", "--allow-empty", "--message", "[skip ci] commit 1"],
        ],
        should_skip=True,
        pr_title="[skip ci] test",
        why="ci should be skipped on a branch with [skip ci] in the last commit",
    )

    test(
        commands=[
            ["checkout", "-b", "some_new_branch"],
            ["commit", "--allow-empty", "--message", "[skip ci] commit 1"],
        ],
        should_skip=False,
        pr_title="[no skip ci] test",
        why="ci should not be skipped on a branch with [skip ci] in the last commit but not the PR title",
    )

    test(
        commands=[
            ["checkout", "-b", "some_new_branch"],
            ["commit", "--allow-empty", "--message", "[skip ci] commit 1"],
            ["commit", "--allow-empty", "--message", "commit 2"],
        ],
        should_skip=True,
        pr_title="[skip ci] test",
        why="ci should not be skipped with [skip ci] in the PR title",
    )

    test(
        commands=[
            ["checkout", "-b", "some_new_branch"],
            ["commit", "--allow-empty", "--message", "[skip ci] commit 1"],
            ["commit", "--allow-empty", "--message", "commit 2"],
        ],
        should_skip=True,
        pr_title="[skip ci] test",
        why="ci should not be skipped with [skip ci] in the PR title",
    )

    test(
        commands=[
            ["checkout", "-b", "some_new_branch"],
            ["commit", "--allow-empty", "--message", "commit 1"],
            ["commit", "--allow-empty", "--message", "commit 2"],
            ["commit", "--allow-empty", "--message", "commit 3"],
            ["commit", "--allow-empty", "--message", "commit 4"],
        ],
        should_skip=True,
        pr_title="[skip ci] test",
        why="ci should not be skipped with [skip ci] in the PR title",
    )


def test_skip_globs(tmpdir_factory):
    script = REPO_ROOT / "tests" / "scripts" / "git_skip_ci_globs.py"

    def run(files, should_skip):
        git = TempGit(tmpdir_factory.mktemp("tmp_git_dir"))
        # Jenkins git is too old and doesn't have 'git init --initial-branch'
        git.run("init")
        git.run("checkout", "-b", "main")
        git.run("remote", "add", "origin", "https://github.com/apache/tvm.git")

        proc = subprocess.run(
            [
                str(script),
                "--files",
                ",".join(files),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            cwd=git.cwd,
        )

        if should_skip:
            assert proc.returncode == 0
        else:
            assert proc.returncode == 1

    run([], should_skip=True)
    run(["README.md"], should_skip=True)
    run(["test.c"], should_skip=False)
    run(["test.c", "README.md"], should_skip=False)
    run(["src/autotvm/feature_visitor.cc", "README.md"], should_skip=False)
    run([".asf.yaml", "docs/README.md"], should_skip=True)


def test_ping_reviewers(tmpdir_factory):
    reviewers_script = REPO_ROOT / "tests" / "scripts" / "ping_reviewers.py"

    def run(pr, check):
        git = TempGit(tmpdir_factory.mktemp("tmp_git_dir"))
        # Jenkins git is too old and doesn't have 'git init --initial-branch'
        git.run("init")
        git.run("checkout", "-b", "main")
        git.run("remote", "add", "origin", "https://github.com/apache/tvm.git")

        data = {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [pr],
                        "edges": [],
                    }
                }
            }
        }
        proc = subprocess.run(
            [
                str(reviewers_script),
                "--dry-run",
                "--wait-time-minutes",
                "1",
                "--cutoff-pr-number",
                "5",
                "--allowlist",
                "user",
                "--pr-json",
                json.dumps(data),
                "--now",
                "2022-01-26T17:54:19Z",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            cwd=git.cwd,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Process failed:\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}")

        assert check in proc.stdout

    def all_time_keys(time):
        return {
            "updatedAt": time,
            "lastEditedAt": time,
            "createdAt": time,
            "publishedAt": time,
        }

    run(
        {
            "isDraft": True,
            "number": 2,
        },
        "Checking 0 of 1 fetched",
    )

    run(
        {
            "isDraft": False,
            "number": 2,
        },
        "Checking 0 of 1 fetched",
    )

    run(
        {
            "number": 123,
            "url": "https://github.com/apache/tvm/pull/123",
            "body": "cc @someone",
            "isDraft": False,
            "author": {"login": "user"},
            "reviews": {"nodes": []},
            **all_time_keys("2022-01-18T17:54:19Z"),
            "comments": {"nodes": []},
        },
        "Pinging reviewers ['someone'] on https://github.com/apache/tvm/pull/123",
    )

    # Check allowlist functionality
    run(
        {
            "number": 123,
            "url": "https://github.com/apache/tvm/pull/123",
            "body": "cc @someone",
            "isDraft": False,
            "author": {"login": "user2"},
            "reviews": {"nodes": []},
            **all_time_keys("2022-01-18T17:54:19Z"),
            "comments": {
                "nodes": [
                    {**all_time_keys("2022-01-19T17:54:19Z"), "bodyText": "abc"},
                ]
            },
        },
        "Checking 0 of 1 fetched",
    )

    # Old comment, ping
    run(
        {
            "number": 123,
            "url": "https://github.com/apache/tvm/pull/123",
            "body": "cc @someone",
            "isDraft": False,
            "author": {"login": "user"},
            "reviews": {"nodes": []},
            **all_time_keys("2022-01-18T17:54:19Z"),
            "comments": {
                "nodes": [
                    {
                        **all_time_keys("2022-01-18T17:54:19Z"),
                        "bodyText": "abc",
                    },
                ]
            },
        },
        "Pinging reviewers ['someone'] on https://github.com/apache/tvm/pull/123",
    )

    # New comment, don't ping
    run(
        {
            "number": 123,
            "url": "https://github.com/apache/tvm/pull/123",
            "body": "cc @someone",
            "isDraft": False,
            "author": {"login": "user"},
            "reviews": {"nodes": []},
            **all_time_keys("2022-01-18T17:54:19Z"),
            "comments": {
                "nodes": [
                    {**all_time_keys("2022-01-27T17:54:19Z"), "bodyText": "abc"},
                ]
            },
        },
        "Not pinging PR 123",
    )


def assert_in(needle: str, haystack: str):
    if needle not in haystack:
        raise AssertionError(f"item not found:\n{needle}\nin:\n{haystack}")


def test_github_tag_teams(tmpdir_factory):
    tag_script = REPO_ROOT / "tests" / "scripts" / "github_tag_teams.py"

    def run(type, data, check):
        git = TempGit(tmpdir_factory.mktemp("tmp_git_dir"))
        git.run("init")
        git.run("checkout", "-b", "main")
        git.run("remote", "add", "origin", "https://github.com/apache/tvm.git")

        issue_body = """
        some text
        [temporary] opt-in: @person5

        - something: @person1 @person2
        - something3: @person1 @person2 @SOME1-ONE-
        - something else @person1 @person2
        - something else2: @person1 @person2
        - something-else @person1 @person2
        """
        comment1 = """
        another thing: @person3
        another-thing @person3
        """
        comment2 = """
        something @person4
        @person5
        """
        teams = {
            "data": {
                "repository": {
                    "issue": {
                        "body": issue_body,
                        "comments": {"nodes": [{"body": comment1}, {"body": comment2}]},
                    }
                }
            }
        }
        env = {
            type: json.dumps(data),
        }
        proc = subprocess.run(
            [
                str(tag_script),
                "--dry-run",
                "--team-issue-json",
                json.dumps(teams),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            cwd=git.cwd,
            env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Process failed:\nstdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}")

        assert_in(check, proc.stdout)

    run(
        "ISSUE",
        {
            "title": "A title",
            "number": 1234,
            "user": {
                "login": "person5",
            },
            "labels": [{"name": "abc"}],
            "body": textwrap.dedent(
                """
            hello
            """.strip()
            ),
        },
        "No one to cc, exiting",
    )

    run(
        "ISSUE",
        {
            "title": "A title",
            "number": 1234,
            "user": {
                "login": "person5",
            },
            "labels": [{"name": "abc"}],
            "body": textwrap.dedent(
                """
            hello

            cc @test
            """.strip()
            ),
        },
        "No one to cc, exiting",
    )

    run(
        type="ISSUE",
        data={
            "title": "A title",
            "number": 1234,
            "user": {
                "login": "person5",
            },
            "labels": [{"name": "something"}],
            "body": textwrap.dedent(
                """
                hello

                something"""
            ),
        },
        check="would have updated issues/1234 with {'body': '\\nhello\\n\\nsomething\\n\\ncc @person1 @person2 @person4'}",
    )

    run(
        type="ISSUE",
        data={
            "title": "A title",
            "number": 1234,
            "user": {
                "login": "person5",
            },
            "labels": [{"name": "something"}],
            "body": textwrap.dedent(
                """
                hello

                cc @person1 @person2 @person4"""
            ),
        },
        check="No one to cc, exiting",
    )

    run(
        type="ISSUE",
        data={
            "title": "[something] A title",
            "number": 1234,
            "user": {
                "login": "person5",
            },
            "labels": [{"name": "something2"}],
            "body": textwrap.dedent(
                """
                hello

                something"""
            ),
        },
        check="would have updated issues/1234 with {'body': '\\nhello\\n\\nsomething\\n\\ncc @person1 @person2 @person4'}",
    )

    run(
        type="ISSUE",
        data={
            "title": "[something] A title",
            "number": 1234,
            "user": {
                "login": "person5",
            },
            "labels": [{"name": "something2"}],
            "body": textwrap.dedent(
                """
                hello

                cc @person1 @person2 @person4"""
            ),
        },
        check="No one to cc, exiting",
    )

    run(
        type="PR",
        data={
            "title": "[something] A title",
            "number": 1234,
            "draft": False,
            "user": {
                "login": "person5",
            },
            "labels": [{"name": "something2"}],
            "body": textwrap.dedent(
                """
                hello

                cc @person1 @person2 @person4"""
            ),
        },
        check="No one to cc, exiting",
    )

    run(
        type="PR",
        data={
            "title": "[something] A title",
            "number": 1234,
            "draft": True,
            "user": {
                "login": "person5",
            },
            "labels": [{"name": "something2"}],
            "body": textwrap.dedent(
                """
                hello

                cc @person1 @person2 @person4"""
            ),
        },
        check="Terminating since 1234 is a draft",
    )

    run(
        type="ISSUE",
        data={
            "title": "[something] A title",
            "number": 1234,
            "user": {
                "login": "person5",
            },
            "labels": [{"name": "something2"}],
            "body": textwrap.dedent(
                """
                `mold` and `lld` can be a much faster alternative to `ld` from gcc. We should modify our CMakeLists.txt to detect and use these when possible. cc @person1

                cc @person4
                """
            ),
        },
        check="would have updated issues/1234 with {'body': '\\n`mold` and `lld` can be a much faster alternative to `ld` from gcc. We should modify our CMakeLists.txt to detect and use these when possible. cc @person1\\n\\ncc @person2 @person4\\n'}",
    )

    run(
        type="ISSUE",
        data={
            "title": "[something3] A title",
            "number": 1234,
            "user": {
                "login": "person5",
            },
            "labels": [{"name": "something2"}],
            "body": "@person2 @SOME1-ONE-",
        },
        check="Dry run, would have updated issues/1234 with {'body': '@person2 @SOME1-ONE-\\n\\ncc @person1'}",
    )

    run(
        type="ISSUE",
        data={
            "title": "[] A title",
            "number": 1234,
            "user": {
                "login": "person5",
            },
            "labels": [],
            "body": "@person2 @SOME1-ONE-",
        },
        check="No one to cc, exiting",
    )


if __name__ == "__main__":
    tvm.testing.main()
