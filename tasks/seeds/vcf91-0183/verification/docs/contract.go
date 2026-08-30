// Package contractdoc exposes the protected, spec-derived contract to the
// loopback mock and acceptance tests.
package contractdoc

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

//go:embed contract.json
var contractJSON []byte

//go:embed official_sources.json
var officialSourcesJSON []byte

type Document struct {
	OpenAPI    string              `json:"openapi"`
	Info       Info                `json:"info"`
	Source     Source              `json:"x-source"`
	Paths      map[string]PathItem `json:"paths"`
	Components Components          `json:"components"`
}

type Info struct {
	Title   string `json:"title"`
	Version string `json:"version"`
}

type Source struct {
	Repository string `json:"repository"`
	Commit     string `json:"commit"`
	Path       string `json:"path"`
	License    string `json:"license"`
}

type PathItem struct {
	Get *Operation `json:"get,omitempty"`
}

type Operation struct {
	OperationID string              `json:"operationId"`
	Parameters  []Parameter         `json:"parameters"`
	Responses   map[string]Response `json:"responses"`
	Summary     string              `json:"summary"`
	Tags        []string            `json:"tags"`
}

type Parameter struct {
	In       string    `json:"in"`
	Name     string    `json:"name"`
	Required bool      `json:"required"`
	Style    string    `json:"style"`
	Explode  bool      `json:"explode"`
	Schema   SchemaRef `json:"schema"`
}

type SchemaRef struct {
	Ref string `json:"$ref"`
}

type Response struct {
	Content     map[string]MediaType `json:"content"`
	Description string               `json:"description"`
}

type MediaType struct {
	Schema json.RawMessage `json:"schema"`
}

type Components struct {
	Schemas         map[string]json.RawMessage `json:"schemas"`
	SecuritySchemes map[string]SecurityScheme  `json:"securitySchemes"`
}

type SecurityScheme struct {
	In   string `json:"in"`
	Name string `json:"name"`
	Type string `json:"type"`
}

type Endpoint struct {
	Method      string
	Path        string
	OperationID string
}

func Load() (Document, error) {
	var doc Document
	if err := json.Unmarshal(contractJSON, &doc); err != nil {
		return Document{}, fmt.Errorf("decode embedded VCF contract: %w", err)
	}
	return doc, nil
}

func ContractJSON() []byte {
	return append([]byte(nil), contractJSON...)
}

func OfficialSourcesJSON() []byte {
	return append([]byte(nil), officialSourcesJSON...)
}

func (d Document) Endpoints() []Endpoint {
	var endpoints []Endpoint
	for path, item := range d.Paths {
		if item.Get != nil {
			endpoints = append(endpoints, Endpoint{
				Method: "GET", Path: path, OperationID: item.Get.OperationID,
			})
		}
	}
	sort.Slice(endpoints, func(i, j int) bool {
		if endpoints[i].Path == endpoints[j].Path {
			return endpoints[i].Method < endpoints[j].Method
		}
		return endpoints[i].Path < endpoints[j].Path
	})
	return endpoints
}

func (d Document) ValidatePinnedSubset() error {
	if d.OpenAPI != "3.0.1" || d.Info.Version != "9.1.0.0" {
		return fmt.Errorf("unexpected OpenAPI/version %q/%q", d.OpenAPI, d.Info.Version)
	}
	if len(d.Endpoints()) != 1 {
		return fmt.Errorf("contract must name exactly one operation, got %d", len(d.Endpoints()))
	}
	ep := d.Endpoints()[0]
	if ep.OperationID != "getAllAgentGroupConfig" ||
		ep.Method != "GET" || ep.Path != "/api/v2/agent/groups" {
		return fmt.Errorf("unexpected operation endpoint: %+v", ep)
	}
	op := d.Paths[ep.Path].Get
	if len(op.Parameters) != 1 {
		return fmt.Errorf("operation must have one pageable parameter")
	}
	p := op.Parameters[0]
	if p.In != "query" || p.Name != "pageable" || !p.Required ||
		p.Style != "form" || !p.Explode ||
		p.Schema.Ref != "#/components/schemas/Pageable" {
		return fmt.Errorf("unexpected pageable parameter: %+v", p)
	}
	scheme, ok := d.Components.SecuritySchemes["OPSTokenAuthorization"]
	if !ok || scheme.Type != "apiKey" || scheme.In != "header" ||
		!strings.EqualFold(scheme.Name, "X-JWT-Token") {
		return fmt.Errorf("unexpected token security scheme: %+v", scheme)
	}
	return nil
}
