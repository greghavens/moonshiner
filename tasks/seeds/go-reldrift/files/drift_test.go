package reldrift

import (
	"strings"
	"testing"
	"time"

	"github.com/google/go-cmp/cmp"
	"github.com/google/go-cmp/cmp/cmpopts"
)

func baseService() Service {
	return Service{
		Name:       "api",
		Image:      "registry.local/api:1.14.2",
		Replicas:   3,
		CPU:        0.5,
		Ports:      []int{8080, 9090},
		Env:        map[string]string{"LOG_LEVEL": "info"},
		DeployedAt: time.Date(2026, 6, 1, 4, 30, 0, 0, time.UTC),
	}
}

func TestSameServiceEquivalences(t *testing.T) {
	cases := []struct {
		name    string
		mutateA func(s *Service)
		mutateB func(s *Service)
		want    bool
	}{
		{name: "identical manifests match", want: true},
		{
			name:    "deploy timestamp is ignored",
			mutateB: func(s *Service) { s.DeployedAt = time.Date(2026, 6, 8, 11, 15, 0, 0, time.UTC) },
			want:    true,
		},
		{
			name:    "cpu jitter within tolerance matches",
			mutateB: func(s *Service) { s.CPU = 0.5005 },
			want:    true,
		},
		{
			name:    "cpu drift beyond tolerance is drift",
			mutateB: func(s *Service) { s.CPU = 0.55 },
			want:    false,
		},
		{
			name:    "port order is ignored",
			mutateB: func(s *Service) { s.Ports = []int{9090, 8080} },
			want:    true,
		},
		{
			name:    "an extra port is drift",
			mutateB: func(s *Service) { s.Ports = []int{8080, 9090, 9100} },
			want:    false,
		},
		{
			name:    "nil env equals empty env",
			mutateA: func(s *Service) { s.Env = map[string]string{} },
			mutateB: func(s *Service) { s.Env = nil },
			want:    true,
		},
		{
			name:    "nil ports equals empty ports",
			mutateA: func(s *Service) { s.Ports = nil },
			mutateB: func(s *Service) { s.Ports = []int{} },
			want:    true,
		},
		{
			name:    "image change is drift",
			mutateB: func(s *Service) { s.Image = "registry.local/api:1.15.0" },
			want:    false,
		},
		{
			name:    "replica change is drift",
			mutateB: func(s *Service) { s.Replicas = 5 },
			want:    false,
		},
		{
			name:    "env value change is drift",
			mutateB: func(s *Service) { s.Env = map[string]string{"LOG_LEVEL": "debug"} },
			want:    false,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			a, b := baseService(), baseService()
			if tc.mutateA != nil {
				tc.mutateA(&a)
			}
			if tc.mutateB != nil {
				tc.mutateB(&b)
			}
			if got := SameService(a, b); got != tc.want {
				t.Fatalf("SameService() = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestDiffService(t *testing.T) {
	t.Run("empty for equivalent services", func(t *testing.T) {
		a, b := baseService(), baseService()
		b.Ports = []int{9090, 8080}
		b.DeployedAt = time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)
		b.CPU = 0.5004
		if d := DiffService(a, b); d != "" {
			t.Fatalf("DiffService() = %q, want empty", d)
		}
	})
	t.Run("names the field that drifted", func(t *testing.T) {
		a, b := baseService(), baseService()
		b.Replicas = 7
		d := DiffService(a, b)
		if d == "" {
			t.Fatal("DiffService() empty for drifted services")
		}
		if !strings.Contains(d, "Replicas") {
			t.Fatalf("DiffService() = %q, want mention of Replicas", d)
		}
	})
}

func TestBuildReport(t *testing.T) {
	before := []Service{
		func() Service { s := baseService(); return s }(),
		{Name: "cron", Image: "registry.local/cron:2.0.0", Replicas: 1, CPU: 0.1,
			Ports: []int{9100, 8081}, DeployedAt: time.Date(2026, 5, 20, 8, 0, 0, 0, time.UTC)},
		{Name: "db", Image: "registry.local/pg:16.3", Replicas: 1, CPU: 1.0,
			Ports: []int{5432}, DeployedAt: time.Date(2026, 5, 20, 8, 0, 0, 0, time.UTC)},
	}
	after := []Service{
		{Name: "search", Image: "registry.local/search:0.9.1", Replicas: 2, CPU: 0.75,
			Ports: []int{9200}, DeployedAt: time.Date(2026, 6, 9, 3, 0, 0, 0, time.UTC)},
		func() Service {
			s := baseService()
			s.Image = "registry.local/api:1.15.0"
			s.DeployedAt = time.Date(2026, 6, 9, 3, 0, 0, 0, time.UTC)
			return s
		}(),
		{Name: "cron", Image: "registry.local/cron:2.0.0", Replicas: 1, CPU: 0.1001,
			Ports: []int{8081, 9100}, Env: map[string]string{},
			DeployedAt: time.Date(2026, 6, 9, 3, 0, 0, 0, time.UTC)},
		{Name: "auth", Image: "registry.local/auth:4.2.0", Replicas: 2, CPU: 0.25,
			Ports: []int{8443}, DeployedAt: time.Date(2026, 6, 9, 3, 0, 0, 0, time.UTC)},
	}

	rep, err := BuildReport(before, after)
	if err != nil {
		t.Fatalf("BuildReport() error: %v", err)
	}
	if d := cmp.Diff([]string{"auth", "search"}, rep.Added, cmpopts.EquateEmpty()); d != "" {
		t.Errorf("Added mismatch (-want +got):\n%s", d)
	}
	if d := cmp.Diff([]string{"db"}, rep.Removed, cmpopts.EquateEmpty()); d != "" {
		t.Errorf("Removed mismatch (-want +got):\n%s", d)
	}
	if d := cmp.Diff([]string{"api"}, rep.Changed, cmpopts.EquateEmpty()); d != "" {
		t.Errorf("Changed mismatch (-want +got):\n%s", d)
	}
	if len(rep.Details) != 1 {
		t.Fatalf("Details has %d entries, want 1 (only changed services)", len(rep.Details))
	}
	detail, ok := rep.Details["api"]
	if !ok || detail == "" {
		t.Fatalf("Details[api] missing or empty: %q", detail)
	}
	if !strings.Contains(detail, "Image") {
		t.Errorf("Details[api] = %q, want mention of Image", detail)
	}
}

func TestBuildReportEmpty(t *testing.T) {
	rep, err := BuildReport(nil, nil)
	if err != nil {
		t.Fatalf("BuildReport(nil, nil) error: %v", err)
	}
	if d := cmp.Diff(Report{}, rep, cmpopts.EquateEmpty()); d != "" {
		t.Fatalf("empty inputs should give an empty report (-want +got):\n%s", d)
	}
}

func TestBuildReportDuplicateNames(t *testing.T) {
	dup := []Service{
		{Name: "api", Image: "a"},
		{Name: "api", Image: "b"},
	}
	cases := []struct {
		name           string
		before, after  []Service
	}{
		{name: "duplicate in before", before: dup, after: nil},
		{name: "duplicate in after", before: nil, after: dup},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := BuildReport(tc.before, tc.after)
			if err == nil {
				t.Fatal("BuildReport() succeeded, want duplicate-name error")
			}
			if got := err.Error(); got != `duplicate service "api"` {
				t.Fatalf("error = %q, want %q", got, `duplicate service "api"`)
			}
		})
	}
}
