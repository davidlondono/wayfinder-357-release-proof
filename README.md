# PROTOTYPE: merge-authorized release publication

Throwaway proof for Wayfinder issue 357. This private repository and its GHCR
package are isolated from the template repository.

The prototype checks release-PR authority, exact-SHA gates, keyless signing,
immutable publication, GHCR digest promotion, retries, and alias monotonicity.

Local scenario model:

```sh
python3 -m unittest discover -s scripts -v
```

The workflow files and test fixtures are evidence only. Do not copy them into
the template until the release map is complete.
