package keycatalog

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"
)

// Catalog is an immutable index of persisted artifact records.
type Catalog struct {
	records map[string]Record
	ids     []string
}

// Load reads a JSON-lines catalog.
func Load(reader io.Reader) (*Catalog, error) {
	records := make(map[string]Record)

	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 1024), 1024*1024)
	line := 0
	for scanner.Scan() {
		line++
		if len(scanner.Bytes()) == 0 {
			continue
		}
		var record Record
		if err := json.Unmarshal(scanner.Bytes(), &record); err != nil {
			return nil, fmt.Errorf("catalog line %d: decode record: %w", line, err)
		}
		key, err := lookupKey(record.ID)
		if err != nil {
			return nil, fmt.Errorf("catalog line %d: %w", line, err)
		}
		if _, exists := records[key]; exists {
			return nil, fmt.Errorf("catalog line %d: duplicate identifier %q", line, record.ID)
		}
		records[key] = record
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read catalog: %w", err)
	}

	ids := make([]string, 0, len(records))
	for key := range records {
		ids = append(ids, key)
	}
	sort.Strings(ids)
	return &Catalog{records: records, ids: ids}, nil
}

// Lookup finds a record by protocol identifier.
func (c *Catalog) Lookup(identifier string) (Record, bool) {
	if c == nil {
		return Record{}, false
	}
	key, err := lookupKey(identifier)
	if err != nil {
		return Record{}, false
	}
	record, ok := c.records[key]
	return record, ok
}

// IDs returns catalog identifiers in deterministic lookup order.
func (c *Catalog) IDs() []string {
	if c == nil {
		return nil
	}
	return c.ids
}

func lookupKey(identifier string) (string, error) {
	parts := strings.Split(identifier, "/")
	if len(parts) < 3 || parts[0] == "" || parts[1] == "" || parts[2] == "" {
		return "", fmt.Errorf("invalid identifier %q", identifier)
	}
	return strings.ToLower(identifier), nil
}
