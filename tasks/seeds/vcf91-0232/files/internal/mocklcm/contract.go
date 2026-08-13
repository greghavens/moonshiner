// Package mocklcm is a loopback stand-in for the VMware Cloud Foundation 9.1
// SDDC LCM service. It builds its route table from docs/contract.json and
// serves only the operations that contract names, so a contract that disagrees
// with the published specification produces a service that rejects the client.
//
// Nothing here talks to a VMware endpoint. The listener is bound to 127.0.0.1.
package mocklcm

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
)

// Contract is the subset of docs/contract.json the mock needs to route and
// police requests.
type Contract struct {
	Source     ContractSource       `json:"source"`
	Server     string               `json:"server"`
	Security   ContractSecurity     `json:"security"`
	Operations map[string]Operation `json:"operations"`
	Schemas    map[string]Schema    `json:"schemas"`
}

// ContractSource records where the contract was derived from.
type ContractSource struct {
	Repository string `json:"repository"`
	SpecPath   string `json:"specPath"`
	Commit     string `json:"commit"`
	License    string `json:"license"`
	OpenAPI    string `json:"openapi"`
	APITitle   string `json:"apiTitle"`
	APIVersion string `json:"apiVersion"`
}

// ContractSecurity describes the specification's single security scheme.
type ContractSecurity struct {
	Scheme       string `json:"scheme"`
	Type         string `json:"type"`
	HTTPScheme   string `json:"httpScheme"`
	BearerFormat string `json:"bearerFormat"`
}

// Operation is one operationId's wire shape.
type Operation struct {
	Method          string            `json:"method"`
	Path            string            `json:"path"`
	SuccessStatus   int               `json:"successStatus"`
	RequestSchema   *string           `json:"requestSchema"`
	ResponseSchema  string            `json:"responseSchema"`
	PathParams      []string          `json:"pathParams"`
	FixedQuery      map[string]string `json:"fixedQuery"`
	QueryParams     FieldSplit        `json:"queryParams"`
	OptionalHeaders []string          `json:"optionalHeaders"`
	RequestVariants *Variants         `json:"requestVariants,omitempty"`
}

// Variants describes a discriminated request body.
type Variants struct {
	Discriminator string   `json:"discriminator"`
	Variants      []string `json:"variants"`
}

// FieldSplit is a required/optional partition.
type FieldSplit struct {
	Required []string `json:"required"`
	Optional []string `json:"optional"`
}

// Schema is a request schema's required/optional field partition.
type Schema struct {
	Required []string `json:"required"`
	Optional []string `json:"optional"`
}

// Known reports whether name is a required or optional field of s.
func (s Schema) Known(name string) bool {
	return contains(s.Required, name) || contains(s.Optional, name)
}

// LoadContract reads and validates docs/contract.json.
//
// Validation here is structural only: it checks that the file is usable as a
// route table. Whether it agrees with the published specification is checked by
// the contract test, and by the fact that a client built against a wrong
// contract will be rejected at request time.
func LoadContract(path string) (*Contract, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	var c Contract
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	if len(c.Operations) == 0 {
		return nil, fmt.Errorf("%s: no operations", path)
	}
	if c.Security.HTTPScheme == "" {
		return nil, fmt.Errorf("%s: security.httpScheme is empty", path)
	}
	for id, op := range c.Operations {
		if op.Method != strings.ToUpper(op.Method) || op.Method == "" {
			return nil, fmt.Errorf("%s: operation %s has method %q, want upper case", path, id, op.Method)
		}
		if !strings.HasPrefix(op.Path, "/") {
			return nil, fmt.Errorf("%s: operation %s has path %q", path, id, op.Path)
		}
		if op.SuccessStatus < 200 || op.SuccessStatus > 299 {
			return nil, fmt.Errorf("%s: operation %s has successStatus %d", path, id, op.SuccessStatus)
		}
		if strings.Contains(op.Path, "?") {
			return nil, fmt.Errorf("%s: operation %s keeps a query string in path %q", path, id, op.Path)
		}
		want := pathParamNames(op.Path)
		got := append([]string(nil), op.PathParams...)
		sort.Strings(got)
		if !equalStrings(want, got) {
			return nil, fmt.Errorf("%s: operation %s declares pathParams %v but path %q has %v",
				path, id, got, op.Path, want)
		}
		if op.RequestSchema != nil {
			if _, ok := c.Schemas[*op.RequestSchema]; !ok && op.RequestVariants == nil {
				return nil, fmt.Errorf("%s: operation %s names request schema %q, which schemas does not define",
					path, id, *op.RequestSchema)
			}
		}
	}
	return &c, nil
}

// OperationID finds the operation a method, path and query select, mirroring the
// way the specification distinguishes them. Two operations may share a path: the
// method, and for the task actions the pinned query, tell them apart.
func (c *Contract) OperationID(method, path string, query map[string][]string) (string, map[string]string, bool) {
	ids := make([]string, 0, len(c.Operations))
	for id := range c.Operations {
		ids = append(ids, id)
	}
	// Sort so that operations pinning more query parameters are considered
	// first: retryTask must win over getTask on the same path when action=retry
	// is present, whatever order the map iterates in.
	sort.Slice(ids, func(i, j int) bool {
		a, b := c.Operations[ids[i]], c.Operations[ids[j]]
		if len(a.FixedQuery) != len(b.FixedQuery) {
			return len(a.FixedQuery) > len(b.FixedQuery)
		}
		return ids[i] < ids[j]
	})
	for _, id := range ids {
		op := c.Operations[id]
		if !strings.EqualFold(op.Method, method) {
			continue
		}
		params, ok := matchPath(op.Path, path)
		if !ok {
			continue
		}
		pinned := true
		for k, v := range op.FixedQuery {
			got, present := query[k]
			if !present || len(got) != 1 || got[0] != v {
				pinned = false
				break
			}
		}
		if !pinned {
			continue
		}
		return id, params, true
	}
	return "", nil, false
}

// matchPath matches a concrete path against a spec path template, returning the
// captured path parameters.
func matchPath(template, path string) (map[string]string, bool) {
	tp := splitPath(template)
	pp := splitPath(path)
	if len(tp) != len(pp) {
		return nil, false
	}
	out := map[string]string{}
	for i, seg := range tp {
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			name := seg[1 : len(seg)-1]
			if pp[i] == "" {
				return nil, false
			}
			out[name] = pp[i]
			continue
		}
		if seg != pp[i] {
			return nil, false
		}
	}
	return out, true
}

func splitPath(p string) []string {
	p = strings.Trim(p, "/")
	if p == "" {
		return nil
	}
	return strings.Split(p, "/")
}

func pathParamNames(template string) []string {
	var out []string
	for _, seg := range splitPath(template) {
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			out = append(out, seg[1:len(seg)-1])
		}
	}
	sort.Strings(out)
	if out == nil {
		out = []string{}
	}
	return out
}

func contains(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
