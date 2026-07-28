package artifact

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
)

var (
	ErrInvalidDigest   = errors.New("invalid artifact digest")
	ErrDigestMismatch  = errors.New("artifact digest mismatch")
	ErrCorruptArtifact = errors.New("corrupt artifact")
)

// Store keeps immutable artifacts below a two-level SHA-256 object layout.
// Store values that use the same root may be used independently.
type Store struct {
	root string
}

type PublishResult struct {
	Path    string
	Size    int64
	Created bool
}

func NewStore(root string) *Store {
	return &Store{root: root}
}

// Publish stores src at the path named by expectedDigest.
func (s *Store) Publish(ctx context.Context, expectedDigest string, src io.Reader) (PublishResult, error) {
	var zero PublishResult
	if err := validateDigest(expectedDigest); err != nil {
		return zero, err
	}
	if err := ctx.Err(); err != nil {
		return zero, err
	}

	finalPath := s.objectPath(expectedDigest)
	if info, err := os.Stat(finalPath); err == nil {
		return PublishResult{Path: finalPath, Size: info.Size(), Created: false}, nil
	} else if !errors.Is(err, fs.ErrNotExist) {
		return zero, fmt.Errorf("inspect artifact: %w", err)
	}

	shard := filepath.Dir(finalPath)
	if err := os.MkdirAll(shard, 0o755); err != nil {
		return zero, fmt.Errorf("create artifact shard: %w", err)
	}

	staged, err := os.CreateTemp(shard, ".incoming-*")
	if err != nil {
		return zero, fmt.Errorf("create staging file: %w", err)
	}
	stagedPath := staged.Name()
	defer func() {
		_ = staged.Close()
		_ = os.Remove(stagedPath)
	}()

	hasher := sha256.New()
	size, err := io.Copy(io.MultiWriter(staged, hasher), src)
	if err != nil {
		return zero, fmt.Errorf("copy artifact: %w", err)
	}
	actualDigest := hex.EncodeToString(hasher.Sum(nil))
	if actualDigest != expectedDigest {
		return zero, fmt.Errorf("%w: expected %s, got %s", ErrDigestMismatch, expectedDigest, actualDigest)
	}
	if err := staged.Sync(); err != nil {
		return zero, fmt.Errorf("flush staging file: %w", err)
	}
	if err := staged.Close(); err != nil {
		return zero, fmt.Errorf("close staging file: %w", err)
	}

	// The destination was absent when it was checked above, so moving the
	// completed staging file into place should publish it.
	if err := os.Rename(stagedPath, finalPath); err != nil {
		return zero, fmt.Errorf("publish artifact: %w", err)
	}
	return PublishResult{Path: finalPath, Size: size, Created: true}, nil
}

// Read returns the bytes stored under digest.
func (s *Store) Read(ctx context.Context, digest string) ([]byte, error) {
	if err := validateDigest(digest); err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	data, err := os.ReadFile(s.objectPath(digest))
	if err != nil {
		return nil, fmt.Errorf("read artifact: %w", err)
	}
	return data, nil
}

func (s *Store) objectPath(digest string) string {
	return filepath.Join(s.root, digest[:2], digest[2:])
}

func validateDigest(digest string) error {
	if len(digest) != sha256.Size*2 {
		return ErrInvalidDigest
	}
	for _, c := range digest {
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
			return ErrInvalidDigest
		}
	}
	return nil
}
