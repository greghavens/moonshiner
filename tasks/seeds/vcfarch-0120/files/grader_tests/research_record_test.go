package grader_tests

import (
	"net/url"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"
)

func TestResearchRecord(t *testing.T) {
	content, err := os.ReadFile(rootPath("research.md"))
	if err != nil {
		t.Fatalf("read research.md: %v", err)
	}
	text := strings.TrimSpace(string(content))
	if text == "" {
		t.Fatal("research.md is empty")
	}
	entryPattern := regexp.MustCompile(`^-[[:space:]]+Title:[[:space:]]+(.+)[[:space:]]+\|[[:space:]]+URL:[[:space:]]+(https?://[^[:space:]]+)[[:space:]]+\|[[:space:]]+Accessed:[[:space:]]+(\d{4}-\d{2}-\d{2})[[:space:]]+\|[[:space:]]+Note:[[:space:]]+(.+)$`)
	var entries [][]string
	for _, line := range strings.Split(text, "\n") {
		if match := entryPattern.FindStringSubmatch(strings.TrimSpace(line)); match != nil {
			entries = append(entries, match)
		}
	}
	if len(entries) == 0 {
		t.Fatal("research.md has no complete source entries")
	}
	foundBroadcomSource := false
	for _, entry := range entries {
		title, raw, note := strings.TrimSpace(entry[1]), strings.TrimRight(entry[2], ".,;"), strings.TrimSpace(entry[4])
		if title == "" || note == "" {
			t.Error("research.md source entry has an empty title or note")
		}
		if _, err := time.Parse("2006-01-02", entry[3]); err != nil {
			t.Errorf("research.md source entry has an invalid access date %q", entry[3])
		}
		parsed, err := url.Parse(raw)
		if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" {
			t.Errorf("research.md contains an invalid source URL %q", raw)
			continue
		}
		host := strings.ToLower(parsed.Hostname())
		if host == "localhost" || strings.HasSuffix(host, ".invalid") {
			t.Errorf("research.md source URL is not publicly reachable: %q", raw)
		}
		if host == "broadcom.com" || strings.HasSuffix(host, ".broadcom.com") ||
			host == "vmware.com" || strings.HasSuffix(host, ".vmware.com") {
			foundBroadcomSource = true
		}
	}
	if !foundBroadcomSource {
		t.Error("research.md does not identify a Broadcom-published source")
	}
}
