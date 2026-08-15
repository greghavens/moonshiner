package verifier

import (
	"net"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"
)

func TestResearchRecord(t *testing.T) {
	raw, err := os.ReadFile("../research.md")
	if err != nil {
		t.Fatalf("read research.md: %v", err)
	}
	research := string(raw)

	seenURLs := map[string]bool{}
	officialSources := 0
	rows := 0
	for lineNumber, line := range strings.Split(research, "\n") {
		if !strings.HasPrefix(strings.TrimSpace(line), "|") {
			continue
		}
		cells := strings.Split(line, "|")
		if len(cells) != 6 {
			t.Fatalf("research.md table row %d must have four columns", lineNumber+1)
		}
		title := strings.TrimSpace(cells[1])
		if strings.EqualFold(title, "source title") || strings.EqualFold(title, "title") ||
			strings.Trim(title, " :-") == "" {
			continue
		}
		candidate := strings.TrimSpace(cells[2])
		date := strings.TrimSpace(cells[3])
		contribution := strings.TrimSpace(cells[4])
		if title == "" {
			t.Fatalf("research.md table row %d has no source title", lineNumber+1)
		}
		parsed, err := url.Parse(candidate)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" {
			t.Fatalf("research.md table row %d has invalid plain HTTPS source URL %q", lineNumber+1, candidate)
		}
		host := strings.ToLower(parsed.Hostname())
		if !publicResearchHost(host) {
			t.Fatalf("research.md source URL %q is not a public source", candidate)
		}
		if seenURLs[candidate] {
			t.Fatalf("research.md source URL is duplicated: %q", candidate)
		}
		seenURLs[candidate] = true
		if _, err := time.Parse("2006-01-02", date); err != nil {
			t.Fatalf("research.md table row %d has invalid YYYY-MM-DD access date %q", lineNumber+1, date)
		}
		if len(strings.Fields(contribution)) < 3 {
			t.Fatalf("research.md table row %d has no useful contribution statement", lineNumber+1)
		}
		if host == "broadcom.com" || strings.HasSuffix(host, ".broadcom.com") ||
			host == "vmware.com" || strings.HasSuffix(host, ".vmware.com") {
			officialSources++
		}
		rows++
	}
	if rows < 2 {
		t.Fatalf("research.md records %d source rows; want multiple sources", rows)
	}
	if officialSources == 0 {
		t.Fatal("research.md must include currently published Broadcom or VMware material")
	}

	lower := strings.ToLower(research)
	topics := []struct {
		name  string
		terms []string
	}{
		{name: "compatibility/interoperability", terms: []string{"compatib", "interop"}},
		{name: "upgrade path or ordering", terms: []string{"upgrade", "sequence", "order", "hop"}},
		{name: "Edge sizing or uplink layout", terms: []string{"edge", "uplink", "throughput", "form factor"}},
	}
	for _, topic := range topics {
		if !containsAny(lower, topic.terms) {
			t.Errorf("research.md does not document %s research", topic.name)
		}
	}
}

func publicResearchHost(host string) bool {
	if host == "localhost" || strings.HasSuffix(host, ".localhost") ||
		strings.HasSuffix(host, ".invalid") || strings.HasSuffix(host, ".example") ||
		strings.HasSuffix(host, ".test") {
		return false
	}
	if address := net.ParseIP(host); address != nil {
		return !address.IsLoopback() && !address.IsPrivate() && !address.IsUnspecified()
	}
	return strings.Contains(host, ".")
}

func containsAny(value string, terms []string) bool {
	for _, term := range terms {
		if strings.Contains(value, term) {
			return true
		}
	}
	return false
}
