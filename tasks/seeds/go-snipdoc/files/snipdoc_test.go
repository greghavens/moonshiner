package snipdoc

import (
	"reflect"
	"strings"
	"testing"
)

func TestRenderSubstitutesEverywhere(t *testing.T) {
	got := Render(Page{Name: "build", Summary: "compile every doc page"})
	want := "# build\n" +
		"\n" +
		"build — compile every doc page\n" +
		"\n" +
		"Regenerate the command docs with `snipdoc build` after editing\n" +
		"any of the .cmd files, or preview this page with `snipdoc show build`.\n"
	if got != want {
		t.Fatalf("Render mismatch:\ngot:\n%q\nwant:\n%q", got, want)
	}
}

func TestRenderKeepsCodeSpans(t *testing.T) {
	got := Render(Page{Name: "show", Summary: "preview one page"})
	for _, span := range []string{"`snipdoc build`", "`snipdoc show show`."} {
		if !strings.Contains(got, span) {
			t.Errorf("rendered page must keep the code span %s verbatim; got:\n%s", span, got)
		}
	}
}

func TestIndexSortsNames(t *testing.T) {
	pages := []Page{
		{Name: "show", Summary: "preview one page"},
		{Name: "build", Summary: "compile every doc page"},
		{Name: "list", Summary: "list all pages"},
	}
	got := Index(pages)
	want := []string{"build", "list", "show"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Index = %v, want %v", got, want)
	}
}

func TestTOCPairsNameWithSummary(t *testing.T) {
	pages := []Page{
		{Name: "show", Summary: "preview one page"},
		{Name: "build", Summary: "compile every doc page"},
	}
	got := TOC(pages)
	want := []string{
		"- build: compile every doc page",
		"- show: preview one page",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("TOC = %v, want %v", got, want)
	}
}

func TestEmptyDocSet(t *testing.T) {
	if got := Index(nil); len(got) != 0 {
		t.Fatalf("Index(nil) = %v, want empty", got)
	}
	if got := TOC(nil); len(got) != 0 {
		t.Fatalf("TOC(nil) = %v, want empty", got)
	}
}
