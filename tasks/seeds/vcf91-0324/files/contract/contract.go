// Package contract holds the machine-readable REST contract for the subset of
// the VCF Automation API that this client speaks.
//
// VCF Automation (the VMware Cloud Foundation 9.1 successor to vRealize and
// Aria Automation) does not publish a machine-readable specification in the
// vmware/vcf-api-specs repository. The contract is therefore transcribed by
// hand from the xAPIs reference documentation on developer.broadcom.com, and
// every operation must be traceable to the reference page it came from via
// docs/official_sources.json.
//
// This package is complete; it defines the format that docs/contract.json must
// follow. Both the client and the loopback mock are driven from the loaded
// contract so that neither can drift from the documented wire shape.
package contract

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sort"
	"strings"
)

// SchemaVersion is the only contract schema version this package accepts.
const SchemaVersion = "1"

// SourceKindReferenceDocumentation is the required value of Source.Kind. It
// records that the contract was derived from reference documentation pages
// rather than from a published specification.
const SourceKindReferenceDocumentation = "reference-documentation"

// Operation IDs that docs/contract.json must define. The client, the mock and
// the verifier all address operations by these IDs.
const (
	// OpAuthToken exchanges a long-lived API (refresh) token for a
	// short-lived access token.
	OpAuthToken = "auth.token"
	// OpDeploymentsList lists deployments, one page at a time.
	OpDeploymentsList = "deployments.list"
	// OpDeploymentsGet fetches a single deployment by ID.
	OpDeploymentsGet = "deployments.get"
	// OpCatalogItemsList lists catalog items, one page at a time.
	OpCatalogItemsList = "catalog.items.list"
	// OpCatalogItemsRequest requests a new deployment from a catalog item.
	OpCatalogItemsRequest = "catalog.items.request"
)

// RequiredOperations lists every operation ID that must be present in
// docs/contract.json, exactly once.
var RequiredOperations = []string{
	OpAuthToken,
	OpDeploymentsList,
	OpDeploymentsGet,
	OpCatalogItemsList,
	OpCatalogItemsRequest,
}

// Content types a request body may declare.
const (
	ContentTypeJSON = "application/json"
	ContentTypeForm = "application/x-www-form-urlencoded"
)

// Contract is the whole of docs/contract.json.
type Contract struct {
	SchemaVersion string      `json:"schema_version"`
	Product       string      `json:"product"`
	Release       string      `json:"release"`
	Source        Source      `json:"source"`
	Operations    []Operation `json:"operations"`
}

// Source records where the contract came from. VCF Automation has no published
// specification, so Kind must be SourceKindReferenceDocumentation and
// SpecificationAvailable must be false.
type Source struct {
	Kind                   string `json:"kind"`
	SpecificationAvailable bool   `json:"specification_available"`
	Portal                 string `json:"portal"`
	// Statement must say plainly, in prose, that this contract was derived
	// from reference documentation rather than from a published
	// specification.
	Statement string `json:"statement"`
}

// Operation is one documented REST operation.
type Operation struct {
	ID         string  `json:"id"`
	Summary    string  `json:"summary"`
	Method     string  `json:"method"`
	Path       string  `json:"path"`
	PathParams []Field `json:"path_params,omitempty"`
	// Query lists every query parameter the operation accepts. The mock
	// rejects any query parameter that is not listed here.
	Query []Field `json:"query,omitempty"`
	// RequestBody is nil for operations that take no body.
	RequestBody *Body    `json:"request_body,omitempty"`
	Response    Response `json:"response"`
}

// Body describes a request body.
type Body struct {
	ContentType string `json:"content_type"`
	// Fields lists every field the body may carry. The mock rejects any
	// field that is not listed here.
	Fields []Field `json:"fields"`
}

// Response describes a success response body.
type Response struct {
	ContentType string `json:"content_type"`
	// Kind is "object" or "array".
	Kind   string  `json:"kind"`
	Fields []Field `json:"fields"`
}

// Kinds a Response may take.
const (
	KindObject = "object"
	KindArray  = "array"
)

