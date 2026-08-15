package main

import (
	"log"

	"vcfdesign"
)

func main() {
	artifact, err := vcfdesign.Build(
		"testdata/design-requirements.json",
		"testdata/estate.json",
		"testdata/compatibility-snapshot.json",
	)
	if err != nil {
		log.Fatal(err)
	}
	if err := vcfdesign.WriteArtifact("architecture.json", artifact); err != nil {
		log.Fatal(err)
	}
}
