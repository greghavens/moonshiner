package main

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"
)

func TestDocumentedDeploymentStartupCheck(t *testing.T) {
	root := filepath.Join("..", "..")
	args := []string{
		"--config", filepath.Join(root, "deploy", "relay.json"),
		"--env-file", filepath.Join(root, "deploy", "container.env"),
	}
	var stdout, stderr bytes.Buffer
	code := run(args, []string{"RELAY_UPSTREAM_URL=https://process-env.example.com"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("run exit = %d, stderr = %s", code, stderr.String())
	}
	if stderr.Len() != 0 {
		t.Fatalf("unexpected stderr: %s", stderr.String())
	}

	output := stdout.String()
	for _, secret := range []string{
		"fixture-relay-token-61b26c",
		"fixture-db-password-903fa1",
	} {
		if strings.Contains(output, secret) {
			t.Fatalf("startup output leaked %q: %s", secret, output)
		}
	}
	for _, want := range []string{
		"relay startup check passed",
		`http.address=":9090" (env)`,
		`upstream.url="https://collector.example.com/v1/events" (default)`,
		"upstream.token=<redacted> (file)",
		"database.dsn=<redacted> (file)",
	} {
		if !strings.Contains(output, want) {
			t.Fatalf("startup output %q does not contain %q", output, want)
		}
	}
}
