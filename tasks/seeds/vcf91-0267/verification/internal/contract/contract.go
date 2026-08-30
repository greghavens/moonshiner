// Package contract loads docs/contract.json, the wire contract derived from the
// VCF Operations OpenAPI specification recorded in docs/official_sources.json.
//
// Both the in-process mock (internal/mockops) and the protected verifier read the
// contract through this package, so neither can drift from the specification.
package contract

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// Contract is the whole of docs/contract.json.
type Contract struct {
	ContractVersion string `json:"contractVersion"`
	Description     string `json:"description"`

	API struct {
		Title    string `json:"title"`
		Version  string `json:"version"`
		OpenAPI  string `json:"openapi"`
		BasePath string `json:"basePath"`
	} `json:"api"`

	Authorization struct {
		SchemeName                    string   `json:"schemeName"`
		Type                          string   `json:"type"`
		In                            string   `json:"in"`
		HeaderName                    string   `json:"headerName"`
		HeaderValueTemplate           string   `json:"headerValueTemplate"`
		HeaderValueTemplateProvenance string   `json:"headerValueTemplateProvenance"`
		AppliesTo                     []string `json:"appliesTo"`
		ExemptOperations              []string `json:"exemptOperations"`
	} `json:"authorization"`

	Operations map[string]Operation           `json:"operations"`
	Schemas    map[string]map[string]Property `json:"schemas"`

	ReportStatus struct {
		Provenance  string   `json:"provenance"`
		NonTerminal []string `json:"nonTerminal"`
		Terminal    []string `json:"terminal"`
		Successful  string   `json:"successful"`
	} `json:"reportStatus"`

	OmitEmptyRule string `json:"omitEmptyRule"`
}

// Operation is one contract operation, keyed in the contract by its operationId.
type Operation struct {
	OperationID     string              `json:"operationId"`
	Method          string              `json:"method"`
	Path            string              `json:"path"`
	Summary         string              `json:"summary"`
	PathParameters  []Param             `json:"pathParameters"`
	QueryParameters []Param             `json:"queryParameters"`
	RequestBody     *RequestBody        `json:"requestBody"`
	Responses       map[string]Response `json:"responses"`
}

// Param is a path or query parameter.
type Param struct {
	Name     string `json:"name"`
	Required bool   `json:"required"`
	Type     string `json:"type"`
	Format   string `json:"format,omitempty"`
}

// RequestBody describes the JSON body an operation accepts.
type RequestBody struct {
	Required    bool   `json:"required"`
	ContentType string `json:"contentType"`
	Schema      string `json:"schema"`
	// RequiredProperties come straight from the specification's `required` list.
	RequiredProperties []string `json:"requiredProperties"`
	// AllowedProperties is the subset of schema properties a client may set on a
	// request. Anything outside this set is server-populated and must not be sent.
	AllowedProperties []string `json:"allowedProperties"`
}

// Response describes one documented response code.
type Response struct {
	Description  string   `json:"description"`
	ContentTypes []string `json:"contentTypes"`
	Schema       string   `json:"schema,omitempty"`
}

// Property is one property of a contract schema.
type Property struct {
	Required    bool      `json:"required"`
	Type        string    `json:"type"`
	Format      string    `json:"format,omitempty"`
	Schema      string    `json:"schema,omitempty"`
	Items       *ItemSpec `json:"items,omitempty"`
	Description string    `json:"description"`
}

// ItemSpec describes the element type of an array property.
type ItemSpec struct {
	Type   string `json:"type,omitempty"`
	Schema string `json:"schema,omitempty"`
}

// FullPath returns the operation path including the API base path, e.g.
// "/suite-api/api/reports/{id}".
func (c *Contract) FullPath(op Operation) string {
	return c.API.BasePath + op.Path
}

var placeholder = regexp.MustCompile(`\{[^/{}]+\}`)

// PathMatcher compiles the operation's templated path into an anchored regexp
// whose capture groups are the path parameters, in declaration order.
func (c *Contract) PathMatcher(op Operation) *regexp.Regexp {
	full := c.FullPath(op)
	var b strings.Builder
	b.WriteString("^")
	last := 0
	for _, loc := range placeholder.FindAllStringIndex(full, -1) {
		b.WriteString(regexp.QuoteMeta(full[last:loc[0]]))
		b.WriteString(`([^/]+)`)
		last = loc[1]
	}
	b.WriteString(regexp.QuoteMeta(full[last:]))
	b.WriteString("$")
	return regexp.MustCompile(b.String())
}

// AuthHeaderValue renders the Authorization header value for a token.
func (c *Contract) AuthHeaderValue(token string) string {
	return strings.ReplaceAll(c.Authorization.HeaderValueTemplate, "{token}", token)
}

// RequiresAuth reports whether the operation must carry the Authorization header.
func (c *Contract) RequiresAuth(operationID string) bool {
	for _, id := range c.Authorization.AppliesTo {
		if id == operationID {
			return true
		}
	}
	return false
}

// IsTerminal reports whether a report status ends polling.
func (c *Contract) IsTerminal(status string) bool {
	for _, s := range c.ReportStatus.Terminal {
		if s == status {
			return true
		}
	}
	return false
}

// Load reads a contract from an explicit file path.
func Load(path string) (*Contract, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	var c Contract
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("decode contract %s: %w", path, err)
	}
	if len(c.Operations) == 0 {
		return nil, fmt.Errorf("contract %s declares no operations", path)
	}
	for id, op := range c.Operations {
		if op.OperationID != id {
			return nil, fmt.Errorf("contract %s: operation key %q disagrees with operationId %q", path, id, op.OperationID)
		}
	}
	return &c, nil
}

// LoadDefault finds docs/contract.json by walking up from the working directory
// to the module root, so it resolves the same way from any package's test.
func LoadDefault() (*Contract, error) {
	root, err := moduleRoot()
	if err != nil {
		return nil, err
	}
	return Load(filepath.Join(root, "docs", "contract.json"))
}

// ModuleRoot returns the directory holding go.mod.
func ModuleRoot() (string, error) { return moduleRoot() }

func moduleRoot() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("no go.mod found above working directory")
		}
		dir = parent
	}
}
