// Package contract loads docs/contract.json, the wire contract for the vCenter
// virtual-machine reconfiguration change set.
//
// The contract is a projection of the vSphere Automation API specification
// recorded in docs/official_sources.json. Both the loopback mock in
// internal/vcmock and the protected verifier in verify/ build themselves from
// this document, so the contract - not any hand-written table - is the single
// authority on paths, query parameters, headers, status codes and the required
// versus optional property sets.
package contract

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// API records which specification revision the contract was taken from.
type API struct {
	Title               string `json:"title"`
	Version             string `json:"version"`
	OpenAPI             string `json:"openapi"`
	ServerURLTemplate   string `json:"serverUrlTemplate"`
	BasePath            string `json:"basePath"`
	SpecPath            string `json:"specPath"`
	RepositoryTag       string `json:"repositoryTag"`
	RepositoryCommitSha string `json:"repositoryCommitSha"`
}

// Authorization describes the api_key_auth security scheme.
type Authorization struct {
	Scheme      string   `json:"scheme"`
	SpecPointer string   `json:"specPointer"`
	HeaderName  string   `json:"headerName"`
	In          string   `json:"in"`
	Provenance  string   `json:"provenance"`
	AppliesTo   []string `json:"appliesTo"`
}

// RequestConventions holds the header and query rules shared by the operations.
type RequestConventions struct {
	AcceptHeader                    string `json:"acceptHeader"`
	ContentTypeHeader               string `json:"contentTypeHeader"`
	ContentTypeOnBodiedRequestsOnly bool   `json:"contentTypeOnBodiedRequestsOnly"`
	GetRequestsHaveNoBody           bool   `json:"getRequestsHaveNoBody"`
	ActionQueryParameter            string `json:"actionQueryParameter"`
	Note                            string `json:"note"`
}

// Vocabulary is a spec-derived enumeration.
type Vocabulary struct {
	SpecPointer string   `json:"specPointer"`
	Values      []string `json:"values"`
}

// Has reports whether v enumerates value.
func (v Vocabulary) Has(value string) bool {
	for _, got := range v.Values {
		if got == value {
			return true
		}
	}
	return false
}

// Operation is one specification operation this change set is allowed to call.
type Operation struct {
	OperationID         string            `json:"operationId"`
	Method              string            `json:"method"`
	Path                string            `json:"path"`
	SpecPathKey         string            `json:"specPathKey"`
	SpecPointer         string            `json:"specPointer"`
	Query               map[string]string `json:"query"`
	PathParameters      []string          `json:"pathParameters"`
	RequestContentType  string            `json:"requestContentType"`
	RequestBodyRequired bool              `json:"requestBodyRequired"`
	RequestSchema       string            `json:"requestSchema"`
	SuccessStatus       int               `json:"successStatus"`
	ResponseContentType string            `json:"responseContentType"`
	ResponseSchema      string            `json:"responseSchema"`
	ErrorResponses      map[string]string `json:"errorResponses"`
	Security            []string          `json:"security"`
}

// HasBody reports whether the operation carries a request body.
func (o Operation) HasBody() bool { return o.RequestSchema != "" }

// Property is one property of a contract schema.
type Property struct {
	Required       bool     `json:"required"`
	Type           string   `json:"type"`
	Format         string   `json:"format,omitempty"`
	SchemaName     string   `json:"schemaName,omitempty"`
	PossibleValues []string `json:"possibleValues,omitempty"`
	Description    string   `json:"description"`
}

// Schema is one specification schema the change set serializes or parses.
type Schema struct {
	SchemaName         string              `json:"schemaName"`
	SpecPointer        string              `json:"specPointer"`
	RequiredProperties []string            `json:"requiredProperties"`
	OptionalProperties []string            `json:"optionalProperties"`
	AllowedProperties  []string            `json:"allowedProperties"`
	Properties         map[string]Property `json:"properties"`
}

// Allows reports whether name is a property the schema declares.
func (s Schema) Allows(name string) bool {
	for _, got := range s.AllowedProperties {
		if got == name {
			return true
		}
	}
	return false
}

// Contract is the parsed docs/contract.json document.
type Contract struct {
	ContractVersion    string                `json:"contractVersion"`
	Description        string                `json:"description"`
	API                API                   `json:"api"`
	Authorization      Authorization         `json:"authorization"`
	RequestConventions RequestConventions    `json:"requestConventions"`
	OmitEmptyRule      string                `json:"omitEmptyRule"`
	Vocabularies       map[string]Vocabulary `json:"vocabularies"`
	Operations         []Operation           `json:"operations"`
	Schemas            map[string]Schema     `json:"schemas"`
}

