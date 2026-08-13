// Package contract loads docs/contract.json, the machine-readable slice of the
// VMware Cloud Foundation Operations OpenAPI specification that this module is
// built against.
//
// The contract is the single source of truth for the HTTP surface: the loopback
// mock in internal/mock routes exclusively from it, and the protected verifier
// asserts against it. Nothing here reaches the network.
package contract

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

// Parameter is a single OpenAPI parameter as recorded in the contract.
type Parameter struct {
	Name        string          `json:"name"`
	In          string          `json:"in"`
	Required    bool            `json:"required"`
	Schema      json.RawMessage `json:"schema"`
	Description string          `json:"description"`
}

// Body describes an operation's request body.
type Body struct {
	Required  bool   `json:"required"`
	MediaType string `json:"mediaType"`
	Schema    Schema `json:"schema"`
}

// Response describes one documented response of an operation.
type Response struct {
	MediaType   string `json:"mediaType"`
	Schema      Schema `json:"schema"`
	Description string `json:"description"`
}

// Operation is one operationId named by the contract.
type Operation struct {
	OperationID string              `json:"operationId"`
	Method      string              `json:"method"`
	Path        string              `json:"path"`
	RequestPath string              `json:"requestPath"`
	Summary     string              `json:"summary"`
	Parameters  []Parameter         `json:"parameters"`
	RequestBody *Body               `json:"requestBody"`
	Responses   map[string]Response `json:"responses"`
}

// Security mirrors the spec's security scheme.
type Security struct {
	Scheme     string `json:"scheme"`
	Type       string `json:"type"`
	In         string `json:"in"`
	HeaderName string `json:"headerName"`
}

// Source records where the contract was derived from.
type Source struct {
	Repository string `json:"repository"`
	Path       string `json:"path"`
	Commit     string `json:"commit"`
	SpecSha256 string `json:"specSha256"`
	License    string `json:"license"`
	OpenAPI    string `json:"openapi"`
	APITitle   string `json:"apiTitle"`
	APIVersion string `json:"apiVersion"`
}

// Schema is an OpenAPI schema node, kept loose so $ref indirection can be
// resolved lazily against the contract's schema pool.
type Schema struct {
	Ref        string            `json:"$ref"`
	Type       string            `json:"type"`
	Format     string            `json:"format"`
	Properties map[string]Schema `json:"properties"`
	Required   []string          `json:"required"`
	Items      *Schema           `json:"items"`
	Enum       []any             `json:"enum"`
}

// Contract is the parsed docs/contract.json.
type Contract struct {
	Source       Source               `json:"source"`
	BasePath     string               `json:"basePath"`
	Security     Security             `json:"security"`
	OperationIDs []string             `json:"operationIds"`
	Operations   map[string]Operation `json:"operations"`
	Schemas      map[string]Schema    `json:"schemas"`
}

var (
	once   sync.Once
	loaded *Contract
	lodErr error
)

// Load reads and parses docs/contract.json from the module root. The result is
// cached, so repeated calls from parallel tests are cheap and race-free.
func Load() (*Contract, error) {
	once.Do(func() {
		path, err := findFile(filepath.Join("docs", "contract.json"))
		if err != nil {
			lodErr = err
			return
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			lodErr = fmt.Errorf("read %s: %w", path, err)
			return
		}
		var c Contract
		dec := json.NewDecoder(strings.NewReader(string(raw)))
		if err := dec.Decode(&c); err != nil {
			lodErr = fmt.Errorf("parse %s: %w", path, err)
			return
		}
		if err := c.validate(); err != nil {
			lodErr = fmt.Errorf("%s: %w", path, err)
			return
		}
		loaded = &c
	})
	return loaded, lodErr
}

// MustLoad is Load but panics on failure. Intended for test setup.
func MustLoad() *Contract {
	c, err := Load()
	if err != nil {
		panic(err)
	}
	return c
}

func (c *Contract) validate() error {
	if c.BasePath == "" {
		return fmt.Errorf("basePath is empty")
	}
	if len(c.OperationIDs) == 0 {
		return fmt.Errorf("operationIds is empty")
	}
	for _, id := range c.OperationIDs {
		op, ok := c.Operations[id]
		if !ok {
			return fmt.Errorf("operationId %q listed but not defined under operations", id)
		}
		if op.OperationID != id {
			return fmt.Errorf("operation %q declares operationId %q", id, op.OperationID)
		}
		if op.Method == "" || op.Path == "" {
			return fmt.Errorf("operation %q is missing method or path", id)
		}
	}
	if len(c.Operations) != len(c.OperationIDs) {
		return fmt.Errorf("operations defines %d entries but operationIds names %d",
			len(c.Operations), len(c.OperationIDs))
	}
	return nil
}

