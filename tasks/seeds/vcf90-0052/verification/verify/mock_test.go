package verify

import (
	"io"
	"net/http"
	"strings"
	"testing"

	"example.com/vcf90/gosc/internal/mockvc"
)

// TestLoopbackServesOnlyContractedOperations exercises the loopback vCenter itself:
// it answers the two operations the contract names and nothing else, and it holds
// the request body to the contracted schemas.
func TestLoopbackServesOnlyContractedOperations(t *testing.T) {
	srv := mockvc.New(t, contractPath)

	const validBody = `{"spec":{"configuration_spec":{"linux_config":{"hostname":{"type":"FIXED","fixed_name":"h"},"domain":"d"}},"global_dns_settings":{},"interfaces":[]}}`

	tests := []struct {
		name    string
		method  string
		target  string
		body    string
		ct      string
		session string
		want    int
	}{
		{"precheck", "POST", "/api/vcenter/vm/vm-1/guest/customization?action=check", "", "", sessionID, 200},
		{"mutation", "PUT", "/api/vcenter/vm/vm-1/guest/customization", validBody, "application/json", sessionID, 204},
		{"precheck without a session", "POST", "/api/vcenter/vm/vm-1/guest/customization?action=check", "", "", "", 401},
		{"precheck with a body", "POST", "/api/vcenter/vm/vm-1/guest/customization?action=check", "{}", "application/json", sessionID, 400},
		{"an operation the contract does not name", "GET", "/api/vcenter/vm/vm-1/guest/customization", "", "", sessionID, 405},
		{"a resource the contract does not name", "GET", "/api/vcenter/vm/vm-1/guest/power", "", "", sessionID, 404},
		{"a resource added after 9.0", "POST", "/api/vcenter/vm/vm-1/guest/customization-live?action=run", "{}", "application/json", sessionID, 404},
		{"an unknown property", "PUT", "/api/vcenter/vm/vm-1/guest/customization", `{"specification":{}}`, "application/json", sessionID, 400},
		{"a missing required property", "PUT", "/api/vcenter/vm/vm-1/guest/customization", `{"spec":{"global_dns_settings":{},"interfaces":[]}}`, "application/json", sessionID, 400},
		{"a nulled optional property", "PUT", "/api/vcenter/vm/vm-1/guest/customization", `{"name":null,"spec":{"configuration_spec":{},"global_dns_settings":{},"interfaces":[]}}`, "application/json", sessionID, 400},
		{"an enum value the 9.1 revision added", "PUT", "/api/vcenter/vm/vm-1/guest/customization",
			`{"spec":{"configuration_spec":{"linux_config":{"hostname":{"type":"FIXED","fixed_name":"h"},"domain":"d"}},"global_dns_settings":{},"interfaces":[{"adapter":{"ipv4":{"type":"DISABLED"}}}]}}`,
			"application/json", sessionID, 400},
		{"a body without a content type", "PUT", "/api/vcenter/vm/vm-1/guest/customization", validBody, "", sessionID, 400},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			var body io.Reader
			if tc.body != "" {
				body = strings.NewReader(tc.body)
			}
			req, err := http.NewRequest(tc.method, srv.URL()+tc.target, body)
			if err != nil {
				t.Fatal(err)
			}
			if tc.ct != "" {
				req.Header.Set("Content-Type", tc.ct)
			}
			if tc.session != "" {
				req.Header.Set(srv.Contract().Auth.Name, tc.session)
			}
			resp, err := srv.Client().Do(req)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			payload, _ := io.ReadAll(resp.Body)
			if resp.StatusCode != tc.want {
				t.Errorf("%s %s -> %d, want %d: %s", tc.method, tc.target, resp.StatusCode, tc.want, payload)
			}
		})
	}
}
