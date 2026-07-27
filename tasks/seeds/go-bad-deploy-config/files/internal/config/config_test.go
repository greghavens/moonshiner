package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const (
	tokenCanary = "fixture-token-MUST-NOT-LEAK-2e91"
	dsnCanary   = "postgres://relay:fixture-password-MUST-NOT-LEAK-7ac4@db.example.com:5432/ledger"
)

func writeConfig(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "relay.json")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func completeFile(t *testing.T) string {
	t.Helper()
	return writeConfig(t, `{
		"http": {"address": "127.0.0.1:8080"},
		"upstream": {"token": "`+tokenCanary+`"},
		"database": {"dsn": "`+dsnCanary+`"}
	}`)
}

func TestIncidentEmptyEnvironmentDoesNotMaskLowerLayer(t *testing.T) {
	cfg, err := Load(completeFile(t), []string{
		"RELAY_HTTP_ADDRESS=:9090",
		"RELAY_UPSTREAM_URL=",
		"RELAY_UPSTREAM_URL= \t ",
	})
	if err != nil {
		t.Fatalf("renderer output should not break startup: %v", err)
	}

	if got, want := cfg.UpstreamURL(), defaultValues[UpstreamURLKey]; got != want {
		t.Fatalf("upstream URL = %q, want lower-layer default %q", got, want)
	}
	if got, ok := cfg.Source(UpstreamURLKey); !ok || got != SourceDefault {
		t.Fatalf("upstream URL source = %q, %v; want %q, true", got, ok, SourceDefault)
	}
	if got := cfg.HTTPAddress(); got != ":9090" {
		t.Fatalf("non-empty env override = %q, want :9090", got)
	}
	if got, ok := cfg.Source(HTTPAddressKey); !ok || got != SourceEnv {
		t.Fatalf("HTTP source = %q, %v; want %q, true", got, ok, SourceEnv)
	}
}

func TestNonEmptyEnvironmentStillWinsAndLastNonEmptyWins(t *testing.T) {
	const envURL = "https://override.example.com/v2/events"
	cfg, err := Load(completeFile(t), []string{
		"RELAY_UPSTREAM_URL=https://first.example.com/events",
		"RELAY_UPSTREAM_URL= ",
		"RELAY_UPSTREAM_URL=" + envURL,
		"RELAY_UPSTREAM_URL=\t",
		"UNRELATED_TOKEN=ignored",
	})
	if err != nil {
		t.Fatal(err)
	}
	if got := cfg.UpstreamURL(); got != envURL {
		t.Fatalf("upstream URL = %q, want %q", got, envURL)
	}
	if got, ok := cfg.Source(UpstreamURLKey); !ok || got != SourceEnv {
		t.Fatalf("upstream URL source = %q, %v; want env", got, ok)
	}
}

func TestExplicitEmptyJSONValueDoesNotFallThrough(t *testing.T) {
	path := writeConfig(t, `{
		"http": {"address": "127.0.0.1:8080"},
		"upstream": {"url": "", "token": "`+tokenCanary+`"},
		"database": {"dsn": "`+dsnCanary+`"}
	}`)
	_, err := Load(path, []string{"RELAY_UPSTREAM_URL="})
	assertSafeValidationError(t, err, UpstreamURLKey, SourceFile)
}

