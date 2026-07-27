package protected_tests

import (
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"

	catalog "example.com/keycatalog"
)

func loadText(t *testing.T, text string) *catalog.Catalog {
	t.Helper()
	got, err := catalog.Load(strings.NewReader(text))
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	return got
}

func lookup(t *testing.T, index *catalog.Catalog, id string) catalog.Record {
	t.Helper()
	record, ok := index.Lookup(id)
	if !ok {
		t.Fatalf("Lookup(%q) missed", id)
	}
	return record
}

func TestProductionEvidenceRemainsDistinctAndAddressable(t *testing.T) {
	file, err := os.Open("../evidence/catalog-export.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()

	index, err := catalog.Load(file)
	if err != nil {
		t.Fatalf("Load evidence: %v", err)
	}

	tests := []struct {
		query   string
		wantID  string
		payload string
	}{
		{
			"CELL-A/payments/InvoicePDF",
			"Cell-A/Payments/InvoicePDF",
			"invoice renderer v3",
		},
		{
			"cell-a/PAYMENTS/invoicepdf",
			"cell-a/payments/invoicepdf",
			"legacy invoice renderer",
		},
		{
			"eu.2/docs/Manuals/RunBook",
			"EU.2/Docs/Manuals/RunBook",
			"operations runbook",
		},
		{
			"EU.2/Docs/manuals/RunBook",
			"eu.2/DOCS/manuals/RunBook",
			"customer runbook",
		},
		{
			"ap_3/MEDIA/Résumé+2026",
			"AP_3/Media/Résumé+2026",
			"campaign asset",
		},
		{
			"AP_3/media/résumé+2026",
			"ap_3/media/résumé+2026",
			"localized campaign asset",
		},
	}
	for _, test := range tests {
		t.Run(test.wantID, func(t *testing.T) {
			got := lookup(t, index, test.query)
			if got.ID != test.wantID || got.Payload != test.payload {
				t.Fatalf("Lookup(%q) = %+v", test.query, got)
			}
		})
	}

	if _, ok := index.Lookup("cell-a/payments/INVOICEPDF"); ok {
		t.Fatal("an unpersisted key spelling unexpectedly matched")
	}
}

func TestNestedOpaqueKeyUsesOnlyTwoRoutingSeparators(t *testing.T) {
	index := loadText(t, strings.Join([]string{
		`{"id":"West-1/Team_A/Guides/Go/Install","payload":"upper"}`,
		`{"id":"west-1/team_a/guides/Go/Install","payload":"lower"}`,
	}, "\n"))

	if got := lookup(t, index, "WEST-1/team_a/Guides/Go/Install"); got.Payload != "upper" {
		t.Fatalf("upper nested key lookup = %+v", got)
	}
	if got := lookup(t, index, "west-1/TEAM_A/guides/Go/Install"); got.Payload != "lower" {
		t.Fatalf("lower nested key lookup = %+v", got)
	}
	if _, ok := index.Lookup("west-1/team_a/guides/go/install"); ok {
		t.Fatal("opaque nested key was case-folded")
	}
}

func TestDisplayIDsArePreservedOrderedAndDefensive(t *testing.T) {
	index := loadText(t, strings.Join([]string{
		`{"id":"z/NS/Key","payload":"z"}`,
		`{"id":"A/ns/beta","payload":"b"}`,
		`{"id":"a/NS/Alpha","payload":"a"}`,
	}, "\n"))

	want := []string{"a/NS/Alpha", "A/ns/beta", "z/NS/Key"}
	first := index.IDs()
	if !reflect.DeepEqual(first, want) {
		t.Fatalf("IDs = %#v, want %#v", first, want)
	}
	first[0] = "corrupted/by/caller"
	if again := index.IDs(); !reflect.DeepEqual(again, want) {
		t.Fatalf("IDs leaked internal storage: %#v", again)
	}

	if got := lookup(t, index, "A/ns/Alpha"); got.ID != "a/NS/Alpha" {
		t.Fatalf("display ID was normalized: %+v", got)
	}
}

func TestLoadAggregatesAndSortsEveryTrueCollision(t *testing.T) {
	text := strings.Join([]string{
		`{"id":"Z/Team/Same","payload":"z1"}`,
		`{"id":"a/Name/Thing","payload":"a1"}`,
		`{"id":"z/team/same","payload":"not a collision: key differs"}`,
		`{"id":"A/name/Thing","payload":"a2"}`,
		`{"id":"z/TEAM/Same","payload":"z2"}`,
		`{"id":"A/NAME/Thing","payload":"a3"}`,
		`{"id":"Z/team/Other","payload":"other"}`,
	}, "\n")

	index, err := catalog.Load(strings.NewReader(text))
	if index != nil {
		t.Fatalf("Load returned partial catalog: %#v", index)
	}
	if !errors.Is(err, catalog.ErrCollision) {
		t.Fatalf("Load error = %v, want ErrCollision identity", err)
	}
	var collisionErr *catalog.CollisionError
	if !errors.As(err, &collisionErr) {
		t.Fatalf("Load error type = %T, want *CollisionError", err)
	}

	want := []catalog.Collision{
		{
			LookupKey: "a/name/Thing",
			IDs:       []string{"A/NAME/Thing", "A/name/Thing", "a/Name/Thing"},
		},
		{
			LookupKey: "z/team/Same",
			IDs:       []string{"Z/Team/Same", "z/TEAM/Same"},
		},
	}
	if !reflect.DeepEqual(collisionErr.Collisions, want) {
		t.Fatalf("collisions = %#v, want %#v", collisionErr.Collisions, want)
	}
}

func TestExactDuplicateIsReportedAsCorruptInput(t *testing.T) {
	index, err := catalog.Load(strings.NewReader(strings.Join([]string{
		`{"id":"Cell/Space/Key","payload":"first"}`,
		`{"id":"Cell/Space/Key","payload":"second"}`,
	}, "\n")))
	if index != nil || !errors.Is(err, catalog.ErrCollision) {
		t.Fatalf("Load = (%#v, %v), want collision", index, err)
	}
	var collisionErr *catalog.CollisionError
	if !errors.As(err, &collisionErr) {
		t.Fatalf("error type = %T", err)
	}
	want := []catalog.Collision{{
		LookupKey: "cell/space/Key",
		IDs:       []string{"Cell/Space/Key", "Cell/Space/Key"},
	}}
	if !reflect.DeepEqual(collisionErr.Collisions, want) {
		t.Fatalf("collisions = %#v, want %#v", collisionErr.Collisions, want)
	}
}

func TestIdentifierValidationFollowsRoutingBoundary(t *testing.T) {
	bad := []string{
		"",
		"cell",
		"cell/namespace",
		"/namespace/key",
		"cell//key",
		"cell/namespace/",
		"cell name/namespace/key",
		"cell/namespace!/key",
		" cél/namespace/key",
	}
	for _, id := range bad {
		t.Run(id, func(t *testing.T) {
			text := `{"id":` + strconvQuote(id) + `,"payload":"bad"}` + "\n"
			index, err := catalog.Load(strings.NewReader(text))
			if index != nil || err == nil {
				t.Fatalf("Load(%q) = (%#v, %v), want error", id, index, err)
			}
			if !strings.Contains(err.Error(), "catalog line 1") ||
				!strings.Contains(err.Error(), "invalid identifier") {
				t.Fatalf("unhelpful invalid-ID error: %v", err)
			}
		})
	}

	index := loadText(t, `{"id":"Cell/Space/key with ! punctuation/and/slashes"}`+"\n")
	if got := lookup(t, index, "cell/SPACE/key with ! punctuation/and/slashes"); got.ID !=
		"Cell/Space/key with ! punctuation/and/slashes" {
		t.Fatalf("opaque key rejected or changed: %+v", got)
	}

	for _, id := range bad {
		if _, ok := index.Lookup(id); ok {
			t.Fatalf("invalid Lookup(%q) matched", id)
		}
	}
}

func TestMalformedJSONKeepsLineContextAndBlankLinesAreAllowed(t *testing.T) {
	index, err := catalog.Load(strings.NewReader(
		"\n" + `{"id":"ok/ns/Key"}` + "\n" + `{"id":` + "\n",
	))
	if index != nil || err == nil {
		t.Fatalf("Load malformed = (%#v, %v)", index, err)
	}
	if !strings.Contains(err.Error(), "catalog line 3") ||
		!strings.Contains(err.Error(), "decode record") {
		t.Fatalf("malformed JSON error lacks context: %v", err)
	}
}

func TestASCIIRoutingFoldDoesNotRewriteOpaqueUnicode(t *testing.T) {
	index := loadText(t, strings.Join([]string{
		`{"id":"CELL/NS/Σ","payload":"capital sigma"}`,
		`{"id":"cell/ns/σ","payload":"small sigma"}`,
		`{"id":"cell/ns/ς","payload":"final sigma"}`,
	}, "\n"))
	for id, payload := range map[string]string{
		"cell/ns/Σ": "capital sigma",
		"CELL/NS/σ": "small sigma",
		"Cell/Ns/ς": "final sigma",
	} {
		if got := lookup(t, index, id); got.Payload != payload {
			t.Fatalf("Lookup(%q) = %+v", id, got)
		}
	}
}

func strconvQuote(value string) string {
	encoded := strings.Builder{}
	encoded.WriteByte('"')
	for _, r := range value {
		switch r {
		case '\\', '"':
			encoded.WriteByte('\\')
			encoded.WriteRune(r)
		case '\n':
			encoded.WriteString(`\n`)
		default:
			encoded.WriteRune(r)
		}
	}
	encoded.WriteByte('"')
	return encoded.String()
}
