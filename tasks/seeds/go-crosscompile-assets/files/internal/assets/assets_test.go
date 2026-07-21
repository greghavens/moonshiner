package assets

import (
	"bytes"
	"errors"
	"io/fs"
	"testing"
)

func TestLookupUsesGeneratedBundle(t *testing.T) {
	data, err := Lookup("index.html")
	if err != nil {
		t.Fatalf("Lookup(index.html): %v", err)
	}
	if !bytes.Contains(data, []byte("moonshiner-runtime-asset-v1")) {
		t.Fatalf("Lookup(index.html) returned unexpected data: %q", data)
	}
}

func TestLookupRejectsTraversal(t *testing.T) {
	for _, name := range []string{"", "../index.html", "/index.html", `..\\index.html`} {
		if _, err := Lookup(name); !errors.Is(err, fs.ErrInvalid) {
			t.Errorf("Lookup(%q) error = %v, want fs.ErrInvalid", name, err)
		}
	}
}