// Operation returns the operation with the given id.
func (c *Contract) Operation(id string) (Operation, bool) {
	op, ok := c.Operations[id]
	return op, ok
}

// Route maps a concrete request method and URL path onto the operationId the
// contract names for it. Paths outside the contract return false; the mock
// relies on this to refuse everything the contract does not describe.
func (c *Contract) Route(method, urlPath string) (Operation, bool) {
	for _, id := range c.SortedOperationIDs() {
		op := c.Operations[id]
		if !strings.EqualFold(op.Method, method) {
			continue
		}
		if matchPath(op.RequestPath, urlPath) {
			return op, true
		}
	}
	return Operation{}, false
}

// SortedOperationIDs returns the contract's operationIds in a stable order.
func (c *Contract) SortedOperationIDs() []string {
	out := append([]string(nil), c.OperationIDs...)
	sort.Strings(out)
	return out
}

// matchPath compares a contract request path against a concrete path, treating
// {placeholder} segments as single-segment wildcards.
func matchPath(pattern, actual string) bool {
	p := strings.Split(strings.Trim(pattern, "/"), "/")
	a := strings.Split(strings.Trim(actual, "/"), "/")
	if len(p) != len(a) {
		return false
	}
	for i := range p {
		if strings.HasPrefix(p[i], "{") && strings.HasSuffix(p[i], "}") {
			if a[i] == "" {
				return false
			}
			continue
		}
		if p[i] != a[i] {
			return false
		}
	}
	return true
}

// Resolve follows a $ref into the contract's schema pool.
func (c *Contract) Resolve(s Schema) (Schema, error) {
	seen := 0
	for s.Ref != "" {
		seen++
		if seen > 32 {
			return Schema{}, fmt.Errorf("schema $ref chain too deep")
		}
		name := s.Ref[strings.LastIndex(s.Ref, "/")+1:]
		next, ok := c.Schemas[name]
		if !ok {
			return Schema{}, fmt.Errorf("unknown schema %q", name)
		}
		s = next
	}
	return s, nil
}

// ValidateBody checks a decoded JSON request body against a contract schema.
// It enforces the two things the contract can state unambiguously: required
// properties are present, and no property outside the schema is sent.
func (c *Contract) ValidateBody(s Schema, value any) error {
	return c.validateNode(s, value, "$")
}

func (c *Contract) validateNode(s Schema, value any, path string) error {
	s, err := c.Resolve(s)
	if err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	if value == nil {
		return nil
	}

	switch s.Type {
	case "object", "":
		obj, ok := value.(map[string]any)
		if !ok {
			if s.Type == "" {
				return nil
			}
			return fmt.Errorf("%s: expected object, got %T", path, value)
		}
		for _, req := range s.Required {
			if _, present := obj[req]; !present {
				return fmt.Errorf("%s: required property %q is missing", path, req)
			}
		}
		if len(s.Properties) > 0 {
			for k, v := range obj {
				sub, known := s.Properties[k]
				if !known {
					return fmt.Errorf("%s: property %q is not part of the contract schema", path, k)
				}
				if err := c.validateNode(sub, v, path+"."+k); err != nil {
					return err
				}
			}
		}
	case "array":
		arr, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: expected array, got %T", path, value)
		}
		if s.Items != nil {
			for i, item := range arr {
				if err := c.validateNode(*s.Items, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	case "string":
		if _, ok := value.(string); !ok {
			return fmt.Errorf("%s: expected string, got %T", path, value)
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("%s: expected boolean, got %T", path, value)
		}
	case "number", "integer":
		if _, ok := value.(float64); !ok {
			return fmt.Errorf("%s: expected number, got %T", path, value)
		}
	}
	return nil
}

// findFile walks up from the working directory to the module root and returns
// the absolute path of rel. Test binaries run with the package directory as the
// working directory, so this resolves the same way from every package.
func findFile(rel string) (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			candidate := filepath.Join(dir, rel)
			if _, err := os.Stat(candidate); err != nil {
				return "", fmt.Errorf("module root %s has no %s", dir, rel)
			}
			return candidate, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("no go.mod found above working directory")
		}
		dir = parent
	}
}

// ModuleFile returns the absolute path of a file relative to the module root.
func ModuleFile(rel string) (string, error) { return findFile(rel) }
