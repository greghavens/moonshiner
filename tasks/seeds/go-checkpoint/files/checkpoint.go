// Package checkpoint persists job progress snapshots so long-running
// batch jobs (importers, backfills, report builders) can resume after a
// restart instead of starting over.
//
// Checkpoints are single files named <job>-<unix seconds>.ckpt, the
// timestamp zero-padded to ten digits so lexical order is chronological.
// Ops tooling greps and sorts these names, so the format is contractual.
package checkpoint

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// ErrNoCheckpoint is returned (wrapped) when a job has no checkpoint yet.
var ErrNoCheckpoint = errors.New("no checkpoint")

// parseName splits "<job>-<ts>.ckpt" from the last hyphen; job names may
// themselves contain hyphens. ok is false for anything that is not a
// well-formed checkpoint name (stray files are common in the spool dir).
func parseName(name string) (job string, ts int64, ok bool) {
	if !strings.HasSuffix(name, ".ckpt") {
		return "", 0, false
	}
	base := strings.TrimSuffix(name, ".ckpt")
	i := strings.LastIndex(base, "-")
	if i <= 0 {
		return "", 0, false
	}
	ts, err := strconv.ParseInt(base[i+1:], 10, 64)
	if err != nil || ts < 0 {
		return "", 0, false
	}
	return base[:i], ts, true
}

// Save writes a checkpoint for job and returns the file name it chose.
func Save(dir, job string, payload []byte) (string, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	name := fmt.Sprintf("%s-%010d.ckpt", job, time.Now().Unix())
	if err := os.WriteFile(filepath.Join(dir, name), payload, 0o644); err != nil {
		return "", err
	}
	return name, nil
}

// Latest returns the newest checkpoint payload for job and the time it
// was taken. Ties on the timestamp go to the lexically greatest name.
func Latest(dir, job string) ([]byte, time.Time, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, time.Time{}, err
	}
	bestName, bestTS := "", int64(-1)
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		j, ts, ok := parseName(e.Name())
		if !ok || j != job {
			continue
		}
		if ts > bestTS || (ts == bestTS && e.Name() > bestName) {
			bestName, bestTS = e.Name(), ts
		}
	}
	if bestName == "" {
		return nil, time.Time{}, fmt.Errorf("%w for job %q", ErrNoCheckpoint, job)
	}
	data, err := os.ReadFile(filepath.Join(dir, bestName))
	if err != nil {
		return nil, time.Time{}, err
	}
	return data, time.Unix(bestTS, 0).UTC(), nil
}

// Resume returns the latest payload for job if it is no older than
// maxAge. A missing or stale checkpoint is (nil, false, nil): the job
// simply starts from scratch, it is not an error.
func Resume(dir, job string, maxAge time.Duration) ([]byte, bool, error) {
	data, at, err := Latest(dir, job)
	if errors.Is(err, ErrNoCheckpoint) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	if time.Now().Sub(at) > maxAge {
		return nil, false, nil
	}
	return data, true, nil
}

// SweepStale deletes every checkpoint (any job) strictly older than
// maxAge and returns the removed names sorted ascending. Files that are
// not checkpoint-shaped are left alone.
func SweepStale(dir string, maxAge time.Duration) ([]string, error) {
	now := time.Now()
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var removed []string
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		_, ts, ok := parseName(e.Name())
		if !ok {
			continue
		}
		if now.Sub(time.Unix(ts, 0)) > maxAge {
			if err := os.Remove(filepath.Join(dir, e.Name())); err != nil {
				return removed, err
			}
			removed = append(removed, e.Name())
		}
	}
	sort.Strings(removed)
	return removed, nil
}
