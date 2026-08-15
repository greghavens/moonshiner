package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	vcfarch "vcfarch-0122/vcfarch"
)

func readJSON(path string, target any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, target)
}

func main() {
	inventoryPath := flag.String("inventory", "fixtures/estate.json", "estate inventory")
	compatibilityPath := flag.String("compatibility", "compatibility/pinned-compatibility.json", "compatibility snapshot")
	flag.Parse()

	var inventory vcfarch.Inventory
	if err := readJSON(*inventoryPath, &inventory); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	var snapshot vcfarch.CompatibilitySnapshot
	if err := readJSON(*compatibilityPath, &snapshot); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	architecture, err := vcfarch.Build(inventory, snapshot)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(architecture); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
