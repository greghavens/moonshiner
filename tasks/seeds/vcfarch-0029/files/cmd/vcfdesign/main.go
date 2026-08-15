package main

import (
	"fmt"
	"os"

	"vcfarch"
)

func main() {
	in, err := vcfarch.LoadInputs(
		"fixtures/requirements.json",
		"fixtures/estate.json",
		"fixtures/compatibility-snapshot.json",
	)
	if err != nil {
		fail(err)
	}
	sddc, architecture, err := vcfarch.BuildGreenfield(in)
	if err != nil {
		fail(err)
	}
	plan, err := vcfarch.BuildMigrationPlan(in)
	if err != nil {
		fail(err)
	}
	if err := vcfarch.WriteArtifacts("out", sddc, architecture, plan); err != nil {
		fail(err)
	}
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
