package main

import (
	"encoding/json"
	"fmt"
	"os"

	"vcfarch"
)

func main() {
	var inventory vcfarch.Inventory
	var snapshot vcfarch.CompatibilitySnapshot
	readJSON("testdata/estate.json", &inventory)
	readJSON("testdata/compatibility-snapshot.json", &snapshot)

	architecture, err := vcfarch.Build(inventory, snapshot)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	encoded, err := json.MarshalIndent(architecture, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	encoded = append(encoded, '\n')
	if err := os.WriteFile("architecture.json", encoded, 0o644); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func readJSON(path string, destination any) {
	raw, err := os.ReadFile(path)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.Unmarshal(raw, destination); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
