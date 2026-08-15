package acceptance_test

import (
	"strings"
	"testing"

	vcfautomation "example.com/vcfautomation"
)

func TestReferenceDerivedContract(t *testing.T) {
	contract, err := vcfautomation.Contract()
	if err != nil {
		t.Fatal(err)
	}
	if contract.ProductVersion != "9.0" {
		t.Fatalf("product version = %q", contract.ProductVersion)
	}
	if contract.Provenance.Kind != "reference-documentation" || !strings.Contains(contract.Provenance.Statement, "not a published API specification") {
		t.Fatalf("contract provenance is not plain about its reference origin: %#v", contract.Provenance)
	}
	if len(contract.Operations) != 1 {
		t.Fatalf("operations = %d, want 1", len(contract.Operations))
	}
	operation := contract.Operations[0]
	if operation.OperationID != "patchDeployment" || operation.Method != "PATCH" || operation.Path != "/deployment/api/deployments/{deploymentId}" {
		t.Fatalf("operation does not match pinned reference: %#v", operation)
	}
	if operation.Authentication.Type != "http" || operation.Authentication.Scheme != "bearer" {
		t.Fatalf("authentication = %#v", operation.Authentication)
	}
	if operation.Request.ContentType != "application/json" || operation.Request.Body.AdditionalProperties {
		t.Fatalf("request contract = %#v", operation.Request)
	}
	if len(operation.Request.Body.Required) != 0 {
		t.Fatalf("optional fields unexpectedly required: %v", operation.Request.Body.Required)
	}
	for _, name := range []string{"description", "iconId", "name"} {
		property, ok := operation.Request.Body.Properties[name]
		if !ok || property.Required || property.Type != "string" {
			t.Fatalf("property %q = %#v, present %v", name, property, ok)
		}
	}

	sources, err := vcfautomation.Sources()
	if err != nil {
		t.Fatal(err)
	}
	if len(sources.Sources) != 1 {
		t.Fatalf("official source count = %d, want 1", len(sources.Sources))
	}
	source := sources.Sources[0]
	if source.URL != "https://developer.broadcom.com/xapis/vm-apps-org-deployment/9.0/deployment/api/deployments/deploymentId/patch/" {
		t.Fatalf("source URL = %q", source.URL)
	}
	if source.Operation != "PATCH /deployment/api/deployments/{deploymentId} — Patch Deployment" || source.Fetched != "2026-08-13" {
		t.Fatalf("source record = %#v", source)
	}
}
