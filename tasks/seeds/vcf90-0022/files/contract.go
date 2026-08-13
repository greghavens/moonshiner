// Package hostcommission exposes the machine-readable wire contract that the
// SDDC Manager host-commissioning client and the loopback mock are both pinned
// to.
//
// The contract in docs/contract.json is derived from the VMware Cloud
// Foundation 9.0 SDDC Manager OpenAPI specification; docs/official_sources.json
// records where it came from. Both files are embedded here so that every
// consumer in this module reads exactly one copy.
package hostcommission

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"sync"
)

//go:embed docs/contract.json
var contractJSON []byte

//go:embed docs/official_sources.json
var officialSourcesJSON []byte

// Source identifies the specification revision the contract was derived from.
type Source struct {
	Repository  string `json:"repository"`
	License     string `json:"license"`
	SpecPath    string `json:"specPath"`
	Tag         string `json:"tag"`
	Commit      string `json:"commit"`
	OpenAPI     string `json:"openapi"`
	InfoVersion string `json:"infoVersion"`
	SpecSHA256  string `json:"specSha256"`
}

// RequestBody describes the shape of an operation's request payload.
type RequestBody struct {
	Schema string `json:"schema"`
	// Shape is "object" or "array". It distinguishes a payload wrapped in an
	// enclosing object from a bare JSON array.
	Shape string `json:"shape"`
	Note  string `json:"note"`
}

// Operation is a single named operation from the specification.
type Operation struct {
	Method             string       `json:"method"`
	Path               string       `json:"path"`
	Summary            string       `json:"summary"`
	RequestContentType string       `json:"requestContentType"`
	RequestBody        *RequestBody `json:"requestBody"`
	SuccessStatus      int          `json:"successStatus"`
	ResponseSchema     string       `json:"responseSchema"`
}

// Schema is a trimmed schema description carrying the field-level facts the
// wire contract depends on.
type Schema struct {
	Type              string   `json:"type"`
	Description       string   `json:"description"`
	Required          []string `json:"required"`
	Optional          []string `json:"optional"`
	OmitEmptyOptional bool     `json:"omitEmptyOptional"`
	OmitEmptyNote     string   `json:"omitEmptyNote"`
}

// Enumeration is a value set the specification expresses informally.
type Enumeration struct {
	AppliesTo        string   `json:"appliesTo"`
	DerivedFrom      string   `json:"derivedFrom"`
	AllowedValues    []string `json:"allowedValues"`
	NotInThisRelease *struct {
		Values []string `json:"values"`
		Note   string   `json:"note"`
	} `json:"notInThisRelease"`
}

// Gate records the precheck-before-mutation rules.
type Gate struct {
	Description string   `json:"description"`
	Rules       []string `json:"rules"`
}

// Contract is the parsed docs/contract.json.
type Contract struct {
	ContractVersion string                 `json:"contractVersion"`
	Description     string                 `json:"description"`
	Source          Source                 `json:"source"`
	Operations      map[string]Operation   `json:"operations"`
	Schemas         map[string]Schema      `json:"schemas"`
	Enumerations    map[string]Enumeration `json:"enumerations"`
	Gate            Gate                   `json:"gate"`
}

var (
	loadOnce sync.Once
	loaded   *Contract
	loadErr  error
)

// Load parses and returns the embedded contract. The returned pointer is
// shared; callers must not mutate it.
func Load() (*Contract, error) {
	loadOnce.Do(func() {
		c := &Contract{}
		if err := json.Unmarshal(contractJSON, c); err != nil {
			loadErr = fmt.Errorf("parse docs/contract.json: %w", err)
			return
		}
		loaded = c
	})
	return loaded, loadErr
}

// MustLoad is Load, panicking on failure.
func MustLoad() *Contract {
	c, err := Load()
	if err != nil {
		panic(err)
	}
	return c
}

// Op returns the operation with the given operationId.
func (c *Contract) Op(operationID string) (Operation, error) {
	op, ok := c.Operations[operationID]
	if !ok {
		return Operation{}, fmt.Errorf("contract names no operation %q", operationID)
	}
	return op, nil
}

// MustOp is Op, panicking on an unknown operationId.
func (c *Contract) MustOp(operationID string) Operation {
	op, err := c.Op(operationID)
	if err != nil {
		panic(err)
	}
	return op
}

// AllowedStorageTypes returns the storage types valid for this release, in
// specification order.
func (c *Contract) AllowedStorageTypes() []string {
	out := make([]string, len(c.Enumerations["storageType"].AllowedValues))
	copy(out, c.Enumerations["storageType"].AllowedValues)
	return out
}

// StorageTypeAllowed reports whether v is a storage type this release accepts.
func (c *Contract) StorageTypeAllowed(v string) bool {
	for _, a := range c.Enumerations["storageType"].AllowedValues {
		if a == v {
			return true
		}
	}
	return false
}

// OfficialSourcesJSON returns the raw bytes of docs/official_sources.json.
func OfficialSourcesJSON() []byte {
	out := make([]byte, len(officialSourcesJSON))
	copy(out, officialSourcesJSON)
	return out
}

// ContractJSON returns the raw bytes of docs/contract.json.
func ContractJSON() []byte {
	out := make([]byte, len(contractJSON))
	copy(out, contractJSON)
	return out
}
