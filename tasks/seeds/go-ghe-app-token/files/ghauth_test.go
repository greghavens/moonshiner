// Acceptance harness for the ghauth package: a loopback fake GitHub API
// exercising the GitHub App authentication wire contract pinned in
// docs/contract.json. No real GitHub, no real credentials, no sleeps.
// Protected — do not modify. Run: go test -race -timeout 30s ./...
package ghauth_test

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	ghauth "go-ghe-app-token"
)

const (
	clientID       = "Iv1.8fae12ab34cd56ef"
	targetOrg      = "Machine-Shop"
	installationID = "9581"
	issuedToken    = "ghs_16C7e42F292c6912E7710c838347Ae178B4a"
	apiVersion     = "2026-03-10"
	acceptMedia    = "application/vnd.github+json"
)

// Fixed base instant for the injected clock; the fake server prices
// expires_at relative to it. Nothing reads the wall clock.
var baseNow = time.Date(2026, 7, 17, 12, 0, 0, 0, time.UTC)

type recorded struct {
	Method     string
	Path       string
	Query      string
	Auth       string
	Accept     string
	APIVersion string
	UserAgent  string
	Body       []byte
}

type fixture struct {
	mu   sync.Mutex
	reqs []recorded

	now       time.Time // shared injected clock (guarded by mu)
	expiresIn time.Duration

	installationsStatus int
	installationsBody   string
	tokenStatus         int
	tokenBody           string // when set, overrides the computed 201 body

	srv *httptest.Server
	key *rsa.PrivateKey
}

func (f *fixture) setNow(t time.Time) {
	f.mu.Lock()
	f.now = t
	f.mu.Unlock()
}

func (f *fixture) clock() time.Time {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.now
}

func (f *fixture) requests() []recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]recorded, len(f.reqs))
	copy(out, f.reqs)
	return out
}

func (f *fixture) tokenExchanges() []recorded {
	var out []recorded
	for _, r := range f.requests() {
		if r.Method == http.MethodPost && r.Path == "/app/installations/"+installationID+"/access_tokens" {
			out = append(out, r)
		}
	}
	return out
}

const installationsPage = `[
  {"id": 77, "app_id": 314159, "repository_selection": "all",
   "account": {"login": "other-widgets", "type": "Organization"},
   "access_tokens_url": "UNUSED", "app_slug": "shop-floor-ci"},
  {"id": ` + installationID + `, "app_id": 314159, "repository_selection": "selected",
   "account": {"login": "` + targetOrg + `", "type": "Organization"},
   "access_tokens_url": "UNUSED", "app_slug": "shop-floor-ci"}
]`

func newFixture(t *testing.T) (*fixture, *ghauth.Client) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generating test RSA key: %v", err)
	}
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatalf("marshaling test key: %v", err)
	}
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})

	f := &fixture{
		now:                 baseNow,
		expiresIn:           time.Hour,
		installationsStatus: 200,
		installationsBody:   installationsPage,
		tokenStatus:         201,
		key:                 key,
	}

	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		f.mu.Lock()
		f.reqs = append(f.reqs, recorded{
			Method:     r.Method,
			Path:       r.URL.Path,
			Query:      r.URL.RawQuery,
			Auth:       r.Header.Get("Authorization"),
			Accept:     r.Header.Get("Accept"),
			APIVersion: r.Header.Get("X-GitHub-Api-Version"),
			UserAgent:  r.Header.Get("User-Agent"),
			Body:       body,
		})
		now := f.now
		expiresIn := f.expiresIn
		instStatus, instBody := f.installationsStatus, f.installationsBody
		tokStatus, tokBody := f.tokenStatus, f.tokenBody
		f.mu.Unlock()

		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/app/installations":
			w.WriteHeader(instStatus)
			io.WriteString(w, instBody)
		case r.Method == http.MethodPost && r.URL.Path == "/app/installations/"+installationID+"/access_tokens":
			if tokBody != "" {
				w.WriteHeader(tokStatus)
				io.WriteString(w, tokBody)
				return
			}
			w.WriteHeader(201)
			resp := map[string]any{
				"token":                issuedToken,
				"expires_at":           now.Add(expiresIn).Format(time.RFC3339),
				"permissions":          map[string]string{"actions": "read", "contents": "read"},
				"repository_selection": "selected",
			}
			json.NewEncoder(w).Encode(resp)
		default:
			w.WriteHeader(404)
			io.WriteString(w, `{"message":"Not Found","documentation_url":"https://docs.github.com/rest"}`)
		}
	}))
	t.Cleanup(f.srv.Close)

	c, err := ghauth.New(ghauth.Config{
		BaseURL:       f.srv.URL,
		ClientID:      clientID,
		PrivateKeyPEM: pemBytes,
		HTTPClient:    f.srv.Client(),
	})
	if err != nil {
		t.Fatalf("ghauth.New: %v", err)
	}
	c.Now = f.clock
	return f, c
}

