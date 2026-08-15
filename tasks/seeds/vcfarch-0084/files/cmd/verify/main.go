package main

import (
	"fmt"
	"os"

	"vcfarch/internal/verifier"
)

func main() {
	artifact := "architecture.json"
	if len(os.Args) > 2 {
		fmt.Fprintln(os.Stderr, "usage: verify [architecture.json]")
		os.Exit(2)
	}
	if len(os.Args) == 2 {
		artifact = os.Args[1]
	}
	if err := verifier.VerifyPaths(
		artifact,
		"specifications/vcf-installer/vcf-installer-openapi.json",
		"testdata/migration-plan.schema.json",
		"testdata/estate.json",
		"testdata/compatibility-snapshot.json",
	); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println("architecture verified")
}
