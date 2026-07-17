package flagbind

import (
	"errors"
	"strings"
	"testing"
)

type baseCfg struct {
	Host    string `flag:"host"`
	Port    int    `flag:"port"`
	Comment string // untagged: never bound
	Secret  string `flag:"-"`
}

func TestBindBothArgumentForms(t *testing.T) {
	var c baseCfg
	err := Bind(&c, []string{"--host=db1.internal", "--port", "5432"})
	if err != nil {
		t.Fatalf("Bind: %v", err)
	}
	if c.Host != "db1.internal" {
		t.Fatalf("Host = %q, want db1.internal", c.Host)
	}
	if c.Port != 5432 {
		t.Fatalf("Port = %d, want 5432", c.Port)
	}
}

func TestBindKeepsDefaultsWhenFlagAbsent(t *testing.T) {
	c := baseCfg{Host: "localhost", Port: 8080}
	if err := Bind(&c, []string{"--port=9090"}); err != nil {
		t.Fatalf("Bind: %v", err)
	}
	if c.Host != "localhost" {
		t.Fatalf("Host = %q, want default localhost preserved", c.Host)
	}
	if c.Port != 9090 {
		t.Fatalf("Port = %d, want 9090", c.Port)
	}
}

func TestBindUnknownFlag(t *testing.T) {
	var c baseCfg
	err := Bind(&c, []string{"--hots=oops"})
	if !errors.Is(err, ErrUnknownFlag) {
		t.Fatalf("err = %v, want errors.Is(err, ErrUnknownFlag)", err)
	}
	if !strings.Contains(err.Error(), "--hots") {
		t.Fatalf("error %q should name the offending flag", err)
	}
}

func TestBindRejectsBadInt(t *testing.T) {
	var c baseCfg
	err := Bind(&c, []string{"--port=eighty"})
	if err == nil {
		t.Fatal("Bind accepted a non-integer for an int field")
	}
	if !strings.Contains(err.Error(), "--port") {
		t.Fatalf("error %q should name the flag", err)
	}
}

func TestBindValueMissingAtEnd(t *testing.T) {
	var c baseCfg
	if err := Bind(&c, []string{"--host"}); err == nil {
		t.Fatal("Bind accepted a trailing flag with no value")
	}
}

func TestBindRejectsPositionalArgument(t *testing.T) {
	var c baseCfg
	if err := Bind(&c, []string{"serve", "--port=1"}); err == nil {
		t.Fatal("Bind accepted a positional argument")
	}
}

func TestBindIgnoresUntaggedAndDashFields(t *testing.T) {
	var c baseCfg
	if err := Bind(&c, []string{"--Comment=x"}); !errors.Is(err, ErrUnknownFlag) {
		t.Fatalf("untagged field bound: err = %v", err)
	}
	if err := Bind(&c, []string{"---=x"}); !errors.Is(err, ErrUnknownFlag) {
		t.Fatalf(`flag:"-" field bound: err = %v`, err)
	}
}

func TestBindUnsupportedFieldType(t *testing.T) {
	var c struct {
		Rate float64 `flag:"rate"`
	}
	err := Bind(&c, []string{"--rate=0.5"})
	if err == nil {
		t.Fatal("Bind accepted a float64 field; only string and int are supported today")
	}
}

func TestBindRequiresStructPointer(t *testing.T) {
	var n int
	if err := Bind(&n, nil); err == nil {
		t.Fatal("Bind accepted a *int destination")
	}
	if err := Bind(baseCfg{}, nil); err == nil {
		t.Fatal("Bind accepted a non-pointer destination")
	}
}
