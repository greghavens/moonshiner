package mock

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

// spec identifies the OpenAPI document the contract was derived from.
type spec struct {
	Repository string `json:"repository"`
	Path       string `json:"path"`
	Tag        string `json:"tag"`
	Commit     string `json:"commit"`
	OpenAPI    string `json:"openapi"`
	Title      string `json:"title"`
	Version    string `json:"version"`
}

// queryParameter is one query parameter of an operation.
type queryParameter struct {
	Name     string `json:"name"`
	Type     string `json:"type"`
	Required bool   `json:"required"`
}

// requestBody describes an operation's request body.
type requestBody struct {
	Required    bool     `json:"required"`
	ContentType string   `json:"contentType"`
	Schema      string   `json:"schema"`
	Properties  []string `json:"properties"`
}

// operation is one operation the contract names.
type operation struct {
	OperationID     string           `json:"operationId"`
	Method          string           `json:"method"`
	Path            string           `json:"path"`
	Summary         string           `json:"summary"`
	SuccessStatus   int              `json:"successStatus"`
	ResponseSchema  string           `json:"responseSchema"`
	ErrorStatuses   []int            `json:"errorStatuses"`
	RequestBody     *requestBody     `json:"requestBody"`
	QueryParameters []queryParameter `json:"queryParameters"`
}

func (o operation) hasQueryParameter(name string) bool {
	for _, p := range o.QueryParameters {
		if p.Name == name {
			return true
		}
	}
	return false
}

func (o operation) allowsProperty(name string) bool {
	if o.RequestBody == nil {
		return false
	}
	for _, p := range o.RequestBody.Properties {
		if p == name {
			return true
		}
	}
	return false
}

// pagination names the fields the paged operation is driven by.
type pagination struct {
	OperationID        string `json:"operationId"`
	PageParameter      string `json:"pageParameter"`
	SizeParameter      string `json:"sizeParameter"`
	ElementsField      string `json:"elementsField"`
	MetadataField      string `json:"metadataField"`
	PageNumberField    string `json:"pageNumberField"`
	PageSizeField      string `json:"pageSizeField"`
	TotalElementsField string `json:"totalElementsField"`
	TotalPagesField    string `json:"totalPagesField"`
}

// contract is docs/contract.json: the operations this server is pinned to.
type contract struct {
	Spec       spec        `json:"spec"`
	Operations []operation `json:"operations"`
	Pagination pagination  `json:"pagination"`
}

// loadContract reads and strictly decodes a contract document.
func loadContract(path string) (*contract, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	var c contract
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("decode contract %s: %w", path, err)
	}
	if len(c.Operations) == 0 {
		return nil, fmt.Errorf("contract %s names no operations", path)
	}
	if c.Pagination.PageParameter == "" || c.Pagination.SizeParameter == "" {
		return nil, fmt.Errorf("contract %s does not name its paging parameters", path)
	}
	return &c, nil
}

// find returns the operation with the given id.
func (c *contract) find(operationID string) (operation, bool) {
	for _, op := range c.Operations {
		if op.OperationID == operationID {
			return op, true
		}
	}
	return operation{}, false
}

// route returns the operation served at a method and path. Only the pairs the
// contract names are served.
func (c *contract) route(method, path string) (operation, bool) {
	for _, op := range c.Operations {
		if op.Method == method && op.Path == path {
			return op, true
		}
	}
	return operation{}, false
}