type jwtParts struct {
	Header struct {
		Alg string `json:"alg"`
		Typ string `json:"typ"`
	}
	Claims struct {
		Iss string `json:"iss"`
		Iat int64  `json:"iat"`
		Exp int64  `json:"exp"`
	}
	SigningInput string
	Signature    []byte
}

func decodeJWT(t *testing.T, token string) jwtParts {
	t.Helper()
	segs := strings.Split(token, ".")
	if len(segs) != 3 {
		t.Fatalf("JWT must have 3 dot-separated segments, got %d in %q", len(segs), token)
	}
	var p jwtParts
	for i, into := range []any{&p.Header, &p.Claims} {
		raw, err := base64.RawURLEncoding.DecodeString(segs[i])
		if err != nil {
			t.Fatalf("JWT segment %d is not unpadded base64url: %v", i, err)
		}
		if err := json.Unmarshal(raw, into); err != nil {
			t.Fatalf("JWT segment %d is not JSON: %v (raw: %s)", i, err, raw)
		}
	}
	sig, err := base64.RawURLEncoding.DecodeString(segs[2])
	if err != nil {
		t.Fatalf("JWT signature is not unpadded base64url: %v", err)
	}
	p.SigningInput = segs[0] + "." + segs[1]
	p.Signature = sig
	return p
}

func verifyAppJWT(t *testing.T, f *fixture, token string, wantNow time.Time) jwtParts {
	t.Helper()
	p := decodeJWT(t, token)
	if p.Header.Alg != "RS256" {
		t.Errorf("JWT alg = %q, want RS256", p.Header.Alg)
	}
	if p.Header.Typ != "JWT" {
		t.Errorf("JWT typ = %q, want JWT", p.Header.Typ)
	}
	if p.Claims.Iss != clientID {
		t.Errorf("iss = %q, want the app client ID %q", p.Claims.Iss, clientID)
	}
	if got, want := p.Claims.Iat, wantNow.Add(-60*time.Second).Unix(); got != want {
		t.Errorf("iat = %d, want now-60s = %d (clock-drift allowance)", got, want)
	}
	if got, want := p.Claims.Exp, wantNow.Add(9*time.Minute).Unix(); got != want {
		t.Errorf("exp = %d, want now+540s = %d (inside the 10-minute cap)", got, want)
	}
	sum := sha256.Sum256([]byte(p.SigningInput))
	if err := rsa.VerifyPKCS1v15(&f.key.PublicKey, crypto.SHA256, sum[:], p.Signature); err != nil {
		t.Errorf("JWT signature does not verify with the app key (RS256/PKCS1v15): %v", err)
	}
	return p
}

func TestAppJWTContract(t *testing.T) {
	f, c := newFixture(t)
	token, err := c.AppJWT()
	if err != nil {
		t.Fatalf("AppJWT: %v", err)
	}
	verifyAppJWT(t, f, token, baseNow)
	if len(f.requests()) != 0 {
		t.Errorf("minting the app JWT is purely local; saw %d HTTP requests", len(f.requests()))
	}
}

