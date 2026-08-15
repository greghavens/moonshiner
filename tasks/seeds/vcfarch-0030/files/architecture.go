package vcfarch

import (
	"errors"
	"io"
)

var ErrNotImplemented = errors.New("architecture builder not implemented")

func BuildArchitecture(Requirements, Estate, CompatibilitySnapshot) (Artifact, error) {
	return Artifact{}, ErrNotImplemented
}

func WriteArtifact(io.Writer, Artifact) error {
	return ErrNotImplemented
}
