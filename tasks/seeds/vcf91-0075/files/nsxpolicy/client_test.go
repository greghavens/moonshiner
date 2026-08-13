package nsxpolicy

import (
	"net/http"
	"testing"
)

func TestNewClientValidation(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		base string
		cred Credentials
		ok   bool
	}{
		{name: "valid", base: "https://nsx.example.test", cred: Credentials{Username: "svc", Password: "secret"}, ok: true},
		{name: "missing scheme", base: "nsx.example.test", cred: Credentials{Username: "svc", Password: "secret"}},
		{name: "query rejected", base: "https://nsx.example.test?debug=1", cred: Credentials{Username: "svc", Password: "secret"}},
		{name: "missing password", base: "https://nsx.example.test", cred: Credentials{Username: "svc"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := NewClient(tt.base, &http.Client{}, tt.cred)
			if (err == nil) != tt.ok {
				t.Fatalf("NewClient() error = %v, want success %v", err, tt.ok)
			}
		})
	}
}

func TestRotateCredentialsRejectsIncompleteValue(t *testing.T) {
	t.Parallel()
	c, err := NewClient("https://nsx.example.test", nil, Credentials{Username: "svc", Password: "old"})
	if err != nil {
		t.Fatal(err)
	}
	if err := c.RotateCredentials(Credentials{Username: "svc"}); err == nil {
		t.Fatal("RotateCredentials() accepted an empty password")
	}
}