func TestInstallationDiscovery(t *testing.T) {
	f, c := newFixture(t)
	inst, err := c.InstallationFor(context.Background(), "machine-shop")
	if err != nil {
		t.Fatalf("InstallationFor: %v", err)
	}
	if inst.ID != 9581 {
		t.Errorf("installation id = %d, want 9581", inst.ID)
	}
	if inst.AppID != 314159 {
		t.Errorf("app_id = %d, want 314159", inst.AppID)
	}
	if inst.AccountLogin != targetOrg {
		t.Errorf("account login = %q, want %q (server casing preserved)", inst.AccountLogin, targetOrg)
	}
	if inst.RepositorySelection != "selected" {
		t.Errorf("repository_selection = %q, want selected", inst.RepositorySelection)
	}

	reqs := f.requests()
	if len(reqs) != 1 {
		t.Fatalf("expected exactly 1 request, got %d", len(reqs))
	}
	r := reqs[0]
	if r.Method != http.MethodGet || r.Path != "/app/installations" {
		t.Errorf("request = %s %s, want GET /app/installations", r.Method, r.Path)
	}
	if r.Query != "per_page=100" {
		t.Errorf("query = %q, want per_page=100", r.Query)
	}
	if r.Accept != acceptMedia {
		t.Errorf("Accept = %q, want %q", r.Accept, acceptMedia)
	}
	if r.APIVersion != apiVersion {
		t.Errorf("X-GitHub-Api-Version = %q, want the current version %q", r.APIVersion, apiVersion)
	}
	if r.UserAgent == "" || strings.HasPrefix(r.UserAgent, "Go-http-client") {
		t.Errorf("User-Agent = %q; GitHub requires a real product User-Agent", r.UserAgent)
	}
	if !strings.HasPrefix(r.Auth, "Bearer ") {
		t.Fatalf("Authorization = %q, want Bearer <app JWT>", r.Auth)
	}
	verifyAppJWT(t, f, strings.TrimPrefix(r.Auth, "Bearer "), baseNow)
}

func TestInstallationForUnknownOrg(t *testing.T) {
	_, c := newFixture(t)
	_, err := c.InstallationFor(context.Background(), "no-such-org")
	if err == nil {
		t.Fatalf("InstallationFor must fail when the app is not installed on the org")
	}
	if !strings.Contains(err.Error(), "no-such-org") {
		t.Errorf("error should name the org, got: %v", err)
	}
}

func TestInstallationTokenExchange(t *testing.T) {
	f, c := newFixture(t)
	tok, err := c.InstallationToken(context.Background(), "machine-shop")
	if err != nil {
		t.Fatalf("InstallationToken: %v", err)
	}
	if tok.Value != issuedToken {
		t.Errorf("token = %q, want %q", tok.Value, issuedToken)
	}
	wantExp := baseNow.Add(time.Hour)
	if !tok.ExpiresAt.Equal(wantExp) {
		t.Errorf("ExpiresAt = %v, want %v (parsed from expires_at)", tok.ExpiresAt, wantExp)
	}
	if tok.RepositorySelection != "selected" {
		t.Errorf("RepositorySelection = %q, want selected", tok.RepositorySelection)
	}
	if got := tok.Permissions["contents"]; got != "read" {
		t.Errorf("permissions[contents] = %q, want read", got)
	}

	exchanges := f.tokenExchanges()
	if len(exchanges) != 1 {
		t.Fatalf("token exchanges = %d, want exactly 1", len(exchanges))
	}
	r := exchanges[0]
	if r.Accept != acceptMedia || r.APIVersion != apiVersion {
		t.Errorf("exchange headers Accept=%q version=%q, want %q / %q",
			r.Accept, r.APIVersion, acceptMedia, apiVersion)
	}
	if !strings.HasPrefix(r.Auth, "Bearer ") {
		t.Fatalf("exchange Authorization = %q, want Bearer <app JWT>", r.Auth)
	}
	got := strings.TrimPrefix(r.Auth, "Bearer ")
	if got == issuedToken {
		t.Fatalf("the token exchange must be authenticated with the app JWT, not an installation token")
	}
	verifyAppJWT(t, f, got, baseNow)
}

