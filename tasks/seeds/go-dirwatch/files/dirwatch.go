// Package dirwatch is the change detector behind the dev server's
// hot-reload loop. The server polls the asset directory, diffs the
// snapshot against the previous one, and rebuilds whatever changed.
package dirwatch

import (
	"crypto/sha256"
	"encoding/hex"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
)

// Snapshot maps slash-separated paths (relative to the watched root)
// to a hex digest of the file's content.
type Snapshot map[string]string

// Take walks dir recursively and fingerprints every regular file.
// Directories themselves are not entries; non-regular files (sockets,
// symlinks, ...) are skipped.
func Take(dir string) (Snapshot, error) {
	snap := Snapshot{}
	err := filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !d.Type().IsRegular() {
			return nil
		}
		data, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(dir, p)
		if err != nil {
			return err
		}
		sum := sha256.Sum256(data)
		snap[filepath.ToSlash(rel)] = hex.EncodeToString(sum[:])
		return nil
	})
	if err != nil {
		return nil, err
	}
	return snap, nil
}

// Changes is what happened between two snapshots. Every slice is
// sorted; a path appears in at most one of them.
type Changes struct {
	Added    []string
	Removed  []string
	Modified []string
}

// Diff compares two snapshots taken from the same root.
func Diff(old, cur Snapshot) Changes {
	var ch Changes
	for p, h := range cur {
		prev, ok := old[p]
		switch {
		case !ok:
			ch.Added = append(ch.Added, p)
		case prev != h:
			ch.Modified = append(ch.Modified, p)
		}
	}
	for p := range old {
		if _, ok := cur[p]; !ok {
			ch.Removed = append(ch.Removed, p)
		}
	}
	sort.Strings(ch.Added)
	sort.Strings(ch.Removed)
	sort.Strings(ch.Modified)
	return ch
}
