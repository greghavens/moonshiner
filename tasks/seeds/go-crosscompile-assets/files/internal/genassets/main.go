// Command genassets copies web sources into the package embed directory and
// emits a stable content manifest. It deliberately records no timestamps.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type manifestEntry struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
	Size   int    `json:"size"`
}

func main() {
	source := flag.String("source", "", "directory containing source assets")
	output := flag.String("output", "", "directory for generated assets")
	flag.Parse()

	if *source == "" || *output == "" || flag.NArg() != 0 {
		fatalf("usage: genassets -source DIR -output DIR")
	}
	if err := generate(*source, *output); err != nil {
		fatalf("generate assets: %v", err)
	}
}

func generate(source, output string) error {
	sourceAbs, err := filepath.Abs(source)
	if err != nil {
		return err
	}
	outputAbs, err := filepath.Abs(output)
	if err != nil {
		return err
	}
	if sourceAbs == outputAbs || strings.HasPrefix(outputAbs, sourceAbs+string(filepath.Separator)) {
		return fmt.Errorf("output directory must not be inside source directory")
	}

	var names []string
	err = filepath.WalkDir(sourceAbs, func(filePath string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("source asset %s is a symlink", filePath)
		}
		if entry.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(sourceAbs, filePath)
		if err != nil {
			return err
		}
		names = append(names, filepath.ToSlash(rel))
		return nil
	})
	if err != nil {
		return err
	}
	sort.Strings(names)

	if err := os.RemoveAll(outputAbs); err != nil {
		return err
	}
	if err := os.MkdirAll(outputAbs, 0o755); err != nil {
		return err
	}

	entries := make([]manifestEntry, 0, len(names))
	for _, name := range names {
		data, err := os.ReadFile(filepath.Join(sourceAbs, filepath.FromSlash(name)))
		if err != nil {
			return err
		}
		destination := filepath.Join(outputAbs, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(destination, data, 0o644); err != nil {
			return err
		}
		digest := sha256.Sum256(data)
		entries = append(entries, manifestEntry{
			Path:   name,
			SHA256: hex.EncodeToString(digest[:]),
			Size:   len(data),
		})
	}

	manifest, err := json.MarshalIndent(entries, "", "  ")
	if err != nil {
		return err
	}
	manifest = append(manifest, '\n')
	return os.WriteFile(filepath.Join(outputAbs, "manifest.json"), manifest, 0o644)
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "genassets: "+format+"\n", args...)
	os.Exit(1)
}
