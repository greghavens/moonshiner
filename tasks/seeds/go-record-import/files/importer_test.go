package importer

import (
	"strings"
	"testing"
)

func TestCleanImport(t *testing.T) {
	lines := []string{
		"# CRM export 2026-07-01",
		"Ada Lovelace | ada@example.com | vip, engineering",
		"",
		"Grace Hopper | grace@example.com | navy",
	}
	contacts, err := ImportAll(lines)
	if err != nil {
		t.Fatalf("clean import returned error: %v", err)
	}
	if len(contacts) != 2 {
		t.Fatalf("imported %d contacts, want 2", len(contacts))
	}
	if contacts[0].Email != "ada@example.com" || len(contacts[0].Tags) != 2 {
		t.Fatalf("first contact parsed wrong: %+v", contacts[0])
	}
}

func TestMessyImportKeepsGoodRowsAndReportsProblem(t *testing.T) {
	lines := []string{
		"Ada Lovelace | ada@example.com | vip",
		"this line is junk without any pipes",
		"Grace Hopper | grace@example.com | navy",
	}
	contacts, err := ImportAll(lines)
	if len(contacts) != 2 {
		t.Fatalf("imported %d contacts, want the 2 good rows", len(contacts))
	}
	if err == nil {
		t.Fatal("import had a malformed row but ImportAll reported a clean run (nil error)")
	}
	if !strings.Contains(err.Error(), "line 2") {
		t.Fatalf("error should identify the offending line: %v", err)
	}
}

func TestLastProblemWins(t *testing.T) {
	lines := []string{
		"broken-row-one",
		"Ada Lovelace | ada@example.com | vip",
		"Karen Sparck Jones | not-an-email | ir",
	}
	contacts, err := ImportAll(lines)
	if len(contacts) != 1 {
		t.Fatalf("imported %d contacts, want 1", len(contacts))
	}
	if err == nil {
		t.Fatal("expected an error for the malformed rows, got nil")
	}
	if !strings.Contains(err.Error(), "line 3") {
		t.Fatalf("error should point at the most recent bad row: %v", err)
	}
}

func TestFieldValidation(t *testing.T) {
	bad := []string{
		" | someone@example.com | x",
		"Bob | bob at example dot com | x",
		"Bob | bob@ | x",
		"Bob | @example.com | x",
	}
	for _, ln := range bad {
		contacts, err := ImportAll([]string{ln})
		if len(contacts) != 0 || err == nil {
			t.Fatalf("row %q: got %d contacts, err=%v; want 0 contacts and an error", ln, len(contacts), err)
		}
	}
}