// Operation returns the operation with the given operationId.
func (c *Contract) Operation(id string) (Operation, bool) {
	for _, op := range c.Operations {
		if op.OperationID == id {
			return op, true
		}
	}
	return Operation{}, false
}

// OperationIDs lists every operationId the contract names, in contract order.
func (c *Contract) OperationIDs() []string {
	ids := make([]string, 0, len(c.Operations))
	for _, op := range c.Operations {
		ids = append(ids, op.OperationID)
	}
	return ids
}

// Schema returns the named schema.
func (c *Contract) Schema(name string) (Schema, bool) {
	s, ok := c.Schemas[name]
	return s, ok
}

// Vocabulary returns the named spec-derived enumeration.
func (c *Contract) Vocabulary(name string) (Vocabulary, bool) {
	v, ok := c.Vocabularies[name]
	return v, ok
}

// Match resolves a request method, path and query to the operation that serves
// it. The returned map holds the path parameters bound by the match. Reporting
// false is how the mock refuses an operation the contract does not name.
func (c *Contract) Match(method, path string, query map[string][]string) (Operation, map[string]string, bool) {
	for _, op := range c.Operations {
		if !strings.EqualFold(op.Method, method) {
			continue
		}
		params, ok := matchPath(op.Path, path)
		if !ok {
			continue
		}
		if !queryMatches(op.Query, query) {
			continue
		}
		return op, params, true
	}
	return Operation{}, nil, false
}

// matchPath binds a templated contract path such as /vcenter/vm/{vm}/power
// against a concrete request path.
func matchPath(template, path string) (map[string]string, bool) {
	tSegs := strings.Split(strings.Trim(template, "/"), "/")
	pSegs := strings.Split(strings.Trim(path, "/"), "/")
	if len(tSegs) != len(pSegs) {
		return nil, false
	}
	params := map[string]string{}
	for i, t := range tSegs {
		if strings.HasPrefix(t, "{") && strings.HasSuffix(t, "}") {
			name := strings.TrimSuffix(strings.TrimPrefix(t, "{"), "}")
			if pSegs[i] == "" {
				return nil, false
			}
			params[name] = pSegs[i]
			continue
		}
		if t != pSegs[i] {
			return nil, false
		}
	}
	return params, true
}

// queryMatches requires the operation's fixed query parameters to be present
// with exactly the contract value, and requires an operation that declares none
// to be called without them. That is what keeps Vcenter.Vm.Power_get,
// Vcenter.Vm.Power_stop and Vcenter.Vm.Power_start apart on their shared path.
func queryMatches(want map[string]string, got map[string][]string) bool {
	for k, v := range want {
		values, ok := got[k]
		if !ok || len(values) != 1 || values[0] != v {
			return false
		}
	}
	if len(want) == 0 {
		if _, ok := got["action"]; ok {
			return false
		}
	}
	return true
}

// Load reads and parses a contract document.
func Load(path string) (*Contract, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("contract: read %s: %w", path, err)
	}
	var c Contract
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("contract: parse %s: %w", path, err)
	}
	if len(c.Operations) == 0 {
		return nil, fmt.Errorf("contract: %s names no operations", path)
	}
	seen := map[string]bool{}
	for _, op := range c.Operations {
		if op.OperationID == "" {
			return nil, fmt.Errorf("contract: %s has an operation with no operationId", path)
		}
		if seen[op.OperationID] {
			return nil, fmt.Errorf("contract: %s names operation %q twice", path, op.OperationID)
		}
		seen[op.OperationID] = true
		if op.RequestSchema != "" {
			if _, ok := c.Schemas[op.RequestSchema]; !ok {
				return nil, fmt.Errorf("contract: operation %q refers to unknown schema %q", op.OperationID, op.RequestSchema)
			}
		}
	}
	return &c, nil
}

// LoadDefault finds docs/contract.json by walking up from the working
// directory, so it resolves the same way from any package in the module.
func LoadDefault() (*Contract, error) {
	dir, err := os.Getwd()
	if err != nil {
		return nil, fmt.Errorf("contract: working directory: %w", err)
	}
	for {
		candidate := filepath.Join(dir, "docs", "contract.json")
		if _, err := os.Stat(candidate); err == nil {
			return Load(candidate)
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return nil, fmt.Errorf("contract: docs/contract.json not found above the working directory")
		}
		dir = parent
	}
}

// SortedKeys returns the sorted keys of a decoded JSON object, which is how the
// verifier compares a serialized request body against a contract property set.
func SortedKeys(obj map[string]any) []string {
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
