package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"vcfarch/architecture"
)

func main() {
	estatePath := flag.String("estate", "fixtures/estate.json", "estate fixture")
	snapshotPath := flag.String("snapshot", "authority/compatibility-snapshot.json", "compatibility snapshot")
	outDir := flag.String("out", "out", "output directory")
	flag.Parse()

	var estate architecture.Estate
	mustDecode(*estatePath, &estate)
	var snapshot architecture.CompatibilitySnapshot
	mustDecode(*snapshotPath, &snapshot)

	design, err := architecture.Build(estate, snapshot)
	if err != nil {
		fatal(err)
	}
	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		fatal(err)
	}
	mustWrite(filepath.Join(*outDir, "sddc-spec.json"), design.SddcSpec)
	mustWrite(filepath.Join(*outDir, "edge-design.json"), design.EdgeDesign)
	mustWrite(filepath.Join(*outDir, "migration-plan.json"), design.MigrationPlan)
}

func mustDecode(path string, dst any) {
	b, err := os.ReadFile(path)
	if err != nil {
		fatal(err)
	}
	if err := json.Unmarshal(b, dst); err != nil {
		fatal(fmt.Errorf("decode %s: %w", path, err))
	}
}

func mustWrite(path string, value any) {
	b, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		fatal(err)
	}
	b = append(b, '\n')
	if err := os.WriteFile(path, b, 0o644); err != nil {
		fatal(err)
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
