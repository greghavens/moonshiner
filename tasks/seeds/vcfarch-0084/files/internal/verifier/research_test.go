package verifier

import (
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

func TestResearchRecordsLiveBroadcomSources(t *testing.T) {
	root := filepath.Join("..", "..")
	raw, err := os.ReadFile(filepath.Join(root, "research.md"))
	if err != nil {
		t.Fatalf("research.md is required: %v", err)
	}
	content := string(raw)

	dateMatch := regexp.MustCompile(`(?i)consulted on\s+(\d{4}-\d{2}-\d{2})`).FindStringSubmatch(content)
	if dateMatch == nil {
		t.Fatal("research.md must state its consultation date as YYYY-MM-DD")
	}
	if _, err := time.Parse("2006-01-02", dateMatch[1]); err != nil {
		t.Fatalf("research.md has an invalid consultation date: %v", err)
	}

	entryPattern := regexp.MustCompile(`^- \*\*(.+)\*\* — (https://\S+) — (.+)$`)
	seenURLs := map[string]bool{}
	for _, line := range strings.Split(content, "\n") {
		match := entryPattern.FindStringSubmatch(line)
		if match == nil {
			continue
		}
		if strings.TrimSpace(match[1]) == "" || strings.TrimSpace(match[3]) == "" {
			t.Fatalf("research entry must include a page title and informed decision: %q", line)
		}
		parsed, err := url.Parse(match[2])
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" {
			t.Fatalf("research entry has an invalid HTTPS URL: %q", match[2])
		}
		host := strings.ToLower(parsed.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			t.Fatalf("research source is not a published Broadcom page: %q", match[2])
		}
		seenURLs[parsed.String()] = true
	}
	if len(seenURLs) < 2 {
		t.Fatalf("research.md records %d distinct Broadcom pages; want multiple live sources", len(seenURLs))
	}

	lower := strings.ToLower(content)
	for _, subject := range []string{"compatib", "interoperab", "bill of materials", "upgrade-path"} {
		if !strings.Contains(lower, subject) {
			t.Fatalf("research.md does not record the required %q research", subject)
		}
	}
	if strings.Contains(lower, ".invalid") {
		t.Fatal("research.md must not use reserved non-reachable URLs")
	}
}
