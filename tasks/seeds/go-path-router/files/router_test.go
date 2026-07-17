package router

import (
	"errors"
	"testing"
)

func TestHandleAndLookupExact(t *testing.T) {
	r := New[string]()
	if err := r.Handle("/health", "health-handler"); err != nil {
		t.Fatalf("Handle: %v", err)
	}
	if err := r.Handle("/api/users", "users-handler"); err != nil {
		t.Fatalf("Handle: %v", err)
	}

	h, ok := r.Lookup("/api/users")
	if !ok || h != "users-handler" {
		t.Fatalf("Lookup(/api/users) = %q, %v; want users-handler, true", h, ok)
	}
	if _, ok := r.Lookup("/api/orders"); ok {
		t.Fatal("Lookup matched an unregistered path")
	}
	if _, ok := r.Lookup("/api"); ok {
		t.Fatal("Lookup matched a prefix of a registered path")
	}
}

func TestTrailingSlashNormalization(t *testing.T) {
	r := New[string]()
	if err := r.Handle("/users/", "u"); err != nil {
		t.Fatalf("Handle: %v", err)
	}
	if h, ok := r.Lookup("/users"); !ok || h != "u" {
		t.Fatalf("Lookup(/users) = %q, %v; want u, true", h, ok)
	}
	if h, ok := r.Lookup("/users/"); !ok || h != "u" {
		t.Fatalf("Lookup(/users/) = %q, %v; want u, true", h, ok)
	}
}

func TestDuplicateRegistration(t *testing.T) {
	r := New[string]()
	if err := r.Handle("/a", "first"); err != nil {
		t.Fatalf("Handle: %v", err)
	}
	err := r.Handle("/a/", "second") // normalizes to the same route
	if !errors.Is(err, ErrDuplicate) {
		t.Fatalf("err = %v, want errors.Is(err, ErrDuplicate)", err)
	}
	if h, _ := r.Lookup("/a"); h != "first" {
		t.Fatalf("duplicate registration clobbered the handler: got %q", h)
	}
}

func TestHandleRejectsRelativePatterns(t *testing.T) {
	r := New[string]()
	if err := r.Handle("users", "u"); err == nil {
		t.Fatal("Handle accepted a pattern without a leading slash")
	}
	if err := r.Handle("", "u"); err == nil {
		t.Fatal("Handle accepted an empty pattern")
	}
}

func TestRootRoute(t *testing.T) {
	r := New[string]()
	if err := r.Handle("/", "root"); err != nil {
		t.Fatalf("Handle(/): %v", err)
	}
	if h, ok := r.Lookup("/"); !ok || h != "root" {
		t.Fatalf("Lookup(/) = %q, %v; want root, true", h, ok)
	}
}

func TestListPreservesRegistrationOrder(t *testing.T) {
	r := New[int]()
	for i, p := range []string{"/c", "/a", "/b"} {
		if err := r.Handle(p, i); err != nil {
			t.Fatalf("Handle(%s): %v", p, err)
		}
	}
	got := r.List()
	want := []string{"/c", "/a", "/b"}
	if len(got) != len(want) {
		t.Fatalf("List() = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("List() = %v, want %v", got, want)
		}
	}
}
