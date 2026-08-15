package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"vcfarchitecture/architecture"
)

func main() {
	inventory := flag.String("inventory", "fixtures/estate-inventory.json", "estate inventory")
	compatibility := flag.String("compatibility", "fixtures/compatibility-snapshot.json", "pinned compatibility snapshot")
	output := flag.String("output", "out/architecture.json", "architecture output")
	flag.Parse()

	artifact, err := architecture.Build(*inventory, *compatibility)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := os.MkdirAll(filepath.Dir(*output), 0o755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := architecture.WriteFile(*output, artifact); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
