// Package router routes work items to queues according to a switch task
// from a workflow DSL document.
package router

import (
	"fmt"

	"gopkg.in/yaml.v3"
)

// Document is the workflow document header.
type Document struct {
	DSL       string `yaml:"dsl"`
	Namespace string `yaml:"namespace"`
	Name      string `yaml:"name"`
}

// Rule is one switch case: route to the Then queue when When holds.
// A rule without a when condition is the default route, taken only
// when no other rule matches.
type Rule struct {
	When string `yaml:"when"`
	Then string `yaml:"then"`
}

// RuleSet is a loaded routing document.
type RuleSet struct {
	Document Document
	Rules    []Rule
}

type ruleFile struct {
	Document Document `yaml:"document"`
	Route    struct {
		Switch []Rule `yaml:"switch"`
	} `yaml:"route"`
}

// LoadRules parses a routing document.
func LoadRules(data []byte) (*RuleSet, error) {
	var rf ruleFile
	if err := yaml.Unmarshal(data, &rf); err != nil {
		return nil, fmt.Errorf("parsing rules: %w", err)
	}
	if len(rf.Route.Switch) == 0 {
		return nil, fmt.Errorf("rules file declares no switch cases")
	}
	return &RuleSet{Document: rf.Document, Rules: rf.Route.Switch}, nil
}
