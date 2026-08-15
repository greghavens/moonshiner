package main

import (
	"fmt"
	"os"

	"vcfarch-0058/design"
)

func main() {
	requirements := mustRead("fixtures/design-requirements.json")
	estate := mustRead("fixtures/estate.json")
	compatibility := mustRead("compatibility/compatibility-snapshot.json")

	artifacts, err := design.Build(requirements, estate, compatibility)
	if err != nil {
		fatal(err)
	}
	if err := design.Write("artifacts", artifacts); err != nil {
		fatal(err)
	}
}

func mustRead(path string) []byte {
	data, err := os.ReadFile(path)
	if err != nil {
		fatal(fmt.Errorf("read %s: %w", path, err))
	}
	return data
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
