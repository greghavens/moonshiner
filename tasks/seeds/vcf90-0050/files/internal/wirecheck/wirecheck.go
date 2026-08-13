// Package wirecheck verifies recorded requests against the request wire shape
// pinned in docs/contract.json.
//
// The checks are exact. A recorded deploy request passes only if its method,
// path, query string, headers and JSON body are precisely what the 9.0.0.0
// specification prescribes — which includes carrying no property the caller
// did not set. An unset optional property is absent from the body; it is not
// "", not {}, not [] and not null.
//
// Every function returns the list of violations it found, empty when the
// request is on contract, so a failing test can report all of them at once.
package wirecheck

import (
	"encoding/json"
	"fmt"
	"net/url"
	"reflect"
	"regexp"
	"sort"
	"strings"

	"vcfovf/internal/mockvc"
)

const (
	deployPathPrefix = "/api/vcenter/ovf/library-item/"
	deployQuery      = "action=deploy"
	listVMsPath      = "/api/vcenter/vm"
	sessionHeader    = "vmware-api-session-id"
	tokenHeader      = "Client-Token"
)

var uuidRe = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

// forbidden lists body properties that must never appear. subnet_mappings and
// tag_params belong to the 9.1.0.0 revision of vcenter.yaml; the camelCase
// spellings belong to no revision of the body at all.
var forbidden = []string{
	"subnet_mappings", "tag_params", "client_token", "clientToken",
	"resourcePoolId", "hostId", "folderId", "acceptAllEula",
	"deploymentSpec", "networkMappings", "storageProvisioning",
	"storageProfileId", "defaultDatastoreId",
}

// Deploy is the expected wire shape of one Vcenter.Ovf.LibraryItem_deploy request.
type Deploy struct {
	// SessionID is the value the vmware-api-session-id header must carry.
	SessionID string
	// LibraryItemID is the last path segment.
	LibraryItemID string
	// Token, when non-empty, is the exact Client-Token expected. When empty,
	// the header must still be present and hold a well-formed UUID.
	Token string
	// Target is the complete expected value of the body's target property:
	// every key that must be present, and no other key may be.
	Target map[string]any
	// Spec is the complete expected value of the body's deployment_spec property.
	Spec map[string]any
}

// DeployRequest checks one recorded deploy request against want.
func DeployRequest(req mockvc.Request, want Deploy) []string {
	var v []string
	add := func(format string, args ...any) { v = append(v, fmt.Sprintf(format, args...)) }

	if req.Op != mockvc.OpDeploy {
		add("request did not route to %s (routed to %q)", mockvc.OpDeploy, req.Op)
	}
	if req.Method != "POST" {
		add("method is %q, want POST", req.Method)
	}
	if wantPath := deployPathPrefix + want.LibraryItemID; req.Path != wantPath {
		add("path is %q, want %q", req.Path, wantPath)
	}
	if req.RawQuery != deployQuery {
		add("query string is %q, want exactly %q", req.RawQuery, deployQuery)
	}
	if got := req.Header.Get("Content-Type"); got != "application/json" {
		add("Content-Type is %q, want %q", got, "application/json")
	}
	if got := req.Header.Get(sessionHeader); got != want.SessionID {
		add("%s header is %q, want %q", sessionHeader, got, want.SessionID)
	}

	token := req.Header.Get(tokenHeader)
	switch {
	case token == "":
		add("%s header is absent; a retry-safe deploy always carries one", tokenHeader)
	case !uuidRe.MatchString(token):
		add("%s %q does not conform to the UUID format (lowercase 8-4-4-4-12 hex)", tokenHeader, token)
	case want.Token != "" && token != want.Token:
		add("%s is %q, want %q", tokenHeader, token, want.Token)
	}

	v = append(v, checkBody(req.Body, want)...)
	return v
}

func checkBody(body []byte, want Deploy) []string {
	var v []string
	add := func(format string, args ...any) { v = append(v, fmt.Sprintf(format, args...)) }

	var top map[string]any
	if err := json.Unmarshal(body, &top); err != nil {
		return []string{fmt.Sprintf("body is not a JSON object: %v (body=%s)", err, truncate(body))}
	}
	if got := keys(top); !reflect.DeepEqual(got, []string{"deployment_spec", "target"}) {
		add("body properties are %v, want exactly [deployment_spec target]", got)
	}
	for _, f := range forbidden {
		if strings.Contains(string(body), `"`+f+`"`) {
			add("body contains forbidden property %q (body=%s)", f, truncate(body))
		}
	}

	v = append(v, checkObject(top, "target", want.Target)...)
	v = append(v, checkObject(top, "deployment_spec", want.Spec)...)
	return v
}

