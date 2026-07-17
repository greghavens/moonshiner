// Package importer ingests contact exports from the old CRM. The export
// format is line-based: `name|email|tags` with `#` comments and blank
// lines allowed. Real exports are messy, so the importer keeps whatever
// parses and reports the problem lines instead of aborting the whole run.
package importer

import (
	"fmt"
	"strings"
)

// Contact is one imported CRM record.
type Contact struct {
	Name  string
	Email string
	Tags  []string
}

// ImportAll parses every record line. Malformed lines are skipped so a
// single bad row can't sink a 50k-row import, but the error from the most
// recent bad line is returned alongside the good records so the caller can
// warn the operator that the import was not clean.
func ImportAll(lines []string) ([]Contact, error) {
	var contacts []Contact
	var err error
	for n, ln := range lines {
		ln = strings.TrimSpace(ln)
		if ln == "" || strings.HasPrefix(ln, "#") {
			continue
		}
		c, err := parseLine(ln)
		if err != nil {
			err = fmt.Errorf("line %d: %w", n+1, err)
			continue
		}
		contacts = append(contacts, c)
	}
	return contacts, err
}

func parseLine(ln string) (Contact, error) {
	parts := strings.Split(ln, "|")
	if len(parts) != 3 {
		return Contact{}, fmt.Errorf("expected 3 fields, got %d", len(parts))
	}
	name := strings.TrimSpace(parts[0])
	email := strings.TrimSpace(parts[1])
	if name == "" {
		return Contact{}, fmt.Errorf("empty name")
	}
	at := strings.Index(email, "@")
	if at <= 0 || at == len(email)-1 || strings.ContainsAny(email, " \t") {
		return Contact{}, fmt.Errorf("invalid email %q", email)
	}
	var tags []string
	for _, t := range strings.Split(parts[2], ",") {
		if t = strings.TrimSpace(t); t != "" {
			tags = append(tags, strings.ToLower(t))
		}
	}
	return Contact{Name: name, Email: strings.ToLower(email), Tags: tags}, nil
}
