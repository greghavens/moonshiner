package main

import (
	"flag"
	"fmt"
	"os"

	"example.com/distill/internal/assets"
)

func main() {
	assetName := flag.String("asset", "index.html", "embedded asset to print")
	flag.Parse()

	data, err := assets.Lookup(*assetName)
	if err != nil {
		fmt.Fprintf(os.Stderr, "distill: read embedded asset %q: %v\n", *assetName, err)
		os.Exit(1)
	}
	if _, err := os.Stdout.Write(data); err != nil {
		fmt.Fprintf(os.Stderr, "distill: write asset: %v\n", err)
		os.Exit(1)
	}
}
