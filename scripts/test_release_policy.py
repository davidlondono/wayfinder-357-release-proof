import copy
import unittest

from release_policy import authorization_error, gates_error, index_error, reconcile


SHA = "a" * 40
HEAD = "b" * 40
REQUIRED = ["go", "postgres", "image-smoke"]
VALID_PR = {
    "merged": True,
    "base_ref": "main",
    "merge_commit_sha": SHA,
    "author": "github-actions[bot]",
    "head_sha": HEAD,
    "head_repo": "davidlondono/wayfinder-357-release-proof",
}
VALID_REVIEW = {
    "user": "davidlondono",
    "commit_id": HEAD,
    "state": "APPROVED",
    "submitted_at": "2026-11-15T12:00:00Z",
}
VALID_CHECKS = [
    {"name": name, "head_sha": SHA, "status": "completed", "conclusion": "success"}
    for name in REQUIRED
]


def plan(version="0.1.0", sha=SHA, digest="sha256:index-1", eligible=None):
    return {
        "version": version,
        "merge_sha": sha,
        "candidate_digest": digest,
        "tested_digest": digest,
        "assets": {"server_linux_amd64": "asset-1", "SHA256SUMS": "sum-1"},
        "eligible_stable": eligible or [{"version": version, "digest": digest}],
    }