func checkObject(top map[string]any, name string, want map[string]any) []string {
	var v []string
	add := func(format string, args ...any) { v = append(v, fmt.Sprintf(format, args...)) }

	raw, ok := top[name]
	if !ok {
		return []string{fmt.Sprintf("body is missing the %q property", name)}
	}
	got, ok := raw.(map[string]any)
	if !ok {
		return []string{fmt.Sprintf("%s is %T, want a JSON object", name, raw)}
	}
	wantNorm := normalize(want)

	gk, wk := keys(got), keys(wantNorm)
	if !reflect.DeepEqual(gk, wk) {
		for _, k := range gk {
			if _, ok := wantNorm[k]; !ok {
				add("%s carries %q, which was not set; an unset optional property is omitted, not sent as %s",
					name, k, render(got[k]))
			}
		}
		for _, k := range wk {
			if _, ok := got[k]; !ok {
				add("%s is missing %q, which must be sent as %s", name, k, render(wantNorm[k]))
			}
		}
	}
	for _, k := range wk {
		g, ok := got[k]
		if !ok {
			continue
		}
		if !reflect.DeepEqual(g, wantNorm[k]) {
			add("%s.%s is %s, want %s", name, k, render(g), render(wantNorm[k]))
		}
	}
	// Belt and braces: nothing empty ever belongs on the wire.
	for k, g := range got {
		switch t := g.(type) {
		case nil:
			add("%s.%s is null; omit it instead", name, k)
		case string:
			if t == "" {
				add("%s.%s is an empty string; omit it instead", name, k)
			}
		case map[string]any:
			if len(t) == 0 {
				add("%s.%s is an empty object; omit it instead", name, k)
			}
		case []any:
			if len(t) == 0 {
				add("%s.%s is an empty array; omit it instead", name, k)
			}
		}
	}
	return v
}

// SameToken returns the single Client-Token shared by every request, and the
// violations found. Retries of one logical deploy must all carry the identical
// token; that is what makes the call safe to repeat.
func SameToken(reqs []mockvc.Request) (string, []string) {
	if len(reqs) == 0 {
		return "", []string{"no deploy requests were recorded"}
	}
	seen := map[string]int{}
	for _, r := range reqs {
		seen[r.Header.Get(tokenHeader)]++
	}
	if len(seen) == 1 {
		for tok := range seen {
			return tok, nil
		}
	}
	toks := make([]string, 0, len(seen))
	for tok := range seen {
		toks = append(toks, fmt.Sprintf("%q x%d", tok, seen[tok]))
	}
	sort.Strings(toks)
	return "", []string{fmt.Sprintf(
		"the %d deploy attempts used %d different %s values (%s); a retry must re-send the original token",
		len(reqs), len(seen), tokenHeader, strings.Join(toks, ", "))}
}

// ListVMsRequest checks one recorded Vcenter.VM_list request. names is the
// filter the caller asked for; nil or empty means no names parameter at all.
func ListVMsRequest(req mockvc.Request, sessionID string, names []string) []string {
	var v []string
	add := func(format string, args ...any) { v = append(v, fmt.Sprintf(format, args...)) }

	if req.Op != mockvc.OpListVMs {
		add("request did not route to %s (routed to %q)", mockvc.OpListVMs, req.Op)
	}
	if req.Method != "GET" {
		add("method is %q, want GET", req.Method)
	}
	if req.Path != listVMsPath {
		add("path is %q, want %q", req.Path, listVMsPath)
	}
	if got := req.Header.Get(sessionHeader); got != sessionID {
		add("%s header is %q, want %q", sessionHeader, got, sessionID)
	}
	if len(req.Body) != 0 {
		add("a GET carried a %d byte body", len(req.Body))
	}

	q, err := url.ParseQuery(req.RawQuery)
	if err != nil {
		return append(v, fmt.Sprintf("query string %q does not parse: %v", req.RawQuery, err))
	}
	for k := range q {
		if k != "names" {
			add("query carries %q; the only parameter this client sends is names", k)
		}
	}
	got := append([]string(nil), q["names"]...)
	wantNames := append([]string(nil), names...)
	sort.Strings(got)
	sort.Strings(wantNames)
	if len(got) == 0 && len(wantNames) == 0 {
		return v
	}
	if !reflect.DeepEqual(got, wantNames) {
		add("names filter is %v, want %v sent as repeated names= pairs (style form, explode true)", got, wantNames)
	}
	return v
}

func normalize(m map[string]any) map[string]any {
	if m == nil {
		return map[string]any{}
	}
	b, err := json.Marshal(m)
	if err != nil {
		panic("wirecheck: " + err.Error())
	}
	var out map[string]any
	if err := json.Unmarshal(b, &out); err != nil {
		panic("wirecheck: " + err.Error())
	}
	return out
}

func keys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func render(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprintf("%v", v)
	}
	return string(b)
}

func truncate(b []byte) string {
	if len(b) > 400 {
		return string(b[:400]) + "..."
	}
	return string(b)
}
