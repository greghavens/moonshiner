// Package contract loads docs/contract.json, the wire contract for the VMware
// Cloud Foundation 9.0 SDDC Manager host-commissioning flow.
//
// The contract was extracted from the OpenAPI specification recorded in
// docs/official_sources.json. Both the loopback mock in internal/smmock and the
// protected verifier in verify/ read their routing table, header rules and
// status vocabularies from here, so the mock and the verifier can never drift
// apart from each other or from the specification.
//
// Do not edit this package. It is replaced wholesale during grading.
package contract

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Contract is the parsed docs/contract.json document.
type Contract struct {
	ContractVersion    string                      `json:"contractVersion"`
	Description        string                      `json:"description"`
	API                API                         `json:"api"`
	Authorization      Authorization               `json:"authorization"`
	RequestConventions RequestConventions          `json:"requestConventions"`
	OmitEmptyRule      string                      `json:"omitEmptyRule"`
	Schemas            map[string]Schema           `json:"schemas"`
	StatusVocabularies map[string]StatusVocabulary `json:"statusVocabularies"`
	Operations         map[string]Operation        `json:"operations"`
	ErrorSchema        ErrorSchema                 `json:"errorSchema"`
}

// API records which specification revision the contract was cut from.
type API struct {
	Title               string `json:"title"`
	Version             string `json:"version"`
	OpenAPI             string `json:"openapi"`
	BasePath            string `json:"basePath"`
	SpecPath            string `json:"specPath"`
	RepositoryCommitSha string `json:"repositoryCommitSha"`
}

// Authorization describes the header the flow must carry and the operations it
// applies to.
type Authorization struct {
	HeaderName          string   `json:"headerName"`
	HeaderValueTemplate string   `json:"headerValueTemplate"`
	TokenSource         string   `json:"tokenSource"`
	Provenance          string   `json:"provenance"`
	AppliesTo           []string `json:"appliesTo"`
	ExemptOperations    []string `json:"exemptOperations"`
}

// HeaderValue renders HeaderValueTemplate for a concrete access token.
func (a Authorization) HeaderValue(accessToken string) string {
	return strings.ReplaceAll(a.HeaderValueTemplate, "{accessToken}", accessToken)
}

// RequiresAuthorization reports whether operationID must carry the header.
func (a Authorization) RequiresAuthorization(operationID string) bool {
	for _, id := range a.AppliesTo {
		if id == operationID {
			return true
		}
	}
	return false
}

// IsExempt reports whether operationID must not carry the header.
func (a Authorization) IsExempt(operationID string) bool {
	for _, id := range a.ExemptOperations {
		if id == operationID {
			return true
		}
	}
	return false
}

// RequestConventions holds the header and URL rules shared by every operation.
type RequestConventions struct {
	AcceptHeader                    string `json:"acceptHeader"`
	ContentTypeHeader               string `json:"contentTypeHeader"`
	ContentTypeOnBodiedRequestsOnly bool   `json:"contentTypeOnBodiedRequestsOnly"`
	GetRequestsHaveNoBody           bool   `json:"getRequestsHaveNoBody"`
	GetRequestsHaveNoQueryString    bool   `json:"getRequestsHaveNoQueryString"`
	Note                            string `json:"note"`
}

// Schema is the contract's view of an OpenAPI request schema.
type Schema struct {
	SchemaName         string              `json:"schemaName"`
	RequiredProperties []string            `json:"requiredProperties"`
	OptionalProperties []string            `json:"optionalProperties"`
	AllowedProperties  []string            `json:"allowedProperties"`
	Properties         map[string]Property `json:"properties"`
}

// Property is one schema property.
type Property struct {
	Type          string   `json:"type"`
	Required      bool     `json:"required"`
	Description   string   `json:"description"`
	AllowedValues []string `json:"allowedValues"`
}

// StatusVocabulary is the set of values an asynchronous status property can
// take, together with which of them end a polling loop.
type StatusVocabulary struct {
	Property                    string              `json:"property"`
	SpecSource                  string              `json:"specSource"`
	RawValues                   []string            `json:"rawValues"`
	CanonicalForms              map[string][]string `json:"canonicalForms"`
	Normalization               string              `json:"normalization"`
	NonTerminal                 []string            `json:"nonTerminal"`
	Terminal                    []string            `json:"terminal"`
	Success                     []string            `json:"success"`
	Failure                     []string            `json:"failure"`
	Acceptable                  []string            `json:"acceptable"`
	Unacceptable                []string            `json:"unacceptable"`
	UnknownValuesAreNonTerminal bool                `json:"unknownValuesAreNonTerminal"`
	RevisionNote                string              `json:"revisionNote"`
}

