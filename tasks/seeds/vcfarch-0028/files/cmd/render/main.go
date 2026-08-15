package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"vcfarch/architecture"
)

func main() {
	out := flag.String("out", "architecture.json", "output artifact")
	scenario := flag.String("scenario", "testdata/estate.json", "scenario fixture")
	snapshot := flag.String("snapshot", "testdata/compatibility-snapshot.json", "compatibility snapshot")
	flag.Parse()

	artifact, err := architecture.Build(*scenario, *snapshot)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	b, err := json.MarshalIndent(artifact, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	b = append(b, '\n')
	if err := os.WriteFile(*out, b, 0o644); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
