package main

import (
	"flag"
	"fmt"
	"os"

	"example.com/vcfarch"
)

func main() {
	requirements := flag.String("requirements", "fixtures/requirements.json", "greenfield requirements JSON")
	estate := flag.String("estate", "fixtures/estate.json", "brownfield estate JSON")
	compatibility := flag.String("compatibility", "pinned/compatibility.json", "pinned compatibility JSON")
	output := flag.String("output", "architecture", "artifact output directory")
	flag.Parse()

	if err := vcfarch.GenerateFromFiles(*requirements, *estate, *compatibility, *output); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
