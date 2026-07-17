// Package dirwalk enumerates files under a root directory for the
// backup agent's snapshot planner. Paths come back slash-separated
// and relative to the root so manifests are portable across hosts.
package dirwalk

import (
	"io/fs"
	"path/filepath"
)

// Entry is one file or directory found under the root.
type Entry struct {
	Path  string // slash-separated, relative to the walk root
	IsDir bool
	Size  int64 // 0 for directories
}

// Walk lists everything under root (the root itself is not included),
// walking depth-first with each directory's children visited in
// lexical name order.
func Walk(root string) ([]Entry, error) {
	var out []Entry
	err := filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if p == root {
			return nil
		}
		rel, err := filepath.Rel(root, p)
		if err != nil {
			return err
		}
		e := Entry{Path: filepath.ToSlash(rel), IsDir: d.IsDir()}
		if !d.IsDir() {
			info, err := d.Info()
			if err != nil {
				return err
			}
			e.Size = info.Size()
		}
		out = append(out, e)
		return nil
	})
	if err != nil {
		return nil, err
	}
	return out, nil
}
