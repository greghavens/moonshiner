package contract

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"strings"
	"time"
)

// SourcesHost is the only host docs/official_sources.json may cite. The VCF
// Automation reference lives on the Broadcom developer portal ("xAPIs").
const SourcesHost = "developer.broadcom.com"

// FetchedAtLayout is the required date format for SourceRecord.FetchedAt.
const FetchedAtLayout = "2006-01-02"

// Sources is the whole of docs/official_sources.json: the provenance record
// for every operation in the contract.
type Sources struct {
	Portal  string         `json:"portal"`
	Note    string         `json:"note"`
	Records []SourceRecord `json:"records"`
}

// SourceRecord ties one contract operation to the one reference page it was
// read from, and to the day that page was read.
type SourceRecord struct {
	// Operation is a contract operation ID.
	Operation string `json:"operation"`
	// URL is the reference page, on SourcesHost.
	URL string `json:"url"`
	// Title is the page's own title, as displayed.
	Title string `json:"title"`
	// FetchedAt is the date the page was read, as YYYY-MM-DD.
	FetchedAt string `json:"fetched_at"`
}

// LoadSources reads and validates the provenance record at path.
func LoadSources(path string) (*Sources, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("sources: %w", err)
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	var s Sources
	if err := dec.Decode(&s); err != nil {
		return nil, fmt.Errorf("sources %s: %w", path, err)
	}
	if err := s.Validate(); err != nil {
		return nil, fmt.Errorf("sources %s: %w", path, err)
	}
	return &s, nil
}

// Validate checks that every record names an operation, cites a page on
// SourcesHost, and records the day it was fetched.
func (s *Sources) Validate() error {
	if len(s.Records) == 0 {
		return fmt.Errorf("records must not be empty")
	}
	for i, r := range s.Records {
		if strings.TrimSpace(r.Operation) == "" {
			return fmt.Errorf("records[%d]: operation must not be empty", i)
		}
		if strings.TrimSpace(r.Title) == "" {
			return fmt.Errorf("records[%d]: title must not be empty", i)
		}
		u, err := url.Parse(r.URL)
		if err != nil {
			return fmt.Errorf("records[%d]: url %q: %w", i, r.URL, err)
		}
		if u.Scheme != "https" {
			return fmt.Errorf("records[%d]: url %q must be https", i, r.URL)
		}
		if u.Host != SourcesHost {
			return fmt.Errorf("records[%d]: url %q must be on %s", i, r.URL, SourcesHost)
		}
		if u.Path == "" || u.Path == "/" {
			return fmt.Errorf("records[%d]: url %q must name a page, not just the host", i, r.URL)
		}
		if _, err := time.Parse(FetchedAtLayout, r.FetchedAt); err != nil {
			return fmt.Errorf("records[%d]: fetched_at %q must be %s", i, r.FetchedAt, FetchedAtLayout)
		}
	}
	return nil
}

// For returns every record citing the given operation ID.
func (s *Sources) For(operation string) []SourceRecord {
	var out []SourceRecord
	for _, r := range s.Records {
		if r.Operation == operation {
			out = append(out, r)
		}
	}
	return out
}

// Covers reports the operation IDs in the contract that have no source record.
func (s *Sources) Covers(c *Contract) []string {
	var missing []string
	for _, op := range c.Operations {
		if len(s.For(op.ID)) == 0 {
			missing = append(missing, op.ID)
		}
	}
	return missing
}
