package vcfops

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// contractPath is the local, spec-derived REST contract. Its provenance (spec
// path, repository commit sha, operationIds) is in docs/official_sources.json.
const contractPath = "../docs/contract.json"

type contract struct {
	ContractVersion int `json:"contract_version"`
	Source          struct {
		Repository string `json:"repository"`
		CommitSHA  string `json:"commit_sha"`
		SpecPath   string `json:"spec_path"`
		OpenAPI    string `json:"openapi"`
		APIVersion string `json:"api_version"`
	} `json:"source"`
	BasePath string `json:"base_path"`
	Security struct {
		Scheme      string `json:"scheme"`
		Type        string `json:"type"`
		In          string `json:"in"`
		Name        string `json:"name"`
		TokenPrefix string `json:"token_prefix"`
	} `json:"security"`
	RequestEncoding struct {
		ContentType         string `json:"content_type"`
		Accept              string `json:"accept"`
		UnsetOptionalFields string `json:"unset_optional_fields"`
	} `json:"request_encoding"`
	Operations []contractOperation `json:"operations"`
}

type contractOperation struct {
	OperationID   string `json:"operationId"`
	Method        string `json:"method"`
	Path          string `json:"path"`
	Authenticated bool   `json:"authenticated"`
	Request       struct {
		Required           bool     `json:"required"`
		ContentType        string   `json:"content_type"`
		Schema             string   `json:"schema"`
		RequiredProperties []string `json:"required_properties"`
		Properties         map[string]struct {
			Type          string `json:"type"`
			Required      bool   `json:"required"`
			OmitWhenUnset bool   `json:"omit_when_unset"`
		} `json:"properties"`
	} `json:"request"`
	Success struct {
		Status int `json:"status"`
	} `json:"success"`
}

// fullPath is the wire path a request for this operation must target.
func (o contractOperation) fullPath(basePath string) string { return basePath + o.Path }

func loadContract(t *testing.T) *contract {
	t.Helper()
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read %s: %v", filepath.Clean(contractPath), err)
	}
	var c contract
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatalf("decode %s: %v", contractPath, err)
	}
	if len(c.Operations) == 0 {
		t.Fatalf("%s names no operations", contractPath)
	}
	return &c
}

func (c *contract) operation(t *testing.T, operationID string) contractOperation {
	t.Helper()
	for _, op := range c.Operations {
		if op.OperationID == operationID {
			return op
		}
	}
	t.Fatalf("contract does not name operationId %q", operationID)
	return contractOperation{}
}

// TestClientMatchesContract pins the client's compiled-in wire constants to the
// spec-derived contract, so the contract file is the single source of truth for
// paths and the Authorization scheme.
func TestClientMatchesContract(t *testing.T) {
	c := loadContract(t)

	if got, want := basePath, c.BasePath; got != want {
		t.Errorf("basePath = %q, contract base_path = %q", got, want)
	}
	if got, want := authHeader, c.Security.Name; got != want {
		t.Errorf("authHeader = %q, contract security.name = %q", got, want)
	}
	if got, want := tokenPrefix, c.Security.TokenPrefix; got != want {
		t.Errorf("tokenPrefix = %q, contract security.token_prefix = %q", got, want)
	}
	if got, want := contentTypeJSON, c.RequestEncoding.ContentType; got != want {
		t.Errorf("contentTypeJSON = %q, contract request_encoding.content_type = %q", got, want)
	}

	tests := []struct {
		operationID string
		clientPath  string
	}{
		{"acquireToken", acquireTokenPath},
		{"getCurrentUser", currentUserPath},
		{"releaseToken", releaseTokenPath},
	}
	for _, tc := range tests {
		t.Run(tc.operationID, func(t *testing.T) {
			op := c.operation(t, tc.operationID)
			if got, want := tc.clientPath, op.fullPath(c.BasePath); got != want {
				t.Errorf("client path = %q, contract path = %q", got, want)
			}
		})
	}
}
