package vcfnetworks_test

import (
	"context"
	"net/http"
	"testing"

	"vcfnetworks"
	"vcfnetworks/mockvcf"
)

func TestUpdateVcenter(t *testing.T) {
	tests := []struct {
		name         string
		failFirst    bool
		update       vcfnetworks.VCenterUpdate
		wantBody     string
		wantRequests int
	}{
		{
			name:         "success without retry omits unset fields",
			update:       vcfnetworks.VCenterUpdate{Nickname: "Core vCenter"},
			wantBody:     `{"nickname":"Core vCenter"}`,
			wantRequests: 1,
		},
		{
			name:      "500 retry repeats body and omits optional password",
			failFirst: true,
			update: vcfnetworks.VCenterUpdate{
				Nickname:    "Core vCenter",
				Credentials: &vcfnetworks.PasswordCredentials{Username: "svc-vcf"},
			},
			wantBody:     `{"nickname":"Core vCenter","credentials":{"username":"svc-vcf"}}`,
			wantRequests: 2,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()

			server := mockvcf.NewServer(tt.failFirst)
			defer server.Close()
			client := vcfnetworks.NewClient(server.URL(), "test-token", http.DefaultClient)
			got, err := client.UpdateVcenter(context.Background(), "10000:902:42", tt.update)
			if err != nil {
				t.Fatalf("UpdateVcenter() error = %v", err)
			}
			if got.EntityID != "10000:902:42" || got.Nickname != tt.update.Nickname {
				t.Fatalf("UpdateVcenter() = %+v", got)
			}
			if server.EffectCount() != 1 {
				t.Fatalf("EffectCount() = %d, want 1", server.EffectCount())
			}

			requests := server.Requests()
			if len(requests) != tt.wantRequests {
				t.Fatalf("request count = %d, want %d", len(requests), tt.wantRequests)
			}
			for i, request := range requests {
				if request.Method != http.MethodPut || request.RequestURI != "/api/ni/data-sources/vcenters/10000:902:42" {
					t.Errorf("request %d line = %s %s", i, request.Method, request.RequestURI)
				}
				if request.Header.Get("Authorization") != "NetworkInsight test-token" || request.Header.Get("Content-Type") != "application/json" {
					t.Errorf("request %d headers = %v", i, request.Header)
				}
				if string(request.Body) != tt.wantBody {
					t.Errorf("request %d body = %q, want %q", i, request.Body, tt.wantBody)
				}
			}
		})
	}
}