func TestTokenCachedUntilExpiryMargin(t *testing.T) {
	f, c := newFixture(t)
	ctx := context.Background()
	first, err := c.InstallationToken(ctx, "machine-shop")
	if err != nil {
		t.Fatalf("first InstallationToken: %v", err)
	}
	before := len(f.requests())

	// Still comfortably before the refresh margin: must be served from cache.
	f.setNow(baseNow.Add(30 * time.Minute))
	second, err := c.InstallationToken(ctx, "machine-shop")
	if err != nil {
		t.Fatalf("second InstallationToken: %v", err)
	}
	if second.Value != first.Value {
		t.Errorf("cached call returned a different token")
	}
	if got := len(f.requests()); got != before {
		t.Errorf("cached call made %d extra HTTP requests, want 0", got-before)
	}

	// 59m01s in: within 60s of expires_at, the cache must refresh.
	f.setNow(baseNow.Add(59*time.Minute + time.Second))
	third, err := c.InstallationToken(ctx, "machine-shop")
	if err != nil {
		t.Fatalf("third InstallationToken: %v", err)
	}
	if got := len(f.tokenExchanges()); got != 2 {
		t.Fatalf("token exchanges after expiry margin = %d, want 2", got)
	}
	wantExp := baseNow.Add(59*time.Minute + time.Second).Add(time.Hour)
	if !third.ExpiresAt.Equal(wantExp) {
		t.Errorf("refreshed ExpiresAt = %v, want %v", third.ExpiresAt, wantExp)
	}
	// The refresh must be signed with a freshly minted JWT, not the boot-time one.
	fresh := f.tokenExchanges()[1]
	verifyAppJWT(t, f, strings.TrimPrefix(fresh.Auth, "Bearer "), baseNow.Add(59*time.Minute+time.Second))
}

func TestDiscoveryIsCachedAcrossRefreshes(t *testing.T) {
	f, c := newFixture(t)
	ctx := context.Background()
	if _, err := c.InstallationToken(ctx, "machine-shop"); err != nil {
		t.Fatalf("InstallationToken: %v", err)
	}
	f.setNow(baseNow.Add(2 * time.Hour))
	if _, err := c.InstallationToken(ctx, "machine-shop"); err != nil {
		t.Fatalf("InstallationToken after expiry: %v", err)
	}
	var lists int
	for _, r := range f.requests() {
		if r.Method == http.MethodGet && r.Path == "/app/installations" {
			lists++
		}
	}
	if lists != 1 {
		t.Errorf("installation discovery requests = %d, want 1 (installation id does not change)", lists)
	}
}

func TestConcurrentCallersShareOneExchange(t *testing.T) {
	f, c := newFixture(t)
	const callers = 16
	tokens := make([]string, callers)
	errs := make([]error, callers)
	var wg sync.WaitGroup
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			tok, err := c.InstallationToken(context.Background(), "machine-shop")
			if err == nil {
				tokens[i] = tok.Value
			}
			errs[i] = err
		}(i)
	}
	wg.Wait()
	for i := 0; i < callers; i++ {
		if errs[i] != nil {
			t.Fatalf("caller %d: %v", i, errs[i])
		}
		if tokens[i] != issuedToken {
			t.Errorf("caller %d token = %q, want %q", i, tokens[i], issuedToken)
		}
	}
	if got := len(f.tokenExchanges()); got != 1 {
		t.Errorf("concurrent callers caused %d token exchanges, want exactly 1", got)
	}
}

