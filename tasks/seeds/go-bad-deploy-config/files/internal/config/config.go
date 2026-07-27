// Package config loads and validates relay configuration.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
)

const (
	HTTPAddressKey   = "http.address"
	UpstreamURLKey   = "upstream.url"
	UpstreamTokenKey = "upstream.token"
	DatabaseDSNKey   = "database.dsn"
)

// Source identifies the layer which supplied an effective value.
type Source string

const (
	SourceDefault Source = "default"
	SourceFile    Source = "file"
	SourceEnv     Source = "env"
	SourceUnset   Source = "unset"
)

type entry struct {
	value  string
	source Source
}

// Config is the effective configuration together with value provenance.
// Its fields stay private so diagnostics go through String or SafeSummary.
type Config struct {
	values map[string]entry
}

var defaultValues = map[string]string{
	HTTPAddressKey: ":8080",
	UpstreamURLKey: "https://collector.example.com/v1/events",
}

var environmentKeys = map[string]string{
	"RELAY_HTTP_ADDRESS":   HTTPAddressKey,
	"RELAY_UPSTREAM_URL":   UpstreamURLKey,
	"RELAY_UPSTREAM_TOKEN": UpstreamTokenKey,
	"RELAY_DATABASE_DSN":   DatabaseDSNKey,
}

type fileConfig struct {
	HTTP *struct {
		Address *string `json:"address"`
	} `json:"http"`
	Upstream *struct {
		URL   *string `json:"url"`
		Token *string `json:"token"`
	} `json:"upstream"`
	Database *struct {
		DSN *string `json:"dsn"`
	} `json:"database"`
}

// Load applies built-in defaults, the JSON file, and then environment values.
// A blank configPath omits the file layer. environ uses os.Environ's NAME=VALUE
// representation and is passed explicitly to keep loading deterministic.
func Load(configPath string, environ []string) (Config, error) {
	cfg := Config{values: make(map[string]entry, len(defaultValues))}
	for key, value := range defaultValues {
		cfg.set(key, value, SourceDefault)
	}

	if configPath != "" {
		if err := cfg.applyFile(configPath); err != nil {
			return Config{}, err
		}
	}
	cfg.applyEnv(environ)

	if err := cfg.Validate(); err != nil {
		return Config{}, fmt.Errorf(
			"invalid configuration: %w; effective config: %s",
			err,
			cfg.debugDump(),
		)
	}
	return cfg, nil
}

func (c *Config) applyFile(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("read config file %q: %w", path, err)
	}
	defer f.Close()

	var decoded fileConfig
	decoder := json.NewDecoder(f)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&decoded); err != nil {
		return fmt.Errorf("decode config file %q: %w", path, err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		if err == nil {
			err = errors.New("multiple JSON values")
		}
		return fmt.Errorf("decode config file %q: %w", path, err)
	}

	if decoded.HTTP != nil && decoded.HTTP.Address != nil {
		c.set(HTTPAddressKey, *decoded.HTTP.Address, SourceFile)
	}
	if decoded.Upstream != nil {
		if decoded.Upstream.URL != nil {
			c.set(UpstreamURLKey, *decoded.Upstream.URL, SourceFile)
		}
		if decoded.Upstream.Token != nil {
			c.set(UpstreamTokenKey, *decoded.Upstream.Token, SourceFile)
		}
	}
	if decoded.Database != nil && decoded.Database.DSN != nil {
		c.set(DatabaseDSNKey, *decoded.Database.DSN, SourceFile)
	}
	return nil
}

func (c *Config) applyEnv(environ []string) {
	for _, item := range environ {
		name, value, ok := strings.Cut(item, "=")
		if !ok {
			continue
		}
		key, recognized := environmentKeys[name]
		if !recognized {
			continue
		}
		c.set(key, value, SourceEnv)
	}
}

func (c *Config) set(key, value string, source Source) {
	c.values[key] = entry{value: value, source: source}
}

// Value returns the effective raw value. Callers should avoid logging secret
// keys; SafeSummary is the diagnostic representation.
func (c Config) Value(key string) (string, bool) {
	item, ok := c.values[key]
	return item.value, ok
}

// Source returns the layer which supplied key's effective value.
func (c Config) Source(key string) (Source, bool) {
	item, ok := c.values[key]
	return item.source, ok
}

func (c Config) HTTPAddress() string {
	value, _ := c.Value(HTTPAddressKey)
	return value
}

func (c Config) UpstreamURL() string {
	value, _ := c.Value(UpstreamURLKey)
	return value
}

func (c Config) UpstreamToken() string {
	value, _ := c.Value(UpstreamTokenKey)
	return value
}

func (c Config) DatabaseDSN() string {
	value, _ := c.Value(DatabaseDSNKey)
	return value
}

// Validate checks only the fields that historically prevented connection
// setup. More complete shape validation belongs here, before startup proceeds.
func (c Config) Validate() error {
	if strings.TrimSpace(c.UpstreamURL()) == "" {
		return errors.New("upstream URL is required")
	}
	if strings.TrimSpace(c.UpstreamToken()) == "" {
		return errors.New("upstream token is required")
	}
	if strings.TrimSpace(c.DatabaseDSN()) == "" {
		return errors.New("database DSN is required")
	}
	return nil
}

func (c Config) debugDump() string {
	return fmt.Sprintf(
		"http.address=%q upstream.url=%q upstream.token=%q database.dsn=%q",
		c.HTTPAddress(),
		c.UpstreamURL(),
		c.UpstreamToken(),
		c.DatabaseDSN(),
	)
}

// SafeSummary returns the configuration representation used by startup logs.
func (c Config) SafeSummary() string {
	return c.debugDump()
}

func (c Config) String() string {
	return c.SafeSummary()
}
