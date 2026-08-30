package mocklogs

import (
	"net/http"
	"testing"
)

func TestOnlyContractRoutesAreServed(t *testing.T) {
	tests := []struct {
		method string
		path   string
		want   int
	}{
		{http.MethodPost, "/api/v2/deployment/join", http.StatusOK},
		{http.MethodPost, "/api/v2/deployment/waitUntilStarted", http.StatusOK},
		{http.MethodGet, "/api/v2/deployment/join", http.StatusNotFound},
		{http.MethodPost, "/api/v2/deployment/new", http.StatusNotFound},
	}

	for _, tc := range tests {
		t.Run(tc.method+" "+tc.path, func(t *testing.T) {
			server, err := New(http.StatusOK)
			if err != nil {
				t.Fatal(err)
			}
			defer server.Close()
			req, err := http.NewRequest(tc.method, server.URL()+tc.path, nil)
			if err != nil {
				t.Fatal(err)
			}
			resp, err := server.Client().Do(req)
			if err != nil {
				t.Fatal(err)
			}
			resp.Body.Close()
			if resp.StatusCode != tc.want {
				t.Fatalf("status = %d, want %d", resp.StatusCode, tc.want)
			}
		})
	}
}
