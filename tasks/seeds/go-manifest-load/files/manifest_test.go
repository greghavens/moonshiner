package manifest

import (
	"strings"
	"testing"
)

// sampleManifest is a trimmed copy of what the release tooling actually emits.
const sampleManifest = `{
  "release": "2026-07-08.2",
  "services": [
    {
      "name": "api",
      "image": "registry.internal/api:9f31c2d",
      "replicas": 3,
      "ports": [8080, 9090],
      "env": {"LOG_LEVEL": "debug", "REGION": "eu-west-1"},
      "check_path": "/internal/health"
    },
    {
      "name": "worker",
      "image": "registry.internal/worker:9f31c2d",
      "env": {"QUEUE": "exports"}
    }
  ]
}`

func TestLoadReadsWholeServiceDefinition(t *testing.T) {
	m, err := Load(strings.NewReader(sampleManifest))
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if m.Release != "2026-07-08.2" {
		t.Fatalf("release = %q, want 2026-07-08.2", m.Release)
	}
	api := m.Services[0]
	if api.Name != "api" || api.Image != "registry.internal/api:9f31c2d" {
		t.Fatalf("api identity wrong: %+v", api)
	}
	if api.Replicas != 3 {
		t.Fatalf("api.Replicas = %d, want 3", api.Replicas)
	}
	if len(api.Ports) != 2 || api.Ports[0] != 8080 || api.Ports[1] != 9090 {
		t.Fatalf("api.Ports = %v, want [8080 9090]", api.Ports)
	}
	if got := api.Env["LOG_LEVEL"]; got != "debug" {
		t.Fatalf("api env LOG_LEVEL = %q, want debug (env map: %v)", got, api.Env)
	}
	if got := api.Env["REGION"]; got != "eu-west-1" {
		t.Fatalf("api env REGION = %q, want eu-west-1", got)
	}
	if api.CheckPath != "/internal/health" {
		t.Fatalf("api.CheckPath = %q, want /internal/health", api.CheckPath)
	}
}

func TestLoadAppliesDefaults(t *testing.T) {
	m, err := Load(strings.NewReader(sampleManifest))
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	worker := m.Services[1]
	if worker.Replicas != 1 {
		t.Fatalf("worker.Replicas = %d, want default 1", worker.Replicas)
	}
	if worker.CheckPath != "/healthz" {
		t.Fatalf("worker.CheckPath = %q, want default /healthz", worker.CheckPath)
	}
	if got := worker.Env["QUEUE"]; got != "exports" {
		t.Fatalf("worker env QUEUE = %q, want exports", got)
	}
}

func TestLoadRejectsBrokenManifests(t *testing.T) {
	cases := []string{
		`{"services": [{"name": "api", "image": "x"}]}`,
		`{"release": "r1", "services": []}`,
		`{"release": "r1", "services": [{"name": "api"}]}`,
		`{"release": "r1", "services": [{"name": "api", "image": "x"}, {"name": "api", "image": "y"}]}`,
	}
	for _, doc := range cases {
		if _, err := Load(strings.NewReader(doc)); err == nil {
			t.Fatalf("Load accepted invalid manifest %s", doc)
		}
	}
}
