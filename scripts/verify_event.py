import json
import os
from urllib.request import Request, urlopen

from release_policy import _version, authorization_error


def api(path):
    request = Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request) as response:
        return json.load(response)


def output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    line = f"{name}={value}\n"
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
    else:
        print(line, end="")


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    merge_sha = os.environ["GITHUB_SHA"]
    with open("VERSION", encoding="utf-8") as version_file:
        version = version_file.read().strip()
    try:
        _version(version)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    output("version", version)
    associated = api(f"/repos/{repository}/commits/{merge_sha}/pulls?per_page=100")
    summaries = [
        pull for pull in associated
        if pull.get("base", {}).get("ref") == "main"
        and pull.get("merge_commit_sha") == merge_sha
    ]
    if not summaries:
        print("No release PR is associated with this main commit; publication skipped.")
        output("authorized", "false")
        return
    if len(summaries) != 1:
        raise SystemExit("Multiple release PRs match the main commit.")

    summary = summaries[0]
    number = summary["number"]
    pull = api(f"/repos/{repository}/pulls/{number}")
    reviews = api(f"/repos/{repository}/pulls/{number}/reviews?per_page=100")
    evidence = {
        "merged": pull.get("merged_at") is not None,
        "base_ref": pull.get("base", {}).get("ref"),
        "merge_commit_sha": pull.get("merge_commit_sha"),
        "author": pull.get("user", {}).get("login"),
        "head_sha": pull.get("head", {}).get("sha"),
        "head_repo": (pull.get("head", {}).get("repo") or {}).get("full_name"),
    }
    error = authorization_error(
        evidence,
        [
            {
                "user": review.get("user", {}).get("login"),
                "commit_id": review.get("commit_id"),
                "state": review.get("state"),
                "submitted_at": review.get("submitted_at") or "",
            }
            for review in reviews
        ],
        merge_sha,
        "davidlondono",
        repository,
    )
    if error:
        raise SystemExit(f"Release authorization failed: {error}")

    print(f"Authorized merged release PR #{number} for exact SHA {merge_sha}.")
    output("authorized", "true")
    output("release_pr", str(number))


if __name__ == "__main__":
    main()
