package architecture

import "errors"

var ErrNotImplemented = errors.New("architecture generator is not implemented")

func Build(Estate, CompatibilitySnapshot) (Design, error) {
	return Design{}, ErrNotImplemented
}
