package main

import (
	"log"

	architecture "vcfarchitecture"
)

func main() {
	var scenario architecture.Scenario
	var estate architecture.Estate
	var authority architecture.CompatibilitySnapshot
	for _, input := range []struct {
		path string
		dst  any
	}{
		{"fixtures/scenario.json", &scenario},
		{"fixtures/estate.json", &estate},
		{"fixtures/compatibility-snapshot.json", &authority},
	} {
		if err := architecture.LoadJSON(input.path, input.dst); err != nil {
			log.Fatal(err)
		}
	}
	spec, plan, err := architecture.Build(scenario, estate, authority)
	if err != nil {
		log.Fatal(err)
	}
	if err := architecture.WriteArtifacts(".", spec, plan); err != nil {
		log.Fatal(err)
	}
}
