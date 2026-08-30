package contractmock

import (
	"net/http"
	"testing"
)

func TestRejectsOperationsOutsideFocusedContract(t *testing.T) {
	t.Parallel()
	server := New(nil)
	defer server.Close()

	response, err := server.Client().Get(server.URL() + "/policy/api/v1/infra")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", response.StatusCode)
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("request log contains %d non-contract requests", got)
	}
}
