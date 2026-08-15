package main

import (
	"fmt"
	"os"

	"vcfarch-0087/architecture"
)

func main() {
	var inventory architecture.Inventory
	if err := architecture.ReadJSON(architecture.InventoryPath, &inventory); err != nil {
		fatal(err)
	}
	var snapshot architecture.CompatibilitySnapshot
	if err := architecture.ReadJSON(architecture.SnapshotPath, &snapshot); err != nil {
		fatal(err)
	}
	plan, err := architecture.Build(inventory, snapshot)
	if err != nil {
		fatal(err)
	}
	if err := architecture.Validate(plan, inventory, snapshot); err != nil {
		fatal(err)
	}
	if err := architecture.WritePlan(architecture.ArtifactPath, plan); err != nil {
		fatal(err)
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
