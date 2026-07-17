package snipdoc

import (
	"sort"
	"strings"
)

// usageTemplate is the skeleton for every generated doc page. NAME and
// SUMMARY get substituted per command. The backticked phrases are
// markdown code spans and must land in the rendered page verbatim —
// the wiki styles them as inline code.
const usageTemplate = `# NAME

NAME — SUMMARY

Regenerate the command docs with `snipdoc build` after editing
any of the .cmd files, or preview this page with `snipdoc show NAME`.
`

// Page is one command's entry in the doc set.
type Page struct {
	Name    string
	Summary string
}

// Render fills the page skeleton for one command.
func Render(p Page) string {
	out := strings.ReplaceAll(usageTemplate, "SUMMARY", p.Summary)
	return strings.ReplaceAll(out, "NAME", p.Name)
}

// Index lists every page name in alphabetical order.
func Index(pages []Page) []string {
	names := make([]string, 0, len(pages))
	for _, p := range pages {
		names = append(names, p.Name)
	}
	sort.Strings(names)
	return names
}

// TOC renders the contents block for the doc set's landing page.
func TOC(pages []Page) []string {
	byName := make(map[string]string, len(pages))
	for _, p := range pages {
		byName[p.Name] = p.Summary
	}
	lines := make([]string, 0, len(pages))
	for _, name := range Index(pages) {
		lines = append(lines, "- "+name+": "+byName[name])
	}
	return lines
}
