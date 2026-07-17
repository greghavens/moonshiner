package flagbind

import (
	"errors"
	"strings"
	"testing"
	"time"
)

// Acceptance tests for the binder extensions: time.Duration fields,
// []string fields (repeatable flags), and required-field validation via
// a ",required" tag option reported through *MissingFlagError.

type serveCfg struct {
	Addr    string        `flag:"addr,required"`
	Timeout time.Duration `flag:"timeout"`
	Tags    []string      `flag:"tag"`
	Retries int           `flag:"retries"`
}

func TestBindDurationSliceAndRequiredTogether(t *testing.T) {
	var c serveCfg
	err := Bind(&c, []string{
		"--addr=:8080",
		"--timeout", "2m30s",
		"--tag", "edge",
		"--tag=canary",
		"--retries=4",
	})
	if err != nil {
		t.Fatalf("Bind: %v", err)
	}
	if c.Addr != ":8080" {
		t.Fatalf("Addr = %q, want :8080", c.Addr)
	}
	if c.Timeout != 2*time.Minute+30*time.Second {
		t.Fatalf("Timeout = %v, want 2m30s", c.Timeout)
	}
	if len(c.Tags) != 2 || c.Tags[0] != "edge" || c.Tags[1] != "canary" {
		t.Fatalf("Tags = %v, want [edge canary] in argument order", c.Tags)
	}
	if c.Retries != 4 {
		t.Fatalf("Retries = %d, want 4", c.Retries)
	}
}

func TestBindDurationRejectsGarbage(t *testing.T) {
	var c serveCfg
	err := Bind(&c, []string{"--addr=x", "--timeout=fast"})
	if err == nil {
		t.Fatal("Bind accepted an unparseable duration")
	}
	if !strings.Contains(err.Error(), "--timeout") {
		t.Fatalf("error %q should name the flag", err)
	}
}

func TestBindSliceAccumulatesInOrder(t *testing.T) {
	var c serveCfg
	err := Bind(&c, []string{"--addr=x", "--tag=a", "--tag=b", "--tag=c"})
	if err != nil {
		t.Fatalf("Bind: %v", err)
	}
	want := []string{"a", "b", "c"}
	if len(c.Tags) != len(want) {
		t.Fatalf("Tags = %v, want %v", c.Tags, want)
	}
	for i := range want {
		if c.Tags[i] != want[i] {
			t.Fatalf("Tags = %v, want %v", c.Tags, want)
		}
	}
}

func TestBindSliceUntouchedWhenAbsent(t *testing.T) {
	var c serveCfg
	if err := Bind(&c, []string{"--addr=x"}); err != nil {
		t.Fatalf("Bind: %v", err)
	}
	if c.Tags != nil {
		t.Fatalf("Tags = %v, want nil when --tag never appears", c.Tags)
	}
}

func TestBindMissingRequiredFlag(t *testing.T) {
	var c serveCfg
	err := Bind(&c, []string{"--retries=1"})
	var mfe *MissingFlagError
	if !errors.As(err, &mfe) {
		t.Fatalf("err = %v, want errors.As(err, **MissingFlagError) to succeed", err)
	}
	if mfe.Flag != "addr" {
		t.Fatalf("MissingFlagError.Flag = %q, want addr", mfe.Flag)
	}
	if !strings.Contains(mfe.Error(), "addr") {
		t.Fatalf("Error() = %q, should mention the flag name", mfe.Error())
	}
}

func TestBindRequiredSatisfiedByEmptyValue(t *testing.T) {
	var c serveCfg
	err := Bind(&c, []string{"--addr="})
	if err != nil {
		t.Fatalf("an explicitly provided empty value should satisfy required: %v", err)
	}
	if c.Addr != "" {
		t.Fatalf("Addr = %q, want empty string", c.Addr)
	}
}

func TestBindRequiredOnDurationField(t *testing.T) {
	var c struct {
		Grace time.Duration `flag:"grace,required"`
	}
	err := Bind(&c, nil)
	var mfe *MissingFlagError
	if !errors.As(err, &mfe) {
		t.Fatalf("err = %v, want a *MissingFlagError", err)
	}
	if mfe.Flag != "grace" {
		t.Fatalf("MissingFlagError.Flag = %q, want grace", mfe.Flag)
	}
	if err := Bind(&c, []string{"--grace=1h"}); err != nil {
		t.Fatalf("Bind with required duration provided: %v", err)
	}
	if c.Grace != time.Hour {
		t.Fatalf("Grace = %v, want 1h", c.Grace)
	}
}

func TestBindRequiredErrorsStillFlagErrorsFirst(t *testing.T) {
	// A bad value must surface as its own parse error even when another
	// required flag is missing.
	var c serveCfg
	err := Bind(&c, []string{"--retries=many"})
	if err == nil {
		t.Fatal("Bind accepted a bad int")
	}
	var mfe *MissingFlagError
	if errors.As(err, &mfe) {
		t.Fatalf("parse failure misreported as missing-required: %v", err)
	}
}

func TestBindUnknownFlagStillDetectedWithNewTypes(t *testing.T) {
	var c serveCfg
	if err := Bind(&c, []string{"--addr=x", "--tags=oops"}); !errors.Is(err, ErrUnknownFlag) {
		t.Fatalf("err = %v, want ErrUnknownFlag for --tags (flag is --tag)", err)
	}
}
