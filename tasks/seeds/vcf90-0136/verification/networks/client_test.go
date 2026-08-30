package networks_test

import (
	"context"
	"errors"
	"testing"

	"vcfnetworks/mockvcf"
	"vcfnetworks/networks"
)

func TestUpdateCertificateAndWaitTerminalOutcomes(t *testing.T) {
	tests := []struct {
		name      string
		terminal  networks.CertificateUpdateStatus
		wantError bool
	}{
		{
			name:     "success",
			terminal: networks.CertificateUpdateStatus{ID: "job-7", Status: networks.StatusSuccess},
		},
		{
			name:      "failure",
			terminal:  networks.CertificateUpdateStatus{ID: "job-7", Status: networks.StatusFailed, ErrorMessage: "node rejected certificate"},
			wantError: true,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := mockvcf.New(mockvcf.Script{
				Initial: networks.CertificateUpdateStatus{ID: "job-7", Status: networks.StatusSubmitted},
				Polls:   []networks.CertificateUpdateStatus{test.terminal},
			})
			defer server.Close()

			client := networks.NewClient(server.URL(), "token", server.Client(), 0)
			status, err := client.UpdateCertificateAndWait(context.Background(), "proxy_register.crt", networks.CertificateUpdateRequest{
				Certificate: "certificate",
				PrivateKey:  "private-key",
			})
			if status.Status != test.terminal.Status {
				t.Errorf("status = %q, want %q", status.Status, test.terminal.Status)
			}
			if !test.wantError && err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if test.wantError {
				var operationError *networks.OperationError
				if !errors.As(err, &operationError) {
					t.Fatalf("error = %v, want OperationError", err)
				}
			}
			if got := len(server.Requests()); got != 2 {
				t.Errorf("request count = %d, want submit plus one poll", got)
			}
		})
	}
}
