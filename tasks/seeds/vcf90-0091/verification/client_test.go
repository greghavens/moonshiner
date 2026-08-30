package vcflogs_test

import (
	"context"
	"net/http"
	"testing"

	vcflogs "example.com/vcfopslogs"
	"example.com/vcfopslogs/mocklogs"
)

func TestJoinRequestFields(t *testing.T) {
	tests := []struct {
		name string
		req  vcflogs.JoinRequest
		body string
	}{
		{"minimal", vcflogs.JoinRequest{MasterFQDN: "li-01.example.com"}, `{"masterFQDN":"li-01.example.com"}`},
		{"populated", vcflogs.JoinRequest{MasterFQDN: "li-02.example.com", MasterPort: intPtr(9543), AcceptCert: boolPtr(true)}, `{"masterFQDN":"li-02.example.com","masterPort":9543,"acceptCert":true}`},
		{"explicit false", vcflogs.JoinRequest{MasterFQDN: "li-03.example.com", AcceptCert: boolPtr(false)}, `{"masterFQDN":"li-03.example.com","acceptCert":false}`},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			server, err := mocklogs.New(http.StatusOK)
			if err != nil {
				t.Fatal(err)
			}
			defer server.Close()
			client, err := vcflogs.NewClient(server.URL(), server.Client())
			if err != nil {
				t.Fatal(err)
			}
			if _, err := client.JoinAndWait(context.Background(), tc.req, 0); err != nil {
				t.Fatal(err)
			}
			requests := server.Requests()
			if got := string(requests[0].Body); got != tc.body {
				t.Fatalf("body = %s, want %s", got, tc.body)
			}
		})
	}
}

func TestJoinPollsUntilStarted(t *testing.T) {
	server, err := mocklogs.New(http.StatusInternalServerError, http.StatusOK)
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	client, err := vcflogs.NewClient(server.URL(), server.Client())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.JoinAndWait(context.Background(), vcflogs.JoinRequest{MasterFQDN: "li-01.example.com"}, 0); err != nil {
		t.Fatal(err)
	}
	if got := len(server.Requests()); got != 3 {
		t.Fatalf("request count = %d, want join plus two polls", got)
	}
}

func intPtr(value int) *int    { return &value }
func boolPtr(value bool) *bool { return &value }
