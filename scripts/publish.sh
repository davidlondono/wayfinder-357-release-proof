#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_TOKEN:?}"
: "${REPOSITORY:?}"
: "${VERSION:?}"
: "${MERGE_SHA:?}"
: "${IMAGE:?}"
: "${IMAGE_DIGEST:?}"

TAG="v$VERSION"
IDENTITY="https://github.com/$REPOSITORY/.github/workflows/publish.yml@refs/heads/main"
ISSUER="https://token.actions.githubusercontent.com"

die() {
	echo "$*" >&2
	exit 1
}

[[ "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || die "version must be stable SemVer"
[[ "$MERGE_SHA" =~ ^[a-f0-9]{40}$ ]] || die "merge SHA must be 40 lowercase hex characters"
[[ "$IMAGE_DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]] || die "candidate digest must be sha256"

tag_status=0
git ls-remote --exit-code origin "refs/tags/$TAG" >/dev/null 2>&1 || tag_status=$?
if [ "$tag_status" -eq 0 ]; then
	git fetch origin "refs/tags/$TAG:refs/tags/$TAG"
	[ "$(git cat-file -t "refs/tags/$TAG")" = "tag" ] || die "existing version tag is not annotated"
	[ "$(git rev-parse "refs/tags/$TAG^{}")" = "$MERGE_SHA" ] || die "conflicting version tag target"
elif [ "$tag_status" -eq 2 ]; then
	git -c user.name='github-actions[bot]' \
		-c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
		tag -a "$TAG" "$MERGE_SHA" -m "Release $TAG"
	git push origin "refs/tags/$TAG"
else
	die "could not inspect remote version tag"
fi

registry_tags=$(crane ls "$IMAGE")
if grep -Fxq "$TAG" <<< "$registry_tags"; then
	[ "$(crane digest "$IMAGE:$TAG")" = "$IMAGE_DIGEST" ] || die "conflicting immutable image version tag"
else
	crane tag "$IMAGE@$IMAGE_DIGEST" "$TAG"
fi
[ "$(crane digest "$IMAGE:$TAG")" = "$IMAGE_DIGEST" ] || die "image promotion changed index digest"
cosign verify --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" "$IMAGE@$IMAGE_DIGEST" >/dev/null
cosign verify-attestation --type slsaprovenance --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" "$IMAGE@$IMAGE_DIGEST" >/dev/null

if ! gh api "repos/$REPOSITORY/releases/tags/$TAG" >/dev/null 2>&1; then
	gh release create "$TAG" --repo "$REPOSITORY" --verify-tag \
		--title "$TAG" --notes "Prototype release for Wayfinder issue 357."
fi

asset_names() {
	gh api "repos/$REPOSITORY/releases/tags/$TAG" --jq '[.assets[].name]'
}

has_asset() {
	asset_names | jq -e --arg name "$1" 'index($name) != null' >/dev/null
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

for artifact in dist/server_* dist/appctl_* dist/release.spdx.json dist/SHA256SUMS; do
	bundle="$artifact.sigstore.json"
	if has_asset "$(basename "$bundle")"; then
		gh release download "$TAG" --repo "$REPOSITORY" --pattern "$(basename "$bundle")" --dir "$tmp" >/dev/null
		mv "$tmp/$(basename "$bundle")" "$bundle"
	else
		cosign sign-blob --yes --bundle "$bundle" "$artifact" >/dev/null
	fi
	cosign verify-blob --bundle "$bundle" --certificate-identity "$IDENTITY" \
		--certificate-oidc-issuer "$ISSUER" "$artifact" >/dev/null
done

(cd dist && sha256sum --check SHA256SUMS)

for file in dist/server_* dist/appctl_* dist/release.spdx.json dist/SHA256SUMS dist/*.sigstore.json; do
	name=$(basename "$file")
	if has_asset "$name"; then
		gh release download "$TAG" --repo "$REPOSITORY" --pattern "$name" --dir "$tmp" >/dev/null
		if [[ "$name" == *.sigstore.json ]]; then
			artifact="dist/${name%.sigstore.json}"
			cosign verify-blob --bundle "$tmp/$name" --certificate-identity "$IDENTITY" \
				--certificate-oidc-issuer "$ISSUER" "$artifact" >/dev/null || die "conflicting signature bundle: $name"
		else
			cmp --silent "$file" "$tmp/$name" || die "conflicting release asset: $name"
		fi
	else
		gh release upload "$TAG" "$file" --repo "$REPOSITORY"
	fi
done

for artifact in dist/server_* dist/appctl_* dist/release.spdx.json dist/SHA256SUMS; do
	name=$(basename "$artifact")
	gh release download "$TAG" --repo "$REPOSITORY" --pattern "$name" --dir "$tmp" >/dev/null
	cmp --silent "$artifact" "$tmp/$name" || die "published release asset differs: $name"
	gh attestation verify "$artifact" --repo "$REPOSITORY" \
		--signer-workflow "$REPOSITORY/.github/workflows/publish.yml" \
		--source-digest "$MERGE_SHA" --source-ref refs/heads/main
done

gh attestation verify "oci://$IMAGE@$IMAGE_DIGEST" --repo "$REPOSITORY" \
	--signer-workflow "$REPOSITORY/.github/workflows/publish.yml" \
	--source-digest "$MERGE_SHA" --source-ref refs/heads/main --bundle-from-oci

release_tags=$(gh api --paginate "repos/$REPOSITORY/releases?per_page=100" \
	--jq '.[] | select(.draft == false and .prerelease == false) | .tag_name')
versions=()
declare -A version_digests
while IFS= read -r release_tag; do
	[[ "$release_tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || continue
	release_version="${release_tag#v}"
	release_digest=$(crane digest "$IMAGE:$release_tag")
	cosign verify --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" \
		"$IMAGE@$release_digest" >/dev/null
	versions+=("$release_version")
	version_digests["$release_version"]="$release_digest"
done <<< "$release_tags"
[ "${#versions[@]}" -gt 0 ] || die "no verified stable releases"

latest_version=$(printf '%s\n' "${versions[@]}" | sort -V | tail -n 1)
major="${VERSION%%.*}"
major_version=$(printf '%s\n' "${versions[@]}" | awk -F. -v major="$major" '$1 == major' | sort -V | tail -n 1)
[ -n "$major_version" ] || die "no verified stable release for major $major"

ensure_alias() {
	alias="$1"
	target_version="$2"
	current_version=""
	registry_tags=$(crane ls "$IMAGE")
	if grep -Fxq "$alias" <<< "$registry_tags"; then
		current_digest=$(crane digest "$IMAGE:$alias")
		for candidate in "${versions[@]}"; do
			if [ "${version_digests[$candidate]}" = "$current_digest" ]; then
				current_version="$candidate"
				break
			fi
		done
		[ -n "$current_version" ] || die "unverifiable current alias: $alias"
	fi

	if [ "$current_version" != "$target_version" ]; then
		if [ -n "$current_version" ] && [ "$(printf '%s\n' "$current_version" "$target_version" | sort -V | tail -n 1)" != "$target_version" ]; then
			echo "Keeping $alias at newer stable version $current_version."
			return
		fi
		crane tag "$IMAGE@${version_digests[$target_version]}" "$alias"
	fi
	[ "$(crane digest "$IMAGE:$alias")" = "${version_digests[$target_version]}" ] || die "alias digest mismatch: $alias"
}

ensure_alias latest "$latest_version"
ensure_alias "v$major" "$major_version"