func TestAPIErrorDecodingAndRedaction(t *testing.T) {
	f, c := newFixture(t)
	f.mu.Lock()
	f.installationsStatus = 401
	f.installationsBody = `{"message":"'Expiration time' claim ('exp') is too far in the future","documentation_url":"https://docs.github.com/rest"}`
	f.mu.Unlock()

	_, err := c.InstallationToken(context.Background(), "machine-shop")
	if err == nil {
		t.Fatalf("InstallationToken must fail on 401")
	}
	var ae *ghauth.APIError
	if !asAPIError(err, &ae) {
		t.Fatalf("error must unwrap to *ghauth.APIError, got %T: %v", err, err)
	}
	if ae.StatusCode != 401 {
		t.Errorf("StatusCode = %d, want 401", ae.StatusCode)
	}
	if !strings.Contains(ae.Message, "too far in the future") {
		t.Errorf("Message should carry GitHub's message field, got %q", ae.Message)
	}
	if !strings.Contains(err.Error(), "401") || !strings.Contains(err.Error(), "too far in the future") {
		t.Errorf("Error() should surface status and message, got %q", err.Error())
	}
	assertRedacted(t, f, err.Error())
}

func TestExchangeFailureIsRedacted(t *testing.T) {
	f, c := newFixture(t)
	f.mu.Lock()
	f.tokenStatus = 422
	f.tokenBody = `{"message":"Validation Failed","documentation_url":"https://docs.github.com/rest","errors":[{"code":"custom"}]}`
	f.mu.Unlock()

	_, err := c.InstallationToken(context.Background(), "machine-shop")
	if err == nil {
		t.Fatalf("InstallationToken must fail on 422")
	}
	var ae *ghauth.APIError
	if !asAPIError(err, &ae) {
		t.Fatalf("error must unwrap to *ghauth.APIError, got %T: %v", err, err)
	}
	if ae.StatusCode != 422 || ae.Message != "Validation Failed" {
		t.Errorf("APIError = %d/%q, want 422/Validation Failed", ae.StatusCode, ae.Message)
	}
	assertRedacted(t, f, err.Error())
}

// assertRedacted fails if any credential material appears in error text: the
// signed JWT (identifiable by its base64url header), the private key, or an
// issued installation token.
func assertRedacted(t *testing.T, f *fixture, msg string) {
	t.Helper()
	if strings.Contains(msg, issuedToken) {
		t.Errorf("error text leaks the installation token: %q", msg)
	}
	if strings.Contains(msg, "ghs_") {
		t.Errorf("error text leaks an installation token prefix: %q", msg)
	}
	if strings.Contains(msg, "eyJ") {
		t.Errorf("error text appears to leak a JWT: %q", msg)
	}
	if strings.Contains(msg, "PRIVATE KEY") || strings.Contains(msg, "MII") {
		t.Errorf("error text appears to leak private key material: %q", msg)
	}
	if strings.Contains(msg, "Bearer ") {
		t.Errorf("error text leaks an Authorization value: %q", msg)
	}
}

func TestContextCancellationPropagates(t *testing.T) {
	_, c := newFixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := c.InstallationToken(ctx, "machine-shop")
	if err == nil {
		t.Fatalf("InstallationToken must fail once the context is canceled")
	}
	if !strings.Contains(err.Error(), "context canceled") {
		t.Errorf("error should propagate context cancellation, got: %v", err)
	}
}

func TestRejectsUnparseableKey(t *testing.T) {
	_, err := ghauth.New(ghauth.Config{
		BaseURL:       "http://127.0.0.1:0",
		ClientID:      clientID,
		PrivateKeyPEM: []byte("not a pem at all"),
	})
	if err == nil {
		t.Fatalf("New must reject an unparseable private key")
	}
}

func asAPIError(err error, target **ghauth.APIError) bool {
	for err != nil {
		if ae, ok := err.(*ghauth.APIError); ok {
			*target = ae
			return true
		}
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}
