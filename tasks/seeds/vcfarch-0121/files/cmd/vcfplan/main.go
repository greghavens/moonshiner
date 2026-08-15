package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"vcfplan/planner"
)

func readJSON(path string, value any) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(b, value); err != nil {
		return fmt.Errorf("decode %s: %w", path, err)
	}
	return nil
}

func main() {
	inventoryPath := flag.String("inventory", "testdata/estate.json", "estate inventory")
	compatPath := flag.String("compat", "testdata/compatibility-snapshot.json", "pinned compatibility snapshot")
	outPath := flag.String("out", "architecture/plan.json", "output plan")
	flag.Parse()

	var inventory planner.Inventory
	var snapshot planner.Snapshot
	if err := readJSON(*inventoryPath, &inventory); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := readJSON(*compatPath, &snapshot); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	plan, err := planner.Build(inventory, snapshot)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	payload, err := json.MarshalIndent(plan, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	payload = append(payload, '\n')
	if err := os.MkdirAll(filepath.Dir(*outPath), 0o755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := os.WriteFile(*outPath, payload, 0o644); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
