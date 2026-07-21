// Package assets provides access to the generated files shipped in a release.
package assets

//go:generate go run ../genassets -source ../../web -output generated

import (
	"fmt"
	"io/fs"
	"path"
	"strings"
)

// Lookup returns a generated asset by its slash-separated logical name.
func Lookup(name string) ([]byte, error) {
	if name == "" || path.IsAbs(name) || strings.Contains(name, "\\") {
		return nil, fmt.Errorf("invalid asset name %q: %w", name, fs.ErrInvalid)
	}

	clean := path.Clean(name)
	if clean == "." || clean == ".." || strings.HasPrefix(clean, "../") {
		return nil, fmt.Errorf("invalid asset name %q: %w", name, fs.ErrInvalid)
	}

	data, err := bundle.ReadFile(path.Join("generated", clean))
	if err != nil {
		return nil, fmt.Errorf("lookup asset %q: %w", name, err)
	}
	return data, nil
}