func TestValidationCoversEffectiveShapesAndReportsProvenance(t *testing.T) {
	tests := []struct {
		name      string
		file      string
		environ   []string
		field     string
		source    Source
		forbidden string
	}{
		{
			name: "listen address",
			file: `{
				"http": {"address": "localhost"},
				"upstream": {"token": "` + tokenCanary + `"},
				"database": {"dsn": "` + dsnCanary + `"}
			}`,
			field:  HTTPAddressKey,
			source: SourceFile,
		},
		{
			name: "listen port range",
			file: `{
				"http": {"address": "127.0.0.1:70000"},
				"upstream": {"token": "` + tokenCanary + `"},
				"database": {"dsn": "` + dsnCanary + `"}
			}`,
			field:  HTTPAddressKey,
			source: SourceFile,
		},
		{
			name:    "upstream scheme from env",
			file:    validJSON(),
			environ: []string{"RELAY_UPSTREAM_URL=ftp://override.example.com/events"},
			field:   UpstreamURLKey,
			source:  SourceEnv,
		},
		{
			name:      "database shape from env",
			file:      validJSON(),
			environ:   []string{"RELAY_DATABASE_DSN=database-secret-without-a-url"},
			field:     DatabaseDSNKey,
			source:    SourceEnv,
			forbidden: "database-secret-without-a-url",
		},
		{
			name: "blank token in file",
			file: `{
				"http": {"address": "127.0.0.1:8080"},
				"upstream": {"token": "   "},
				"database": {"dsn": "` + dsnCanary + `"}
			}`,
			field:     UpstreamTokenKey,
			source:    SourceFile,
			forbidden: "   ",
		},
		{
			name: "database missing path",
			file: `{
				"http": {"address": "127.0.0.1:8080"},
				"upstream": {"token": "` + tokenCanary + `"},
				"database": {"dsn": "postgres://relay:another-secret@db.example.com"}
			}`,
			field:     DatabaseDSNKey,
			source:    SourceFile,
			forbidden: "another-secret",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := Load(writeConfig(t, tt.file), tt.environ)
			assertSafeValidationError(t, err, tt.field, tt.source)
			if tt.forbidden != "" && strings.Contains(err.Error(), tt.forbidden) {
				t.Fatalf("validation error leaked rejected value %q: %v", tt.forbidden, err)
			}
		})
	}
}

func TestMissingRequiredSettingReportsUnset(t *testing.T) {
	path := writeConfig(t, `{
		"http": {"address": "127.0.0.1:8080"},
		"database": {"dsn": "`+dsnCanary+`"}
	}`)
	_, err := Load(path, nil)
	assertSafeValidationError(t, err, UpstreamTokenKey, SourceUnset)
}

func TestSummariesAndErrorsRedactEverySecret(t *testing.T) {
	cfg, err := Load(completeFile(t), nil)
	if err != nil {
		t.Fatal(err)
	}
	for name, output := range map[string]string{
		"SafeSummary": cfg.SafeSummary(),
		"String":      cfg.String(),
		"fmt %v":      fmt.Sprintf("%v", cfg),
		"fmt %+v":     fmt.Sprintf("%+v", cfg),
	} {
		assertRedacted(t, name, output)
		if !strings.Contains(output, HTTPAddressKey) ||
			!strings.Contains(output, UpstreamURLKey) ||
			!strings.Contains(output, string(SourceFile)) {
			t.Fatalf("%s omitted useful field/source context: %s", name, output)
		}
	}

	badPath := writeConfig(t, `{
		"http": {"address": "bad-address"},
		"upstream": {"token": "`+tokenCanary+`"},
		"database": {"dsn": "`+dsnCanary+`"}
	}`)
	_, err = Load(badPath, nil)
	if err == nil {
		t.Fatal("expected invalid address to fail")
	}
	assertNoSecrets(t, "Load error", err.Error())
}

func TestFileDecodeRemainsStrict(t *testing.T) {
	path := writeConfig(t, `{"http":{"address":":8080"},"surprise":true}`)
	if _, err := Load(path, nil); err == nil || !strings.Contains(err.Error(), "unknown field") {
		t.Fatalf("expected unknown JSON field error, got %v", err)
	}
}

func validJSON() string {
	return `{
		"http": {"address": "127.0.0.1:8080"},
		"upstream": {"token": "` + tokenCanary + `"},
		"database": {"dsn": "` + dsnCanary + `"}
	}`
}

func assertSafeValidationError(t *testing.T, err error, field string, source Source) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected validation failure for %s", field)
	}
	message := err.Error()
	if !strings.Contains(message, field) {
		t.Fatalf("error %q does not name field %q", message, field)
	}
	if !strings.Contains(message, string(source)) {
		t.Fatalf("error %q does not name source %q", message, source)
	}
	assertNoSecrets(t, "validation error", message)
}

func assertRedacted(t *testing.T, label, output string) {
	t.Helper()
	assertNoSecrets(t, label, output)
	if !strings.Contains(output, "<redacted>") {
		t.Fatalf("%s has no redaction marker: %q", label, output)
	}
}

func assertNoSecrets(t *testing.T, label, output string) {
	t.Helper()
	for _, secret := range []string{
		tokenCanary,
		dsnCanary,
		"fixture-password-MUST-NOT-LEAK-7ac4",
	} {
		if strings.Contains(output, secret) {
			t.Fatalf("%s leaked secret %q in %q", label, secret, output)
		}
	}
}
