// Package verification holds the protected checks for this task.
//
// facts.go defines how docs/contract.json is normalised into digest inputs.
// The normalisation is deliberately visible; the expected digests are pinned
// as constants in contract_facts_test.go so that the contract values
// themselves are never spelled out in this repository. Deriving them is the
// job of whoever writes docs/contract.json.
package verification

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
)

// digestSalt keeps the pinned digests specific to this task.
const digestSalt = "vcf90-0018"

// Digest hashes a normalised payload under a label.
func Digest(label, payload string) string {
	sum := sha256.Sum256([]byte(digestSalt + "\x00" + label + "\x00" + payload))
	return hex.EncodeToString(sum[:])
}

// SpecRef identifies the OpenAPI document the contract was derived from.
type SpecRef struct {
	Repository string `json:"repository"`
	Path       string `json:"path"`
	Tag        string `json:"tag"`
	Commit     string `json:"commit"`
	OpenAPI    string `json:"openapi"`
	Title      string `json:"title"`
	Version    string `json:"version"`
}

// QueryParameter mirrors one entry of an operation's query parameter list.
type QueryParameter struct {
	Name          string   `json:"name"`
	Required      bool     `json:"required"`
	Deprecated    bool     `json:"deprecated"`
	Default       string   `json:"default"`
	AllowedValues []string `json:"allowedValues"`
}

// RequestBody mirrors an operation's request body definition.
type RequestBody struct {
	Required    bool     `json:"required"`
	ContentType string   `json:"contentType"`
	Schema      string   `json:"schema"`
	Properties  []string `json:"properties"`
}

// Operation is one operation named by the contract.
type Operation struct {
	OperationID     string           `json:"operationId"`
	Method          string           `json:"method"`
	Path            string           `json:"path"`
	SuccessStatus   int              `json:"successStatus"`
	ResponseSchema  string           `json:"responseSchema"`
	RequestBody     *RequestBody     `json:"requestBody"`
	QueryParameters []QueryParameter `json:"queryParameters"`
}

// Contract is the parsed docs/contract.json.
type Contract struct {
	Spec       SpecRef     `json:"spec"`
	Operations []Operation `json:"operations"`
}

// Source is one entry of docs/official_sources.json.
type Source struct {
	Title        string   `json:"title"`
	URL          string   `json:"url"`
	Repository   string   `json:"repository"`
	License      string   `json:"license"`
	Path         string   `json:"path"`
	Tag          string   `json:"tag"`
	Commit       string   `json:"commit"`
	OperationIDs []string `json:"operationIds"`
}

// Sources is the parsed docs/official_sources.json.
type Sources struct {
	Sources []Source `json:"sources"`
}

// LoadContract reads and strictly decodes docs/contract.json.
func LoadContract(path string) (*Contract, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	var c Contract
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("decode %s: %w", path, err)
	}
	return &c, nil
}

// LoadSources reads and strictly decodes docs/official_sources.json.
func LoadSources(path string) (*Sources, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	var s Sources
	if err := dec.Decode(&s); err != nil {
		return nil, fmt.Errorf("decode %s: %w", path, err)
	}
	return &s, nil
}

// Find returns the operation with the given id.
func (c *Contract) Find(operationID string) (Operation, bool) {
	for _, op := range c.Operations {
		if op.OperationID == operationID {
			return op, true
		}
	}
	return Operation{}, false
}

// SpecRefPayload normalises the spec reference: repository, path, tag, commit
// joined by "|". The commit is lower-cased.
func (c *Contract) SpecRefPayload() string {
	return strings.Join([]string{
		c.Spec.Repository,
		c.Spec.Path,
		c.Spec.Tag,
		strings.ToLower(c.Spec.Commit),
	}, "|")
}

// SpecInfoPayload normalises the document's own identity: the openapi version,
// info.title and info.version joined by "|".
func (c *Contract) SpecInfoPayload() string {
	return strings.Join([]string{c.Spec.OpenAPI, c.Spec.Title, c.Spec.Version}, "|")
}

// OperationsPayload normalises every operation to
// "operationId|METHOD|path|successStatus|responseSchema", sorted by
// operationId and joined by newlines.
func (c *Contract) OperationsPayload() string {
	lines := make([]string, 0, len(c.Operations))
	for _, op := range c.Operations {
		lines = append(lines, strings.Join([]string{
			op.OperationID,
			strings.ToUpper(op.Method),
			op.Path,
			strconv.Itoa(op.SuccessStatus),
			op.ResponseSchema,
		}, "|"))
	}
	sort.Strings(lines)
	return strings.Join(lines, "\n")
}

// QueryPayload normalises an operation's query parameters, in the order they
// are listed, to "name|required|deprecated|default" joined by newlines.
func (c *Contract) QueryPayload(operationID string) string {
	op, ok := c.Find(operationID)
	if !ok {
		return ""
	}
	lines := make([]string, 0, len(op.QueryParameters))
	for _, p := range op.QueryParameters {
		lines = append(lines, strings.Join([]string{
			p.Name,
			strconv.FormatBool(p.Required),
			strconv.FormatBool(p.Deprecated),
			p.Default,
		}, "|"))
	}
	return strings.Join(lines, "\n")
}

// AllowedValuesPayload normalises one query parameter's allowed values, in the
// order they are listed, joined by ",".
func (c *Contract) AllowedValuesPayload(operationID, parameter string) string {
	op, ok := c.Find(operationID)
	if !ok {
		return ""
	}
	for _, p := range op.QueryParameters {
		if p.Name == parameter {
			return strings.Join(p.AllowedValues, ",")
		}
	}
	return ""
}

// RequestBodiesPayload normalises every operation that carries a request body
// to "operationId|required|contentType|schema|prop,prop,...", sorted by
// operationId and joined by newlines.
func (c *Contract) RequestBodiesPayload() string {
	var lines []string
	for _, op := range c.Operations {
		if op.RequestBody == nil {
			continue
		}
		lines = append(lines, strings.Join([]string{
			op.OperationID,
			strconv.FormatBool(op.RequestBody.Required),
			op.RequestBody.ContentType,
			op.RequestBody.Schema,
			strings.Join(op.RequestBody.Properties, ","),
		}, "|"))
	}
	sort.Strings(lines)
	return strings.Join(lines, "\n")
}