class ReleasePolicyTest(unittest.TestCase):
    def test_valid_merge_authorizes_only_final_bot_head_approval(self):
        self.assertIsNone(authorization_error(
            VALID_PR, [VALID_REVIEW], SHA, "davidlondono", "davidlondono/wayfinder-357-release-proof"
        ))

    def test_direct_unauthorized_and_bypassed_merges_fail_closed(self):
        self.assertEqual(authorization_error(None, [], SHA, "davidlondono"), "no merged pull request")
        for field, value in (("author", "davidlondono"), ("base_ref", "feature")):
            pr = {**VALID_PR, field: value}
            self.assertIsNotNone(authorization_error(pr, [VALID_REVIEW], SHA, "davidlondono"))
        fork = {**VALID_PR, "head_repo": "attacker/fork"}
        self.assertIsNotNone(authorization_error(
            fork, [VALID_REVIEW], SHA, "davidlondono", "davidlondono/wayfinder-357-release-proof"
        ))
        self.assertIsNotNone(authorization_error(VALID_PR, [], SHA, "davidlondono"))

    def test_stale_and_dismissed_approvals_do_not_authorize(self):
        stale = {**VALID_REVIEW, "commit_id": "c" * 40}
        dismissed = {**VALID_REVIEW, "state": "DISMISSED", "submitted_at": "2026-11-15T13:00:00Z"}
        self.assertIsNotNone(authorization_error(VALID_PR, [stale], SHA, "davidlondono"))
        self.assertIsNotNone(authorization_error(VALID_PR, [VALID_REVIEW, dismissed], SHA, "davidlondono"))

    def test_merge_sha_must_match(self):
        self.assertIsNotNone(authorization_error(VALID_PR, [VALID_REVIEW], "c" * 40, "davidlondono"))

    def test_every_gate_must_succeed_on_exact_merge_sha(self):
        self.assertIsNone(gates_error(VALID_CHECKS, SHA, REQUIRED))
        self.assertEqual(gates_error(VALID_CHECKS[:-1], SHA, REQUIRED), "missing gate: image-smoke")
        failed = [{**VALID_CHECKS[0], "conclusion": "failure"}, *VALID_CHECKS[1:]]
        self.assertEqual(gates_error(failed, SHA, REQUIRED), "gate not successful on merge SHA: go")
        stale = [{**VALID_CHECKS[0], "head_sha": "c" * 40}, *VALID_CHECKS[1:]]
        self.assertEqual(gates_error(stale, SHA, REQUIRED), "gate not successful on merge SHA: go")
        skipped = [{**VALID_CHECKS[0], "conclusion": "skipped"}, *VALID_CHECKS[1:]]
        self.assertEqual(gates_error(skipped, SHA, REQUIRED), "gate not successful on merge SHA: go")

    def test_image_index_contains_both_platforms(self):
        index = (
            '{"manifests":['
            '{"platform":{"os":"linux","architecture":"amd64"}},'
            '{"platform":{"os":"linux","architecture":"arm64"}}]}'
        )
        self.assertIsNone(index_error(index))
        self.assertEqual(
            index_error('{"manifests":[{"platform":{"os":"linux","architecture":"amd64"}}]}'),
            "image index missing platforms: linux/arm64",
        )

    def test_successful_replay_is_idempotent(self):
        once = reconcile({}, plan())
        twice = reconcile(once, plan())
        self.assertEqual(once, twice)
        self.assertEqual(once["aliases"], {"latest": "0.1.0", "v0": "0.1.0"})

    def test_matching_tag_is_noop_but_conflict_fails_without_mutation(self):
        matching = {"version_tags": {"0.1.0": SHA}}
        self.assertEqual(reconcile(matching, plan())["version_tags"]["0.1.0"], SHA)
        conflict = {"version_tags": {"0.1.0": "c" * 40}}
        original = copy.deepcopy(conflict)
        with self.assertRaisesRegex(ValueError, "conflicting immutable version tag"):
            reconcile(conflict, plan())
        self.assertEqual(conflict, original)

    def test_asset_and_digest_conflicts_fail(self):
        with self.assertRaisesRegex(ValueError, "conflicting release asset"):
            reconcile({"assets": {"0.1.0": {"SHA256SUMS": "wrong"}}}, plan())
        with self.assertRaisesRegex(ValueError, "conflicting immutable image version tag"):
            reconcile({"image_tags": {"v0.1.0": "sha256:other"}}, plan())
        with self.assertRaisesRegex(ValueError, "tested digest"):
            reconcile({}, {**plan(), "tested_digest": "sha256:other"})

    def test_retry_after_each_publication_boundary(self):
        partial_states = [
            {"version_tags": {"0.1.0": SHA}},
            {
                "version_tags": {"0.1.0": SHA},
                "assets": {"0.1.0": {"server_linux_amd64": "asset-1"}},
            },
            {
                "version_tags": {"0.1.0": SHA},
                "assets": {"0.1.0": plan()["assets"]},
                "image_tags": {"v0.1.0": "sha256:index-1"},
            },
            {
                "version_tags": {"0.1.0": SHA},
                "assets": {"0.1.0": plan()["assets"]},
                "image_tags": {
                    "v0.1.0": "sha256:index-1",
                    "latest": "sha256:index-1",
                    "v0": "sha256:index-1",
                },
                "aliases": {"latest": "0.1.0", "v0": "0.1.0"},
            },
        ]
        for partial in partial_states:
            with self.subTest(partial=partial):
                complete = reconcile(partial, plan())
                self.assertEqual(complete["assets"]["0.1.0"], plan()["assets"])
                self.assertEqual(complete["image_tags"]["v0.1.0"], "sha256:index-1")
                self.assertEqual(complete["aliases"], {"latest": "0.1.0", "v0": "0.1.0"})

    def test_out_of_order_releases_never_move_aliases_back(self):
        eligible = [
            {"version": "0.1.0", "digest": "sha256:old"},
            {"version": "0.2.0", "digest": "sha256:new"},
        ]
        old = plan("0.1.0", "a" * 40, "sha256:old", eligible)
        new = plan("0.2.0", "c" * 40, "sha256:new", [
            {"version": "0.1.0", "digest": "sha256:old"},
            {"version": "0.2.0", "digest": "sha256:new"},
        ])
        state = reconcile({}, new)
        state = reconcile(state, old)
        self.assertEqual(state["aliases"]["latest"], "0.2.0")
        self.assertEqual(state["aliases"]["v0"], "0.2.0")

    def test_unknown_alias_fails_closed(self):
        state = {"aliases": {"latest": "0.9.0"}, "image_tags": {"latest": "sha256:unknown"}}
        with self.assertRaisesRegex(ValueError, "unverifiable current alias"):
            reconcile(state, plan())

    def test_prerelease_cannot_enter_stable_aliases(self):
        with self.assertRaisesRegex(ValueError, "not a stable SemVer"):
            reconcile({}, plan("0.2.0-rc.1"))


if __name__ == "__main__":
    unittest.main()
