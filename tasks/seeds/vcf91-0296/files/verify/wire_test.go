// This file is part of the protected harness. Do not edit it.
package verify

import (
	"net/http"
	"strconv"
	"strings"
	"testing"

	"vcfopsnetinv/internal/opsnet"
	"vcfopsnetinv/internal/opsnetmock"
)

const (
	authTokenPath = opsnetmock.BasePath + "/auth/token"
	appsPath      = opsnetmock.BasePath + "/groups/applications"
	appPathPrefix = appsPath + "/"
)

// assertWireShape checks every recorded request against docs/contract.json.
// It is applied to every scenario, so the "omit unset optional fields" rule is
// enforced uniformly rather than only where a scenario happens to look.
func assertWireShape(t *testing.T, cfg opsnet.Config, srv *opsnetmock.Server) {
	t.Helper()
	log := srv.Log()
	if len(log) == 0 {
		t.Fatal("the client made no requests at all")
	}

	for _, e := range log {
		if e.OperationID == "" {
			t.Errorf("request #%d %s %s matches no operation in the contract (mock answered %d: %s)",
				e.Seq, e.Method, e.Path, e.Status, strings.TrimSpace(string(e.ResponseBody)))
			continue
		}
		switch e.Status {
		case http.StatusBadRequest, http.StatusNotFound, http.StatusMethodNotAllowed, http.StatusServiceUnavailable:
			t.Errorf("request #%d (%s %s %s?%s) was rejected by the contract mock with %d: %s",
				e.Seq, e.OperationID, e.Method, e.Path, e.Query.Encode(), e.Status,
				strings.TrimSpace(string(e.ResponseBody)))
		}
	}

	assertCreateShape(t, cfg, log)
	assertDeleteShape(t, srv, log)
	assertListShape(t, cfg, log)
	assertDetailShape(t, cfg, log)
}

// assertCreateShape covers operationId "create" (POST /auth/token).
func assertCreateShape(t *testing.T, cfg opsnet.Config, log []opsnetmock.Entry) {
	t.Helper()
	entries := entriesFor(log, "create")
	if len(entries) == 0 {
		t.Error(`operationId "create" was never called; the client must obtain an auth token`)
		return
	}
	for _, e := range entries {
		what := "create request #" + itoa(e.Seq)
		if e.Method != http.MethodPost {
			t.Errorf("%s: method %s, want POST", what, e.Method)
		}
		if e.Path != authTokenPath {
			t.Errorf("%s: path %q, want %q", what, e.Path, authTokenPath)
		}
		// The operation declares "security: []" in the specification.
		if e.Authorization != "" {
			t.Errorf("%s: sent Authorization %q; operationId \"create\" declares security: [] and must not carry the header",
				what, e.Authorization)
		}
		if ct := e.ContentType; !strings.HasPrefix(ct, "application/json") {
			t.Errorf("%s: Content-Type %q, want application/json", what, ct)
		}
		assertKeys(t, what+" query", e.QueryKeys(), nil)

		body := decodeObject(t, what+" UserCredential", e.Body)
		assertKeys(t, what+" UserCredential", sortedKeys(body), wantCreateBodyKeys(cfg.Credentials))
		assertNoEmptyStrings(t, what+" UserCredential", body)
		assertStringProp(t, what+" UserCredential", body, "username", cfg.Credentials.Username)
		assertStringProp(t, what+" UserCredential", body, "password", cfg.Credentials.Password)

		if raw, ok := body["domain"]; ok {
			domain := decodeObject(t, what+" Domain", raw)
			assertKeys(t, what+" Domain", sortedKeys(domain), wantDomainKeys(cfg.Credentials))
			assertNoEmptyStrings(t, what+" Domain", domain)
			assertStringProp(t, what+" Domain", domain, "domain_type", cfg.Credentials.DomainType)
			if cfg.Credentials.DomainValue != "" {
				assertStringProp(t, what+" Domain", domain, "value", cfg.Credentials.DomainValue)
			}
		}
	}
}

// assertDeleteShape covers operationId "delete" (DELETE /auth/token).
func assertDeleteShape(t *testing.T, srv *opsnetmock.Server, log []opsnetmock.Entry) {
	t.Helper()
	entries := entriesFor(log, "delete")
	if len(entries) != 1 {
		t.Errorf(`operationId "delete" was called %d times, want exactly 1: Close must revoke the token once`, len(entries))
	}
	live := srv.TokensIssued()
	for _, e := range entries {
		what := "delete request #" + itoa(e.Seq)
		if e.Method != http.MethodDelete {
			t.Errorf("%s: method %s, want DELETE", what, e.Method)
		}
		if e.Path != authTokenPath {
			t.Errorf("%s: path %q, want %q", what, e.Path, authTokenPath)
		}
		assertKeys(t, what+" query", e.QueryKeys(), nil)
		if len(e.Body) != 0 {
			t.Errorf("%s: sent a %d byte body; the operation declares no request body", what, len(e.Body))
		}
		assertAuthHeader(t, what, srv, e)
		if e.TokenIndex != live {
			t.Errorf("%s: revoked token #%d but the client's current token is #%d; Close must revoke the token in use, not a replaced one",
				what, e.TokenIndex, live)
		}
	}
}