// Field is one parameter or body field.
type Field struct {
	Name        string `json:"name"`
	Type        string `json:"type"`
	Required    bool   `json:"required"`
	Default     string `json:"default,omitempty"`
	Deprecated  bool   `json:"deprecated,omitempty"`
	Description string `json:"description,omitempty"`
}

// Load reads and validates a contract from path.
func Load(path string) (*Contract, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("contract: %w", err)
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	var c Contract
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("contract %s: %w", path, err)
	}
	if err := c.Validate(); err != nil {
		return nil, fmt.Errorf("contract %s: %w", path, err)
	}
	return &c, nil
}

var allowedMethods = map[string]bool{
	http.MethodGet: true, http.MethodPost: true, http.MethodPut: true,
	http.MethodPatch: true, http.MethodDelete: true,
}

// Validate reports whether the contract is structurally sound. It deliberately
// checks only structure; whether the documented values are *correct* is the
// job of the sources in docs/official_sources.json.
func (c *Contract) Validate() error {
	if c.SchemaVersion != SchemaVersion {
		return fmt.Errorf("schema_version must be %q, got %q", SchemaVersion, c.SchemaVersion)
	}
	if strings.TrimSpace(c.Product) == "" {
		return fmt.Errorf("product must not be empty")
	}
	if strings.TrimSpace(c.Release) == "" {
		return fmt.Errorf("release must not be empty")
	}
	if c.Source.Kind != SourceKindReferenceDocumentation {
		return fmt.Errorf("source.kind must be %q, got %q", SourceKindReferenceDocumentation, c.Source.Kind)
	}
	if c.Source.SpecificationAvailable {
		return fmt.Errorf("source.specification_available must be false: VCF Automation publishes no specification")
	}
	if len(strings.TrimSpace(c.Source.Statement)) < 80 {
		return fmt.Errorf("source.statement must state plainly that the contract derives from reference documentation rather than a published specification")
	}

	seen := map[string]bool{}
	for i := range c.Operations {
		op := &c.Operations[i]
		if op.ID == "" {
			return fmt.Errorf("operations[%d]: id must not be empty", i)
		}
		if seen[op.ID] {
			return fmt.Errorf("operation %q defined more than once", op.ID)
		}
		seen[op.ID] = true
		if err := op.validate(); err != nil {
			return fmt.Errorf("operation %q: %w", op.ID, err)
		}
	}
	var missing []string
	for _, id := range RequiredOperations {
		if !seen[id] {
			missing = append(missing, id)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return fmt.Errorf("missing required operations: %s", strings.Join(missing, ", "))
	}
	return nil
}

func (op *Operation) validate() error {
	if !allowedMethods[op.Method] {
		return fmt.Errorf("method %q must be an upper-case HTTP method", op.Method)
	}
	if !strings.HasPrefix(op.Path, "/") {
		return fmt.Errorf("path %q must begin with /", op.Path)
	}
	if strings.HasSuffix(op.Path, "/") && op.Path != "/" {
		return fmt.Errorf("path %q must not have a trailing slash", op.Path)
	}
	if strings.TrimSpace(op.Summary) == "" {
		return fmt.Errorf("summary must not be empty")
	}

	names, err := PathParamNames(op.Path)
	if err != nil {
		return err
	}
	declared := map[string]bool{}
	for _, f := range op.PathParams {
		if declared[f.Name] {
			return fmt.Errorf("path_params: %q declared twice", f.Name)
		}
		declared[f.Name] = true
		if !f.Required {
			return fmt.Errorf("path_params: %q must be required", f.Name)
		}
	}
	for _, n := range names {
		if !declared[n] {
			return fmt.Errorf("path placeholder {%s} is not declared in path_params", n)
		}
	}
	for n := range declared {
		if !contains(names, n) {
			return fmt.Errorf("path_params declares %q which does not appear in the path", n)
		}
	}

	if err := uniqueFields("query", op.Query); err != nil {
		return err
	}
	if op.RequestBody != nil {
		b := op.RequestBody
		if b.ContentType != ContentTypeJSON && b.ContentType != ContentTypeForm {
			return fmt.Errorf("request_body.content_type %q must be %q or %q", b.ContentType, ContentTypeJSON, ContentTypeForm)
		}
		if len(b.Fields) == 0 {
			return fmt.Errorf("request_body declares no fields")
		}
		if err := uniqueFields("request_body.fields", b.Fields); err != nil {
			return err
		}
	}
	if op.Response.Kind != KindObject && op.Response.Kind != KindArray {
		return fmt.Errorf("response.kind must be %q or %q, got %q", KindObject, KindArray, op.Response.Kind)
	}
	if op.Response.ContentType == "" {
		return fmt.Errorf("response.content_type must not be empty")
	}
	if len(op.Response.Fields) == 0 {
		return fmt.Errorf("response declares no fields")
	}
	return uniqueFields("response.fields", op.Response.Fields)
}

func uniqueFields(what string, fields []Field) error {
	seen := map[string]bool{}
	for _, f := range fields {
		if strings.TrimSpace(f.Name) == "" {
			return fmt.Errorf("%s: field with empty name", what)
		}
		if seen[f.Name] {
			return fmt.Errorf("%s: %q declared twice", what, f.Name)
		}
		seen[f.Name] = true
		if strings.TrimSpace(f.Type) == "" {
			return fmt.Errorf("%s: field %q has no type", what, f.Name)
		}
	}
	return nil
}

// PathParamNames returns the {placeholder} names in a path template, in order.
func PathParamNames(path string) ([]string, error) {
	var names []string
	rest := path
	for {
		open := strings.Index(rest, "{")
		if open < 0 {
			if strings.Contains(rest, "}") {
				return nil, fmt.Errorf("path %q has an unmatched }", path)
			}
			return names, nil
		}
		rest = rest[open+1:]
		close := strings.Index(rest, "}")
		if close < 0 {
			return nil, fmt.Errorf("path %q has an unmatched {", path)
		}
		name := rest[:close]
		if name == "" || strings.ContainsAny(name, "{}/") {
			return nil, fmt.Errorf("path %q has a malformed placeholder", path)
		}
		names = append(names, name)
		rest = rest[close+1:]
	}
}

// Operation returns the operation with the given ID.
func (c *Contract) Operation(id string) (*Operation, error) {
	for i := range c.Operations {
		if c.Operations[i].ID == id {
			return &c.Operations[i], nil
		}
	}
	return nil, fmt.Errorf("contract: no operation %q", id)
}

// MustOperation is Operation, panicking on error. Intended for tests.
func (c *Contract) MustOperation(id string) *Operation {
	op, err := c.Operation(id)
	if err != nil {
		panic(err)
	}
	return op
}

// ExpandPath substitutes path parameters into the operation's path template.
// It returns an error if a declared parameter is missing from params or if
// params carries a name the template does not use.
func (op *Operation) ExpandPath(params map[string]string) (string, error) {
	names, err := PathParamNames(op.Path)
	if err != nil {
		return "", err
	}
	for k := range params {
		if !contains(names, k) {
			return "", fmt.Errorf("operation %q: path takes no parameter %q", op.ID, k)
		}
	}
	out := op.Path
	for _, n := range names {
		v, ok := params[n]
		if !ok || v == "" {
			return "", fmt.Errorf("operation %q: missing path parameter %q", op.ID, n)
		}
		out = strings.ReplaceAll(out, "{"+n+"}", v)
	}
	return out, nil
}

// QueryField returns the declared query parameter with the given name.
func (op *Operation) QueryField(name string) (*Field, bool) {
	for i := range op.Query {
		if op.Query[i].Name == name {
			return &op.Query[i], true
		}
	}
	return nil, false
}

// BodyField returns the declared request body field with the given name.
func (op *Operation) BodyField(name string) (*Field, bool) {
	if op.RequestBody == nil {
		return nil, false
	}
	for i := range op.RequestBody.Fields {
		if op.RequestBody.Fields[i].Name == name {
			return &op.RequestBody.Fields[i], true
		}
	}
	return nil, false
}

// FieldNames returns the names of fields, in declaration order.
func FieldNames(fields []Field) []string {
	out := make([]string, 0, len(fields))
	for _, f := range fields {
		out = append(out, f.Name)
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
