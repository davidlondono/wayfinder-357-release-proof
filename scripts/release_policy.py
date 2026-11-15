"""Throwaway release authorization and reconciliation model for issue 357."""

from copy import deepcopy
import json
import re


def authorization_error(pr, reviews, merge_sha, authority, expected_repo=None):
    if not pr or pr.get("merged") is not True:
        return "no merged pull request"
    if pr.get("base_ref") != "main":
        return "wrong base branch"
    if pr.get("merge_commit_sha") != merge_sha:
        return "merge SHA mismatch"
    if pr.get("author") != "github-actions[bot]":
        return "release PR was not authored by automation"
    if expected_repo and pr.get("head_repo") != expected_repo:
        return "release PR head is outside the isolated repository"
    head_sha = pr.get("head_sha")
    if not head_sha:
        return "missing release PR head SHA"

    approvals = [
        review for review in reviews
        if review.get("user") == authority and review.get("commit_id") == head_sha
    ]
    if not approvals:
        return "no approval for final release PR head"
    latest = max(approvals, key=lambda review: review.get("submitted_at", ""))
    if latest.get("state") != "APPROVED":
        return "latest review of final release PR head is not approval"
    return None


def gates_error(checks, merge_sha, required):
    for name in required:
        matches = [check for check in checks if check.get("name") == name]
        if not matches:
            return f"missing gate: {name}"
        if not any(
            check.get("head_sha") == merge_sha
            and check.get("status") == "completed"
            and check.get("conclusion") == "success"
            for check in matches
        ):
            return f"gate not successful on merge SHA: {name}"
    return None


def index_error(raw, expected=("linux/amd64", "linux/arm64")):
    try:
        manifest = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return "invalid image index JSON"
    platforms = {
        f"{item.get('platform', {}).get('os')}/{item.get('platform', {}).get('architecture')}"
        for item in manifest.get("manifests", [])
    }
    if not set(expected).issubset(platforms):
        return f"image index missing platforms: {', '.join(sorted(set(expected) - platforms))}"
    return None


def _version(value):
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value)
    if not match:
        raise ValueError(f"not a stable SemVer version: {value}")
    return tuple(map(int, match.groups()))


def reconcile(state, release):
    """Return a safe next state; raise before changing input on any conflict."""
    result = deepcopy(state)
    version = release["version"]
    _version(version)
    sha = release["merge_sha"]
    digest = release["tested_digest"]
    if not digest or release.get("candidate_digest") != digest:
        raise ValueError("tested digest does not match candidate index")

    version_tag = result.setdefault("version_tags", {}).get(version)
    if version_tag is not None and version_tag != sha:
        raise ValueError("conflicting immutable version tag")

    current_assets = result.setdefault("assets", {}).get(version, {})
    for name, checksum in release["assets"].items():
        existing = current_assets.get(name)
        if existing is not None and existing != checksum:
            raise ValueError(f"conflicting release asset: {name}")

    image_tags = result.setdefault("image_tags", {})
    image_version_tag = f"v{version}"
    existing_digest = image_tags.get(image_version_tag)
    if existing_digest is not None and existing_digest != digest:
        raise ValueError("conflicting immutable image version tag")

    eligible = {item["version"]: item["digest"] for item in release["eligible_stable"]}
    for item_version, item_digest in eligible.items():
        _version(item_version)
        if not item_digest:
            raise ValueError("eligible release has no verifiable digest")

    desired_latest = max(eligible, key=_version)
    desired_major = max(
        (item_version for item_version in eligible if item_version.split(".")[0] == version.split(".")[0]),
        key=_version,
    )
    for alias, desired in (("latest", desired_latest), (f"v{version.split('.')[0]}", desired_major)):
        current = result.setdefault("aliases", {}).get(alias)
        if current is not None:
            if current not in eligible:
                raise ValueError(f"unverifiable current alias: {alias}")
            if image_tags.get(alias) != eligible[current]:
                raise ValueError(f"alias digest does not match current version: {alias}")
        if current is None or _version(current) < _version(desired):
            result["aliases"][alias] = desired
            image_tags[alias] = eligible[desired]

    result["version_tags"][version] = sha
    result["assets"].setdefault(version, {}).update(release["assets"])
    image_tags[image_version_tag] = digest
    return result
