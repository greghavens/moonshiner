package cmdline

import (
	"reflect"
	"testing"
)

// Pins the existing builder behavior. Must keep passing.

func TestNewAndAppendBuildArgvInOrder(t *testing.T) {
	c := New("tar", "-czf", "out.tgz").Append("src", "docs")
	want := []string{"tar", "-czf", "out.tgz", "src", "docs"}
	if got := c.Args(); !reflect.DeepEqual(got, want) {
		t.Fatalf("Args() = %v, want %v", got, want)
	}
}

func TestStringJoinsWithSpaces(t *testing.T) {
	c := New("go", "test", "./...")
	if got := c.String(); got != "go test ./..." {
		t.Fatalf("String() = %q", got)
	}
	if got := New("true").String(); got != "true" {
		t.Fatalf("String() of bare command = %q", got)
	}
}

func TestNewCopiesItsArguments(t *testing.T) {
	src := []string{"-v", "--fast"}
	c := New("lint", src...)
	src[0] = "MUTATED"
	if got := c.Args()[1]; got != "-v" {
		t.Fatalf("Args()[1] = %q, want %q (caller slice must be copied)", got, "-v")
	}
}

func TestArgsReturnsAFreshCopy(t *testing.T) {
	c := New("ls", "-l")
	got := c.Args()
	got[0] = "MUTATED"
	got[1] = "MUTATED"
	if want := []string{"ls", "-l"}; !reflect.DeepEqual(c.Args(), want) {
		t.Fatalf("Args() = %v, want %v (returned slice must not alias internals)", c.Args(), want)
	}
}