// assertListShape covers operationId "listApplications" (GET /groups/applications).
func assertListShape(t *testing.T, cfg opsnet.Config, log []opsnetmock.Entry) {
	t.Helper()
	entries := entriesFor(log, "listApplications")
	if len(entries) == 0 {
		t.Error(`operationId "listApplications" was never called`)
		return
	}
	for i, e := range entries {
		what := "listApplications request #" + itoa(e.Seq)
		if e.Method != http.MethodGet {
			t.Errorf("%s: method %s, want GET", what, e.Method)
		}
		if e.Path != appsPath {
			t.Errorf("%s: path %q, want %q", what, e.Path, appsPath)
		}
		if len(e.Body) != 0 {
			t.Errorf("%s: sent a %d byte body; the operation declares no request body", what, len(e.Body))
		}

		// The key set is what proves omission: an implementation that always
		// appends cursor= and modifiedAfter=0 shows up as extra keys here.
		want := wantListQueryKeys(cfg, !e.Query.Has("cursor"))
		assertKeys(t, what+" query", e.QueryKeys(), want)

		if cfg.PageSize > 0 && e.Query.Get("size") != strconv.Itoa(cfg.PageSize) {
			t.Errorf("%s: size=%q, want %q", what, e.Query.Get("size"), strconv.Itoa(cfg.PageSize))
		}
		if e.Query.Has("cursor") && e.Query.Get("cursor") == "" {
			t.Errorf("%s: sent cursor= with an empty value; an unset cursor must be omitted", what)
		}
		if i == 0 && e.Query.Has("cursor") {
			t.Errorf("%s: the first page carried cursor=%q; there is no cursor before the first response",
				what, e.Query.Get("cursor"))
		}
	}
}

// assertDetailShape covers operationId "getApplicationById"
// (GET /groups/applications/{id}).
func assertDetailShape(t *testing.T, cfg opsnet.Config, log []opsnetmock.Entry) {
	t.Helper()
	for _, e := range entriesFor(log, "getApplicationById") {
		what := "getApplicationById request #" + itoa(e.Seq)
		if e.Method != http.MethodGet {
			t.Errorf("%s: method %s, want GET", what, e.Method)
		}
		id := strings.TrimPrefix(e.Path, appPathPrefix)
		if !strings.HasPrefix(e.Path, appPathPrefix) || id == "" {
			t.Errorf("%s: path %q, want %q + an entity id", what, e.Path, appPathPrefix)
		}
		if len(e.Body) != 0 {
			t.Errorf("%s: sent a %d byte body; the operation declares no request body", what, len(e.Body))
		}
		assertKeys(t, what+" query", e.QueryKeys(), wantDetailQueryKeys(cfg))
		for _, flag := range []string{"fetch_member_counts", "fetch_update_status"} {
			if e.Query.Has(flag) && e.Query.Get(flag) != "true" {
				t.Errorf("%s: %s=%q; the flag must be omitted rather than sent as %q",
					what, flag, e.Query.Get(flag), e.Query.Get(flag))
			}
		}
	}
}

func assertAuthHeader(t *testing.T, what string, srv *opsnetmock.Server, e opsnetmock.Entry) {
	t.Helper()
	if !strings.HasPrefix(e.Authorization, opsnetmock.AuthPrefix) {
		t.Errorf("%s: Authorization %q, want the %q prefix required by ApiKeyAuth",
			what, e.Authorization, opsnetmock.AuthPrefix)
		return
	}
	if e.TokenIndex == 0 {
		t.Errorf("%s: Authorization %q does not name a token this server issued", what, e.Authorization)
		return
	}
	if want := opsnetmock.AuthPrefix + srv.TokenValue(e.TokenIndex); e.Authorization != want {
		t.Errorf("%s: Authorization %q, want %q", what, e.Authorization, want)
	}
}

func assertStringProp(t *testing.T, what string, obj map[string]jsonRaw, key, want string) {
	t.Helper()
	raw, ok := obj[key]
	if !ok {
		return
	}
	got, err := unquote(raw)
	if err != nil {
		t.Errorf("%s: property %q is not a JSON string: %s", what, key, string(raw))
		return
	}
	if got != want {
		t.Errorf("%s: property %q = %q, want %q", what, key, got, want)
	}
}

// assertAuthenticatedRequests checks the Authorization header on every operation
// that declares ApiKeyAuth.
func assertAuthenticatedRequests(t *testing.T, srv *opsnetmock.Server) {
	t.Helper()
	for _, e := range srv.Log() {
		switch e.OperationID {
		case "listApplications", "getApplicationById", "delete":
			assertAuthHeader(t, e.OperationID+" request #"+itoa(e.Seq), srv, e)
		}
	}
}
