// Package wire verifies that a recorded request had the exact shape it was
// supposed to have.
//
// "Exact" is meant strictly, and in both directions. A query parameter the
// expectation does not list must not have been sent — not sent empty, not
// sent as the zero value, not sent at all. A JSON body key the expectation
// does not list must be absent from the object. This is the whole point of the
// package: the interesting bugs in a client of an API whose fields are almost
// all optional are the fields it sends when it should have stayed quiet.
package wire

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"reflect"
	"sort"
	"strings"

	"vcfauto/mock"
)

// Expectation is the exact shape a request must have had.
type Expectation struct {
	// Operation is the contract operation ID the request must have matched.
	Operation string

	Method string

	// Path is the concrete request path, with path parameters substituted.
	Path string

	// Query is the complete set of query parameters the request must have
	// carried. A parameter absent from Query must be absent from the
	// request. A nil Query means the request must have carried no query
	// string at all.
	Query url.Values

	// Header lists headers that must be present with exactly these values.
	// Headers not listed are ignored, since the transport adds its own.
	Header map[string]string

	// AbsentHeaders lists headers that must not be present.
	AbsentHeaders []string

	// JSONBody, when non-empty, is the JSON object the body must have been,
	// compared by decoded value rather than by byte equality. A key absent
	// from JSONBody must be absent from the body.
	JSONBody string

	// FormBody, when non-nil, is the complete set of form-encoded values
	// the body must have carried.
	FormBody url.Values

	// NoBody asserts the request carried an empty body.
	NoBody bool

	// Status is the status the server replied with. Zero means "don't
	// check".
	Status int
}

// problems accumulates every discrepancy so that one call reports all of them.
type problems []string

func (p *problems) addf(format string, args ...any) {
	*p = append(*p, fmt.Sprintf(format, args...))
}

func (p problems) err() error {
	if len(p) == 0 {
		return nil
	}
	if len(p) == 1 {
		return errors.New("wire: " + p[0])
	}
	return fmt.Errorf("wire: %d discrepancies:\n  - %s", len(p), strings.Join(p, "\n  - "))
}

// Check reports how got departs from want, or nil if it matches exactly.
//
// The error names every discrepancy it found, not just the first, and
// distinguishes "parameter absent" from "parameter present but empty" — those
// are the two cases this package exists to tell apart.
func Check(got mock.RecordedRequest, want Expectation) error {
	var p problems

	if want.Operation != "" && got.Operation != want.Operation {
		p.addf("operation: got %q, want %q", got.Operation, want.Operation)
	}
	if want.Method != "" && got.Method != want.Method {
		p.addf("method: got %q, want %q", got.Method, want.Method)
	}
	if want.Path != "" && got.Path != want.Path {
		p.addf("path: got %q, want %q", got.Path, want.Path)
	}
	if want.Status != 0 && got.Status != want.Status {
		p.addf("status: got %d, want %d", got.Status, want.Status)
	}

	checkValues(&p, "query parameter", got.Query, want.Query)

	for name, wantVal := range want.Header {
		gotVal := got.Header.Get(name)
		if _, present := got.Header[canonical(name)]; !present {
			p.addf("header %q is absent; want %q", name, wantVal)
			continue
		}
		if gotVal != wantVal {
			p.addf("header %q: got %q, want %q", name, gotVal, wantVal)
		}
	}
	for _, name := range want.AbsentHeaders {
		if _, present := got.Header[canonical(name)]; present {
			p.addf("header %q was sent as %q; it must not be sent at all", name, got.Header.Get(name))
		}
	}

	if want.NoBody && len(got.Body) != 0 {
		p.addf("body: got %q, want no body at all", string(got.Body))
	}

	if want.JSONBody != "" {
		checkJSON(&p, got.Body, want.JSONBody)
	}
	if want.FormBody != nil {
		gotForm, err := url.ParseQuery(string(got.Body))
		if err != nil {
			p.addf("body %q is not form-encoded: %v", string(got.Body), err)
		} else {
			checkValues(&p, "form field", gotForm, want.FormBody)
		}
	}

	return p.err()
}

func canonical(name string) string {
	return http.CanonicalHeaderKey(name)
}

// checkValues compares two url.Values exactly, reporting absent and
// present-but-unwanted separately.
func checkValues(p *problems, what string, got, want url.Values) {
	for _, name := range sortedKeys(want) {
		gotVals, present := got[name]
		if !present {
			p.addf("%s %q is absent; want %q. An absent %s is not the same as one sent empty",
				what, name, strings.Join(want[name], ","), what)
			continue
		}
		if !reflect.DeepEqual(gotVals, want[name]) {
			p.addf("%s %q: got %q, want %q", what, name, gotVals, want[name])
		}
	}
	for _, name := range sortedKeys(got) {
		if _, expected := want[name]; expected {
			continue
		}
		vals := got[name]
		if len(vals) == 1 && vals[0] == "" {
			p.addf("%s %q was sent empty; it must be omitted entirely rather than sent with an empty value", what, name)
			continue
		}
		p.addf("%s %q was sent as %q; it must not be sent at all", what, name, vals)
	}
}

func sortedKeys(v url.Values) []string {
	out := make([]string, 0, len(v))
	for k := range v {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// checkJSON compares two JSON objects by decoded value, reporting each key
// that is missing, unwanted or different.
func checkJSON(p *problems, gotRaw []byte, wantRaw string) {
	var gotObj, wantObj map[string]any
	if err := json.Unmarshal([]byte(wantRaw), &wantObj); err != nil {
		p.addf("expected body %q is not a JSON object: %v", wantRaw, err)
		return
	}
	if wantObj == nil {
		p.addf("expected body %q is not a JSON object", wantRaw)
		return
	}
	if len(gotRaw) == 0 {
		p.addf("body is empty; want the JSON object %s", wantRaw)
		return
	}
	if err := json.Unmarshal(gotRaw, &gotObj); err != nil {
		p.addf("body %q is not a JSON object: %v", string(gotRaw), err)
		return
	}
	if gotObj == nil {
		p.addf("body %q is not a JSON object", string(gotRaw))
		return
	}

	for _, key := range sortedObjKeys(wantObj) {
		gotVal, present := gotObj[key]
		if !present {
			p.addf("body key %q is absent; want %v", key, wantObj[key])
			continue
		}
		if !reflect.DeepEqual(gotVal, wantObj[key]) {
			p.addf("body key %q: got %v, want %v", key, gotVal, wantObj[key])
		}
	}
	for _, key := range sortedObjKeys(gotObj) {
		if _, expected := wantObj[key]; expected {
			continue
		}
		switch v := gotObj[key].(type) {
		case nil:
			p.addf("body key %q was sent as null; an unset optional field must be omitted from the object, not sent null", key)
		case string:
			if v == "" {
				p.addf("body key %q was sent as an empty string; an unset optional field must be omitted from the object, not sent empty", key)
				continue
			}
			p.addf("body key %q was sent as %q; it must not be sent at all", key, v)
		default:
			p.addf("body key %q was sent as %v; it must not be sent at all", key, v)
		}
	}
}

func sortedObjKeys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// CheckAll matches a recorded sequence against an expected sequence, in order,
// reporting a length mismatch or the first index that differs.
func CheckAll(got []mock.RecordedRequest, want []Expectation) error {
	if len(got) != len(want) {
		return fmt.Errorf("wire: recorded %d requests, want %d", len(got), len(want))
	}
	for i := range want {
		if err := Check(got[i], want[i]); err != nil {
			return fmt.Errorf("wire: request %d: %w", i, err)
		}
	}
	return nil
}
