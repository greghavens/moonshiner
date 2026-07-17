package config

import (
	"strings"
	"testing"
)

func baseConfig(t *testing.T) *Config {
	t.Helper()
	return New(map[string]any{
		"server.port": 8080,
		"server.host": "localhost",
		"log.level":   "info",
		"debug":       false,
		"timeout.sec": 30.0,
	})
}

func TestDefaultsAreVisibleThroughTypedGetters(t *testing.T) {
	c := baseConfig(t)
	if v, err := c.Int("server.port"); err != nil || v != 8080 {
		t.Fatalf("Int(server.port) = (%d, %v), want (8080, nil)", v, err)
	}
	if v, err := c.String("server.host"); err != nil || v != "localhost" {
		t.Fatalf("String(server.host) = (%q, %v)", v, err)
	}
	if v, err := c.Bool("debug"); err != nil || v != false {
		t.Fatalf("Bool(debug) = (%v, %v)", v, err)
	}
	if v, err := c.Float("timeout.sec"); err != nil || v != 30.0 {
		t.Fatalf("Float(timeout.sec) = (%v, %v)", v, err)
	}
}

func TestFileLayerOverridesDefaultsButOnlyWhereSet(t *testing.T) {
	c := baseConfig(t)
	err := c.LoadJSON([]byte(`{
		"server": {"port": 9000},
		"log":    {"level": "debug"}
	}`))
	if err != nil {
		t.Fatalf("LoadJSON: %v", err)
	}
	if v, _ := c.Int("server.port"); v != 9000 {
		t.Fatalf("server.port = %d, want file value 9000", v)
	}
	if v, _ := c.String("log.level"); v != "debug" {
		t.Fatalf("log.level = %q, want file value debug", v)
	}
	// Keys the file does not mention keep their defaults: merge, not replace.
	if v, _ := c.String("server.host"); v != "localhost" {
		t.Fatalf("server.host = %q, want default localhost (file must merge)", v)
	}
	if v, _ := c.Bool("debug"); v != false {
		t.Fatalf("debug = %v, want default false", v)
	}
}

func TestNestedJSONFlattensToDotPaths(t *testing.T) {
	c := New(nil)
	err := c.LoadJSON([]byte(`{"db": {"pool": {"max": 25, "idle": 5}}, "name": "svc"}`))
	if err != nil {
		t.Fatalf("LoadJSON: %v", err)
	}
	if v, err := c.Int("db.pool.max"); err != nil || v != 25 {
		t.Fatalf("Int(db.pool.max) = (%d, %v), want (25, nil)", v, err)
	}
	if v, err := c.Int("db.pool.idle"); err != nil || v != 5 {
		t.Fatalf("Int(db.pool.idle) = (%d, %v)", v, err)
	}
	if v, err := c.String("name"); err != nil || v != "svc" {
		t.Fatalf("String(name) = (%q, %v)", v, err)
	}
}

func TestEnvLayerBeatsFileAndDefaultsRegardlessOfLoadOrder(t *testing.T) {
	c := baseConfig(t)
	// Env is loaded FIRST, file second — env must still win.
	if err := c.LoadEnv("APP_", []string{"APP_SERVER_PORT=7777"}); err != nil {
		t.Fatalf("LoadEnv: %v", err)
	}
	if err := c.LoadJSON([]byte(`{"server": {"port": 9000}}`)); err != nil {
		t.Fatalf("LoadJSON: %v", err)
	}
	if v, err := c.Int("server.port"); err != nil || v != 7777 {
		t.Fatalf("server.port = (%d, %v), want env value 7777 — precedence is fixed, not load-order", v, err)
	}
}

func TestEnvNameMapping(t *testing.T) {
	c := baseConfig(t)
	err := c.LoadEnv("APP_", []string{
		"APP_LOG_LEVEL=warn",       // -> log.level
		"APP_DEBUG=true",           // -> debug
		"OTHERAPP_LOG_LEVEL=nope",  // wrong prefix: ignored
		"PATH=/usr/bin",            // unrelated: ignored
		"MALFORMED-NO-EQUALS-SIGN", // skipped, not an error
		"APP_SERVER_HOST=",         // empty value still overrides
	})
	if err != nil {
		t.Fatalf("LoadEnv: %v", err)
	}
	if v, _ := c.String("log.level"); v != "warn" {
		t.Fatalf("log.level = %q, want warn (APP_LOG_LEVEL should map to log.level)", v)
	}
	if v, err := c.Bool("debug"); err != nil || v != true {
		t.Fatalf("Bool(debug) = (%v, %v), want (true, nil) — env strings must coerce for Bool", v, err)
	}
	if v, _ := c.String("server.host"); v != "" {
		t.Fatalf("server.host = %q, want empty string (explicitly set empty in env)", v)
	}
}

