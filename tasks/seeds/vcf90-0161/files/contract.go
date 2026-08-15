package vcfautomation

import (
	"embed"
	"encoding/json"
	"fmt"
)

//go:embed docs/contract.json docs/official_sources.json
var documentation embed.FS

type APIContract struct {
	Title          string      `json:"title"`
	Product        string      `json:"product"`
	ProductVersion string      `json:"productVersion"`
	Provenance     Provenance  `json:"provenance"`
	Operations     []Operation `json:"operations"`
}

type Provenance struct {
	Kind      string `json:"kind"`
	Statement string `json:"statement"`
}

type Operation struct {
	OperationID    string          `json:"operationId"`
	ReferenceName  string          `json:"referenceName"`
	Method         string          `json:"method"`
	Path           string          `json:"path"`
	Authentication Authentication  `json:"authentication"`
	Request        ContractRequest `json:"request"`
	Responses      map[string]any  `json:"responses"`
}

type Authentication struct {
	Type   string `json:"type"`
	Scheme string `json:"scheme"`
}

type ContractRequest struct {
	ContentType    string                   `json:"contentType"`
	PathParameters map[string]PathParameter `json:"pathParameters"`
	Body           ObjectSchema             `json:"body"`
}

type PathParameter struct {
	Type     string `json:"type"`
	Required bool   `json:"required"`
}

type ObjectSchema struct {
	Type                 string                    `json:"type"`
	Required             []string                  `json:"required"`
	AdditionalProperties bool                      `json:"additionalProperties"`
	Properties           map[string]PropertySchema `json:"properties"`
}

type PropertySchema struct {
	Type      string `json:"type"`
	Required  bool   `json:"required"`
	Format    string `json:"format,omitempty"`
	MinLength int    `json:"minLength,omitempty"`
	MaxLength int    `json:"maxLength,omitempty"`
}

type OfficialSources struct {
	Sources []OfficialSource `json:"sources"`
}

type OfficialSource struct {
	URL       string `json:"url"`
	Operation string `json:"operation"`
	Fetched   string `json:"fetched"`
}

func Contract() (APIContract, error) {
	var contract APIContract
	if err := readDocumentation("docs/contract.json", &contract); err != nil {
		return APIContract{}, err
	}
	return contract, nil
}

func Sources() (OfficialSources, error) {
	var sources OfficialSources
	if err := readDocumentation("docs/official_sources.json", &sources); err != nil {
		return OfficialSources{}, err
	}
	return sources, nil
}

func readDocumentation(name string, target any) error {
	data, err := documentation.ReadFile(name)
	if err != nil {
		return fmt.Errorf("read embedded %s: %w", name, err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		return fmt.Errorf("decode embedded %s: %w", name, err)
	}
	return nil
}
