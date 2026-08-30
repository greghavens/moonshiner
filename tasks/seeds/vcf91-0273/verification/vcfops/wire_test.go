package vcfops

import (
	"encoding/json"
	"sort"
	"testing"
)

// keysOf returns the sorted property names actually present in a JSON object.
func keysOf(obj map[string]json.RawMessage) []string {
	out := make([]string, 0, len(obj))
	for k := range obj {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// TestAcquireTokenRequestWireShape checks the encoded username-password body
// property by property. The spec marks username and password required and
// authSource optional; docs/contract.json requires an unset optional field to
// be absent from the object rather than sent as an empty value.
func TestAcquireTokenRequestWireShape(t *testing.T) {
	c := loadContract(t)
	op := c.operation(t, "acquireToken")

	tests := []struct {
		name       string
		creds      Credentials
		userSource string   // authSource the mock requires for this user
		wantKeys   []string // exact property set of the encoded body
		wantValues map[string]string
	}{
		{
			name:       "authSource unset is omitted",
			creds:      Credentials{Username: "svc-ops", Password: "pw-v1"},
			userSource: "",
			wantKeys:   []string{"password", "username"},
			wantValues: map[string]string{"username": "svc-ops", "password": "pw-v1"},
		},
		{
			name:       "authSource set is sent",
			creds:      Credentials{Username: "svc-ops", Password: "pw-v1", AuthSource: "Imported LDAP Server"},
			userSource: "Imported LDAP Server",
			wantKeys:   []string{"authSource", "password", "username"},
			wantValues: map[string]string{
				"username":   "svc-ops",
				"password":   "pw-v1",
				"authSource": "Imported LDAP Server",
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			m := newMockServer(t)
			m.addUser(tc.creds.Username, tc.creds.Password, tc.userSource)

			client := NewClient(m.URL(), m.srv.Client())
			ctx, cancel := waitCtx(t)
			defer cancel()

			if err := client.Authenticate(ctx, tc.creds); err != nil {
				t.Fatalf("Authenticate: %v (request log: %s)", err, m.summary())
			}

			got := m.recordsFor("acquireToken")
			if len(got) != 1 {
				t.Fatalf("got %d acquireToken requests, want 1: %s", len(got), m.summary())
			}
			rec := got[0]

			if rec.Method != op.Method {
				t.Errorf("method = %q, contract = %q", rec.Method, op.Method)
			}
			if want := op.fullPath(c.BasePath); rec.Path != want {
				t.Errorf("path = %q, contract = %q", rec.Path, want)
			}
			if rec.RawQuery != "" {
				t.Errorf("query = %q, contract defines no query parameters", rec.RawQuery)
			}
			if got, want := rec.Header.Get("Content-Type"), op.Request.ContentType; got != want {
				t.Errorf("Content-Type = %q, contract = %q", got, want)
			}
			// The spec gives acquireToken an empty security list.
			if got := rec.Header.Get(c.Security.Name); got != "" {
				t.Errorf("acquireToken sent %s: %q; the operation is unauthenticated", c.Security.Name, got)
			}

			body := rec.jsonBody(t)
			if gotKeys := keysOf(body); !equalStrings(gotKeys, tc.wantKeys) {
				t.Errorf("encoded properties = %v, want exactly %v (body=%s)", gotKeys, tc.wantKeys, rec.Body)
			}
			if _, present := body["authSource"]; present && tc.creds.AuthSource == "" {
				t.Errorf("authSource is unset but was still encoded as %s; the contract requires unset optional fields to be omitted", body["authSource"])
			}
			for prop, want := range tc.wantValues {
				raw, ok := body[prop]
				if !ok {
					t.Errorf("required property %q missing from body %s", prop, rec.Body)
					continue
				}
				var got string
				if err := json.Unmarshal(raw, &got); err != nil {
					t.Errorf("property %q is not a JSON string: %s", prop, raw)
					continue
				}
				if got != want {
					t.Errorf("property %q = %q, want %q", prop, got, want)
				}
			}
		})
	}
}

// TestWireShapeAcrossRotation drives one full credential rotation and checks
// every request the client made, in order, against the contract.
func TestWireShapeAcrossRotation(t *testing.T) {
	c := loadContract(t)

	m := newMockServer(t)
	m.addUser("svc-ops", "pw-v1", "")

	client := NewClient(m.URL(), m.srv.Client())
	ctx, cancel := waitCtx(t)
	defer cancel()

	if err := client.Authenticate(ctx, Credentials{Username: "svc-ops", Password: "pw-v1"}); err != nil {
		t.Fatalf("Authenticate: %v", err)
	}
	if _, err := client.CurrentUser(ctx); err != nil {
		t.Fatalf("CurrentUser before rotation: %v", err)
	}

	m.addUser("svc-ops", "pw-v2", "")
	if err := client.Rotate(ctx, Credentials{Username: "svc-ops", Password: "pw-v2"}); err != nil {
		t.Fatalf("Rotate: %v (request log: %s)", err, m.summary())
	}
	if _, err := client.CurrentUser(ctx); err != nil {
		t.Fatalf("CurrentUser after rotation: %v", err)
	}
	if err := client.Close(ctx); err != nil {
		t.Fatalf("Close: %v", err)
	}

	issued := m.issuedTokens()
	if len(issued) != 2 {
		t.Fatalf("mock issued %d tokens, want 2 (one per credential generation): %s", len(issued), m.summary())
	}
	oldToken, newToken := issued[0], issued[1]

	// tokenIndex: -1 means no Authorization header expected.
	want := []struct {
		operationID string
		tokenIndex  int
		bodyKeys    []string // nil means no request body
	}{
		{"acquireToken", -1, []string{"password", "username"}},
		{"getCurrentUser", 0, nil},
		{"acquireToken", -1, []string{"password", "username"}},
		{"releaseToken", 0, nil},
		{"getCurrentUser", 1, nil},
		{"releaseToken", 1, nil},
	}

	got := m.records()
	if len(got) != len(want) {
		t.Fatalf("client made %d requests, want %d: %s", len(got), len(want), m.summary())
	}

	tokens := []string{oldToken, newToken}
	for i, w := range want {
		rec := got[i]
		t.Run(w.operationID, func(t *testing.T) {
			op := c.operation(t, w.operationID)

			if rec.OperationID != w.operationID {
				t.Fatalf("request #%d was %s, want %s: %s", i+1, rec.OperationID, w.operationID, m.summary())
			}
			if rec.Method != op.Method {
				t.Errorf("request #%d method = %q, contract = %q", i+1, rec.Method, op.Method)
			}
			if p := op.fullPath(c.BasePath); rec.Path != p {
				t.Errorf("request #%d path = %q, contract = %q", i+1, rec.Path, p)
			}
			if rec.Status != op.Success.Status {
				t.Errorf("request #%d status = %d, contract success = %d", i+1, rec.Status, op.Success.Status)
			}

			auth := rec.Header.Get(c.Security.Name)
			if w.tokenIndex < 0 {
				if auth != "" {
					t.Errorf("request #%d sent %s %q on an unauthenticated operation", i+1, c.Security.Name, auth)
				}
			} else {
				wantAuth := c.Security.TokenPrefix + tokens[w.tokenIndex]
				if auth != wantAuth {
					t.Errorf("request #%d %s = %q, want %q", i+1, c.Security.Name, auth, wantAuth)
				}
			}

			if w.bodyKeys == nil {
				if len(rec.Body) != 0 {
					t.Errorf("request #%d carried a body %q; the contract defines none", i+1, rec.Body)
				}
				return
			}
			if gotKeys := keysOf(rec.jsonBody(t)); !equalStrings(gotKeys, w.bodyKeys) {
				t.Errorf("request #%d properties = %v, want exactly %v (body=%s)", i+1, gotKeys, w.bodyKeys, rec.Body)
			}
		})
	}
}

// TestOnlyContractOperationsAreCalled fails if the client reaches a route the
// contract does not name. The mock serves nothing else.
func TestOnlyContractOperationsAreCalled(t *testing.T) {
	c := loadContract(t)
	named := map[string]bool{}
	for _, op := range c.Operations {
		named[op.OperationID] = true
	}

	m := newMockServer(t)
	m.addUser("svc-ops", "pw-v1", "")

	client := NewClient(m.URL(), m.srv.Client())
	ctx, cancel := waitCtx(t)
	defer cancel()

	if err := client.Authenticate(ctx, Credentials{Username: "svc-ops", Password: "pw-v1"}); err != nil {
		t.Fatalf("Authenticate: %v", err)
	}
	if _, err := client.CurrentUser(ctx); err != nil {
		t.Fatalf("CurrentUser: %v", err)
	}
	m.addUser("svc-ops", "pw-v2", "")
	if err := client.Rotate(ctx, Credentials{Username: "svc-ops", Password: "pw-v2"}); err != nil {
		t.Fatalf("Rotate: %v", err)
	}
	if err := client.Close(ctx); err != nil {
		t.Fatalf("Close: %v", err)
	}

	for _, rec := range m.records() {
		if !named[rec.OperationID] {
			t.Errorf("client called %s %s, which docs/contract.json does not name", rec.Method, rec.Path)
		}
	}
}
