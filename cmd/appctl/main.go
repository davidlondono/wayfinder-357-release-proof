package main

import (
	"fmt"
	"runtime"
)

var version = "dev"

func main() {
	fmt.Printf("%s %s/%s\n", version, runtime.GOOS, runtime.GOARCH)
}
