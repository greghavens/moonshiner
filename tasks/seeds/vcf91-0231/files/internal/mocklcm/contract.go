// Package mocklcm implements a loopback stand-in for the VCF 9.1 SDDC LCM
// service. Every route it serves is built from docs/contract.json: the mock
// knows how to simulate five operations and refuses to start unless the
// contract names exactly those five. Paths, methods, success statuses, query
// parameters, optional headers and request-body field splits all come from the
// contract, so a contract that disagrees with the specification produces a
// service that rejects the client.
package mocklcm

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
)

// Operations the mock knows how to simulate. The contract must name exactly
// these, no more and no fewer.
var knownOperations = []string{
	"backupRestoreComponentsAction",
	"fetchComponentStatuses",
	"getComponents",
	"getComponentsBackups",
	"getTask",
}

// nestedItemSchemas records which array property of a request schema carries
// items of another schema. The mock validates those items against the schema
// the contract publishes under that name.
var nestedItemSchemas = map[string]map[string]string{
	"ComponentsRestoreSpec": {"components": "RestoreBackupSpec"},
}

type contractDoc struct {
	Source     map[string]any             `json:"source"`
	Server     string                     `json:"server"`
	Security   securityBlock              `json:"security"`
	Operations map[string]*operation      `json:"operations"`
	Schemas    map[string]*schemaFieldSet `json:"schemas"`
}

type securityBlock struct {
	Scheme       string `json:"scheme"`
	Type         string `json:"type"`
	HTTPScheme   string `json:"httpScheme"`
	BearerFormat string `json:"bearerFormat"`
}

type operation struct {
	Method          string           `json:"method"`
	Path            string           `json:"path"`
	SuccessStatus   int              `json:"successStatus"`
	RequestSchema   *string          `json:"requestSchema"`
	ResponseSchema  string           `json:"responseSchema"`
	PathParams      []string         `json:"pathParams"`
	QueryParams     *fieldSplit      `json:"queryParams"`
	OptionalHeaders []string         `json:"optionalHeaders"`
	RequestVariants *variantSelector `json:"requestVariants"`

	segments []string // path split on "/", "{name}" segments are parameters
}

type fieldSplit struct {
	Required []string `json:"required"`
	Optional []string `json:"optional"`
}

type schemaFieldSet struct {
	Required []string `json:"required"`
	Optional []string `json:"optional"`
}

type variantSelector struct {
	Discriminator string   `json:"discriminator"`
	Variants      []string `json:"variants"`
}

func loadContract(path string) (*contractDoc, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	var doc contractDoc
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&doc); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	if err := doc.validate(path); err != nil {
		return nil, err
	}
	return &doc, nil
}

func (d *contractDoc) validate(path string) error {
	if d.Security.HTTPScheme == "" {
		return fmt.Errorf("%s: security.httpScheme is empty", path)
	}
	if d.Security.Scheme == "" {
		return fmt.Errorf("%s: security.scheme is empty", path)
	}
	if !strings.HasPrefix(d.Server, "https://") {
		return fmt.Errorf("%s: server %q is not an https URL", path, d.Server)
	}

	named := make([]string, 0, len(d.Operations))
	for name := range d.Operations {
		named = append(named, name)
	}
	sort.Strings(named)
	if strings.Join(named, ",") != strings.Join(knownOperations, ",") {
		return fmt.Errorf("%s: operations must be exactly %v, got %v",
			path, knownOperations, named)
	}

	for name, op := range d.Operations {
		if err := d.validateOperation(path, name, op); err != nil {
			return err
		}
	}
	for name, set := range d.Schemas {
		if set == nil {
			return fmt.Errorf("%s: schemas.%s is null", path, name)
		}
		if set.Required == nil || set.Optional == nil {
			return fmt.Errorf("%s: schemas.%s needs both required and optional lists", path, name)
		}
	}
	return nil
}

func (d *contractDoc) validateOperation(path, name string, op *operation) error {
	where := fmt.Sprintf("%s: operations.%s", path, name)
	if op == nil {
		return fmt.Errorf("%s is null", where)
	}
	switch op.Method {
	case "GET", "POST", "PUT", "PATCH", "DELETE":
	default:
		return fmt.Errorf("%s.method %q is not an upper case HTTP method", where, op.Method)
	}
	if !strings.HasPrefix(op.Path, "/") {
		return fmt.Errorf("%s.path %q must start with /", where, op.Path)
	}
	if op.SuccessStatus < 200 || op.SuccessStatus > 299 {
		return fmt.Errorf("%s.successStatus %d is not a 2xx status", where, op.SuccessStatus)
	}
	if op.QueryParams == nil || op.QueryParams.Required == nil || op.QueryParams.Optional == nil {
		return fmt.Errorf("%s.queryParams needs both required and optional lists", where)
	}
	if op.PathParams == nil {
		return fmt.Errorf("%s.pathParams is missing", where)
	}
	if op.OptionalHeaders == nil {
		return fmt.Errorf("%s.optionalHeaders is missing", where)
	}

	op.segments = strings.Split(strings.TrimPrefix(op.Path, "/"), "/")
	declared := map[string]bool{}
	for _, p := range op.PathParams {
		declared[p] = true
	}
	for _, seg := range op.segments {
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			pname := seg[1 : len(seg)-1]
			if !declared[pname] {
				return fmt.Errorf("%s.pathParams does not declare %q used in the path", where, pname)
			}
			delete(declared, pname)
		}
	}
	if len(declared) != 0 {
		return fmt.Errorf("%s.pathParams declares parameters absent from the path", where)
	}

	for _, schemaName := range d.requestSchemasFor(op) {
		set, ok := d.Schemas[schemaName]
		if !ok || set == nil {
			return fmt.Errorf("%s uses schema %q but schemas.%s is missing", where, schemaName, schemaName)
		}
		for prop, itemSchema := range nestedItemSchemas[schemaName] {
			if !contains(set.Required, prop) && !contains(set.Optional, prop) {
				return fmt.Errorf("%s: schemas.%s does not list property %q", path, schemaName, prop)
			}
			if _, ok := d.Schemas[itemSchema]; !ok {
				return fmt.Errorf("%s: schemas.%s is missing (items of %s.%s)",
					path, itemSchema, schemaName, prop)
			}
		}
	}
	if op.RequestVariants != nil {
		if op.RequestVariants.Discriminator == "" {
			return fmt.Errorf("%s.requestVariants.discriminator is empty", where)
		}
		if len(op.RequestVariants.Variants) == 0 {
			return fmt.Errorf("%s.requestVariants.variants is empty", where)
		}
	}
	return nil
}

// requestSchemasFor returns every schema a request body for op may be validated
// against: the discriminated variants when the operation declares them, and the
// plain request schema otherwise.
func (d *contractDoc) requestSchemasFor(op *operation) []string {
	if op.RequestVariants != nil {
		return op.RequestVariants.Variants
	}
	if op.RequestSchema != nil && *op.RequestSchema != "" {
		return []string{*op.RequestSchema}
	}
	return nil
}

// governedHeaders is the union of every optionalHeaders entry in the contract.
// A header in this set may only appear on an operation that declares it.
func (d *contractDoc) governedHeaders() []string {
	seen := map[string]bool{}
	var out []string
	for _, op := range d.Operations {
		for _, h := range op.OptionalHeaders {
			key := strings.ToLower(h)
			if !seen[key] {
				seen[key] = true
				out = append(out, h)
			}
		}
	}
	sort.Strings(out)
	return out
}

func contains(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}
