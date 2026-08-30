package mock

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"
)

// Contract is the machine-readable REST contract the mock is pinned to. It is
// the on-disk shape of docs/contract.json.
type Contract struct {
	Spec           SpecRef           `json:"spec"`
	BasePath       string            `json:"basePath"`
	SecurityScheme SecurityScheme    `json:"securityScheme"`
	Operations     []Operation       `json:"operations"`
	Schemas        map[string]Schema `json:"schemas"`
}

// SpecRef identifies the OpenAPI document the contract was derived from.
type SpecRef struct {
	Repository string `json:"repository"`
	Path       string `json:"path"`
	Commit     string `json:"commit"`
	Title      string `json:"title"`
	Version    string `json:"version"`
}

// SecurityScheme mirrors the OpenAPI security scheme the operations use.
type SecurityScheme struct {
	Name string `json:"name"`
	In   string `json:"in"`
	Type string `json:"type"`
}

// Operation is one contracted operation, keyed by its OpenAPI operationId.
type Operation struct {
	OperationID    string `json:"operationId"`
	Method         string `json:"method"`
	Path           string `json:"path"`
	Authenticated  bool   `json:"authenticated"`
	SuccessStatus  int    `json:"successStatus"`
	RequestSchema  string `json:"requestSchema"`
	ResponseSchema string `json:"responseSchema"`
	// QueryParams lists every query parameter the client is permitted to send
	// for this operation. Empty means the operation takes no query parameters.
	QueryParams []string `json:"queryParams"`
}

// Schema records the required and optional property names of a named schema
// from the specification's components/schemas section.
type Schema struct {
	Required []string `json:"required"`
	Optional []string `json:"optional"`
}

// Has reports whether name is a known property of the schema.
func (s Schema) Has(name string) bool {
	for _, n := range s.Required {
		if n == name {
			return true
		}
	}
	for _, n := range s.Optional {
		if n == name {
			return true
		}
	}
	return false
}

var commitPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)

// LoadContract reads and validates a contract document.
func LoadContract(path string) (*Contract, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	var c Contract
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("parse contract %s: %w", path, err)
	}

	if c.Spec.Path == "" {
		return nil, fmt.Errorf("contract %s: spec.path is empty", path)
	}
	if !commitPattern.MatchString(c.Spec.Commit) {
		return nil, fmt.Errorf("contract %s: spec.commit %q is not a 40-character lowercase hex sha", path, c.Spec.Commit)
	}
	if !strings.HasPrefix(c.BasePath, "/") || strings.HasSuffix(c.BasePath, "/") {
		return nil, fmt.Errorf("contract %s: basePath %q must start with %q and must not end with %q", path, c.BasePath, "/", "/")
	}
	if c.SecurityScheme.Name == "" {
		return nil, fmt.Errorf("contract %s: securityScheme.name is empty", path)
	}
	if len(c.Operations) == 0 {
		return nil, fmt.Errorf("contract %s: no operations declared", path)
	}

	seenID := map[string]bool{}
	seenRoute := map[string]string{}
	for i, op := range c.Operations {
		switch {
		case op.OperationID == "":
			return nil, fmt.Errorf("contract %s: operations[%d] has no operationId", path, i)
		case seenID[op.OperationID]:
			return nil, fmt.Errorf("contract %s: operationId %q declared twice", path, op.OperationID)
		case op.Method != strings.ToUpper(op.Method) || op.Method == "":
			return nil, fmt.Errorf("contract %s: operation %q method %q must be upper case", path, op.OperationID, op.Method)
		case !strings.HasPrefix(op.Path, "/"):
			return nil, fmt.Errorf("contract %s: operation %q path %q must start with %q", path, op.OperationID, op.Path, "/")
		case op.SuccessStatus < 200 || op.SuccessStatus > 299:
			return nil, fmt.Errorf("contract %s: operation %q successStatus %d is not a 2xx status", path, op.OperationID, op.SuccessStatus)
		}
		seenID[op.OperationID] = true

		route := op.Method + " " + op.Path
		if other, dup := seenRoute[route]; dup {
			return nil, fmt.Errorf("contract %s: operations %q and %q both claim %q", path, other, op.OperationID, route)
		}
		seenRoute[route] = op.OperationID

		for _, name := range []string{op.RequestSchema, op.ResponseSchema} {
			if name == "" {
				continue
			}
			if _, ok := c.Schemas[name]; !ok {
				return nil, fmt.Errorf("contract %s: operation %q references schema %q which is not in schemas", path, op.OperationID, name)
			}
		}
	}

	for name, s := range c.Schemas {
		combined := append(append([]string{}, s.Required...), s.Optional...)
		sort.Strings(combined)
		for j := 1; j < len(combined); j++ {
			if combined[j] == combined[j-1] {
				return nil, fmt.Errorf("contract %s: schema %q lists property %q twice", path, name, combined[j])
			}
		}
	}

	return &c, nil
}

// Operation returns the contracted operation with the given operationId.
func (c *Contract) Operation(operationID string) (Operation, bool) {
	for _, op := range c.Operations {
		if op.OperationID == operationID {
			return op, true
		}
	}
	return Operation{}, false
}

// OperationIDs returns the declared operationIds in declaration order.
func (c *Contract) OperationIDs() []string {
	ids := make([]string, 0, len(c.Operations))
	for _, op := range c.Operations {
		ids = append(ids, op.OperationID)
	}
	return ids
}
