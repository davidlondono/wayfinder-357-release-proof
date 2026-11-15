import sys

from release_policy import index_error


with open(sys.argv[1], encoding="utf-8") as index_file:
    error = index_error(index_file.read())
if error:
    raise SystemExit(error)
print("Image index contains linux/amd64 and linux/arm64.")
