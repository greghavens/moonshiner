package contractmock

import (
	"net/http"
	"testing"
)

func TestOnlyContractedOperationsAreServed(t *testing.T) {
	tests := []struct {
		name   string
		method string
		path   string
		want   int
	}{
		{name: "updateVcenter", method: http.MethodPut, path: BasePath + "/data-sources/vcenters/vc-1", want: http.StatusOK},
		{name: "enableVcenter", method: http.MethodPost, path: BasePath + "/data-sources/vcenters/vc-1/enable", want: http.StatusOK},
		{name: "uncontracted version endpoint", method: http.MethodGet, path: BasePath + "/info/version", want: http.StatusNotFound},
		{name: "uncontracted disable operation", method: http.MethodPost, path: BasePath + "/data-sources/vcenters/vc-1/disable", want: http.StatusNotFound},
		{name: "uncontracted read method", method: http.MethodGet, path: BasePath + "/data-sources/vcenters/vc-1", want: http.StatusNotFound},
	}

	server := New(Plan{})
	t.Cleanup(server.Close)

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request, err := http.NewRequest(test.method, server.URL()+test.path, nil)
			if err != nil {
				t.Fatalf("new request: %v", err)
			}
			response, err := server.Client().Do(request)
			if err != nil {
				t.Fatalf("send request: %v", err)
			}
			_ = response.Body.Close()
			if response.StatusCode != test.want {
				t.Fatalf("status = %d, want %d", response.StatusCode, test.want)
			}
		})
	}
}