func TestEnvRequiresPrefix(t *testing.T) {
	c := New(nil)
	if err := c.LoadEnv("", []string{"X=1"}); err == nil {
		t.Fatal("empty prefix accepted; that would slurp the whole environment")
	}
}

func TestIntCoercions(t *testing.T) {
	c := New(map[string]any{"whole.float": 42.0, "frac.float": 7.5, "flag": true})
	if err := c.LoadJSON([]byte(`{"json.port": 8443}`)); err != nil {
		t.Fatalf("LoadJSON: %v", err)
	}
	if err := c.LoadEnv("APP_", []string{"APP_ENV_PORT=9090", "APP_BAD_PORT=lots"}); err != nil {
		t.Fatalf("LoadEnv: %v", err)
	}
	if v, err := c.Int("json.port"); err != nil || v != 8443 {
		t.Fatalf("Int over JSON number = (%d, %v), want (8443, nil)", v, err)
	}
	if v, err := c.Int("env.port"); err != nil || v != 9090 {
		t.Fatalf("Int over env string = (%d, %v), want (9090, nil)", v, err)
	}
	if v, err := c.Int("whole.float"); err != nil || v != 42 {
		t.Fatalf("Int over integral float = (%d, %v), want (42, nil)", v, err)
	}
	if _, err := c.Int("frac.float"); err == nil {
		t.Fatal("Int over 7.5 must error, not truncate")
	}
	if _, err := c.Int("bad.port"); err == nil || !strings.Contains(err.Error(), "bad.port") {
		t.Fatalf("Int over unparseable env string: want error naming the key, got %v", err)
	}
	if _, err := c.Int("flag"); err == nil {
		t.Fatal("Int over a bool must error")
	}
}

func TestFloatAndBoolAndStringCoercions(t *testing.T) {
	c := New(map[string]any{"ratio": 0.25, "count": 4, "label": "prod"})
	if err := c.LoadEnv("APP_", []string{"APP_SHARE=0.75", "APP_STRICT=1"}); err != nil {
		t.Fatalf("LoadEnv: %v", err)
	}
	if v, err := c.Float("ratio"); err != nil || v != 0.25 {
		t.Fatalf("Float(ratio) = (%v, %v)", v, err)
	}
	if v, err := c.Float("count"); err != nil || v != 4.0 {
		t.Fatalf("Float over int = (%v, %v), want (4, nil)", v, err)
	}
	if v, err := c.Float("share"); err != nil || v != 0.75 {
		t.Fatalf("Float over env string = (%v, %v), want (0.75, nil)", v, err)
	}
	if v, err := c.Bool("strict"); err != nil || v != true {
		t.Fatalf("Bool over env \"1\" = (%v, %v), want (true, nil)", v, err)
	}
	if v, err := c.String("label"); err != nil || v != "prod" {
		t.Fatalf("String(label) = (%q, %v)", v, err)
	}
	if _, err := c.String("count"); err == nil {
		t.Fatal("String over an int must error — no silent stringification")
	}
	if _, err := c.Bool("label"); err == nil {
		t.Fatal("Bool over \"prod\" must error")
	}
}

func TestMissingKeyErrorsNameTheKey(t *testing.T) {
	c := baseConfig(t)
	for _, probe := range []func() error{
		func() error { _, err := c.String("no.such.key"); return err },
		func() error { _, err := c.Int("no.such.key"); return err },
		func() error { _, err := c.Bool("no.such.key"); return err },
		func() error { _, err := c.Float("no.such.key"); return err },
	} {
		err := probe()
		if err == nil || !strings.Contains(err.Error(), "no.such.key") {
			t.Fatalf("missing key: want error naming no.such.key, got %v", err)
		}
	}
}

func TestHas(t *testing.T) {
	c := baseConfig(t)
	if !c.Has("server.port") {
		t.Fatal("Has(server.port) = false")
	}
	if c.Has("nope") {
		t.Fatal("Has(nope) = true")
	}
	if err := c.LoadEnv("APP_", []string{"APP_FEATURE_X=on"}); err != nil {
		t.Fatalf("LoadEnv: %v", err)
	}
	if !c.Has("feature.x") {
		t.Fatal("Has must see keys contributed by the env layer")
	}
}

func TestLoadJSONRejectsMalformedInput(t *testing.T) {
	c := New(nil)
	if err := c.LoadJSON([]byte(`{"a": `)); err == nil {
		t.Fatal("malformed JSON accepted")
	}
}