// Canonical applies the vocabulary's documented normalization to a raw value.
func Canonical(status string) string {
	return strings.ReplaceAll(strings.ToUpper(strings.TrimSpace(status)), " ", "_")
}

// IsTerminal reports whether a raw status value ends a polling loop.
func (v StatusVocabulary) IsTerminal(status string) bool {
	want := Canonical(status)
	for _, t := range v.Terminal {
		if Canonical(t) == want {
			return true
		}
	}
	return false
}

// Operation is one contract operation.
type Operation struct {
	OperationID     string              `json:"operationId"`
	Method          string              `json:"method"`
	Path            string              `json:"path"`
	Summary         string              `json:"summary"`
	Tags            []string            `json:"tags"`
	PathParameters  []PathParameter     `json:"pathParameters"`
	QueryParameters []string            `json:"queryParameters"`
	RequestBody     *RequestBody        `json:"requestBody"`
	Responses       map[string]Response `json:"responses"`
	SuccessStatus   int                 `json:"successStatus"`
	SpecPointer     string              `json:"specPointer"`
}

// PathParameter is a templated path segment.
type PathParameter struct {
	Name     string `json:"name"`
	Required bool   `json:"required"`
	Type     string `json:"type"`
}

// RequestBody describes an operation's JSON request body.
type RequestBody struct {
	Required           bool     `json:"required"`
	ContentType        string   `json:"contentType"`
	JSONShape          string   `json:"jsonShape"` // "object" or "array"
	Schema             string   `json:"schema"`
	ItemSchema         string   `json:"itemSchema"`
	RequiredProperties []string `json:"requiredProperties"`
	AllowedProperties  []string `json:"allowedProperties"`
}

// Response is one declared response code.
type Response struct {
	Description  string   `json:"description"`
	ContentTypes []string `json:"contentTypes"`
	Schema       string   `json:"schema"`
}

// ErrorSchema is the contract's view of the shared Error schema.
type ErrorSchema struct {
	SchemaName        string   `json:"schemaName"`
	AllowedProperties []string `json:"allowedProperties"`
	Note              string   `json:"note"`
}

// Operation returns the named operation, or an error naming the operations the
// contract does define.
func (c *Contract) Operation(operationID string) (Operation, error) {
	op, ok := c.Operations[operationID]
	if !ok {
		return Operation{}, fmt.Errorf("contract: no operation %q (contract defines %s)",
			operationID, strings.Join(c.OperationIDs(), ", "))
	}
	return op, nil
}

// MustOperation is Operation for callers that already know the operation
// exists; it panics otherwise.
func (c *Contract) MustOperation(operationID string) Operation {
	op, err := c.Operation(operationID)
	if err != nil {
		panic(err)
	}
	return op
}

// OperationIDs lists every operation the contract names, sorted.
func (c *Contract) OperationIDs() []string {
	ids := make([]string, 0, len(c.Operations))
	for id := range c.Operations {
		ids = append(ids, id)
	}
	sortStrings(ids)
	return ids
}

// Vocabulary returns a status vocabulary by key ("task", "validationExecution"
// or "validationResult").
func (c *Contract) Vocabulary(key string) (StatusVocabulary, error) {
	v, ok := c.StatusVocabularies[key]
	if !ok {
		return StatusVocabulary{}, fmt.Errorf("contract: no status vocabulary %q", key)
	}
	return v, nil
}

// Segments splits an operation path into segments. A segment of the form
// "{name}" is a path parameter.
func Segments(path string) []string {
	trimmed := strings.Trim(path, "/")
	if trimmed == "" {
		return nil
	}
	return strings.Split(trimmed, "/")
}

// IsParamSegment reports whether a path segment is a "{name}" template, and
// returns the parameter name.
func IsParamSegment(segment string) (string, bool) {
	if len(segment) >= 2 && segment[0] == '{' && segment[len(segment)-1] == '}' {
		return segment[1 : len(segment)-1], true
	}
	return "", false
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
	for id, op := range c.Operations {
		if op.OperationID != id {
			return nil, fmt.Errorf("contract: operation key %q disagrees with operationId %q", id, op.OperationID)
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

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}
