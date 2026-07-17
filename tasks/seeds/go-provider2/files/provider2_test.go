package provider2

// Acceptance tests for the notification-sender port and its two vendor
// adapters. Every scenario runs against scripted httptest servers on
// loopback: each server answers from a fixed queue of responses and records
// every request (method, path, headers, decoded JSON body), so the wire
// assertions below are exact. Nothing touches a real network.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

// Compile-time proof that all three constructors satisfy the port.
var (
	_ Sender = NewMeteor("http://example.invalid", nil)
	_ Sender = NewPelican("http://example.invalid", nil)
	_ Sender = NewFailover(NewMeteor("http://example.invalid", nil), NewPelican("http://example.invalid", nil))
)

type respSpec struct {
	status int // 0 means 200
	body   string
}

type sentReq struct {
	method      string
	path        string
	contentType string
	idemHeader  []string // values of the Idempotency-Key header; nil when absent
	body        map[string]any
}

type vendorScript struct {
	mu    sync.Mutex
	steps []respSpec
	reqs  []sentReq
}

func (v *vendorScript) requests() []sentReq {
	v.mu.Lock()
	defer v.mu.Unlock()
	out := make([]sentReq, len(v.reqs))
	copy(out, v.reqs)
	return out
}

func (v *vendorScript) count() int {
	v.mu.Lock()
	defer v.mu.Unlock()
	return len(v.reqs)
}

func newVendor(t *testing.T, steps ...respSpec) (*httptest.Server, *vendorScript) {
	t.Helper()
	v := &vendorScript{steps: steps}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var decoded map[string]any
		_ = json.Unmarshal(raw, &decoded)
		v.mu.Lock()
		var idem []string
		if vals, ok := r.Header["Idempotency-Key"]; ok {
			idem = append([]string(nil), vals...)
		}
		v.reqs = append(v.reqs, sentReq{
			method:      r.Method,
			path:        r.URL.Path,
			contentType: r.Header.Get("Content-Type"),
			idemHeader:  idem,
			body:        decoded,
		})
		var step respSpec
		if len(v.steps) > 0 {
			step = v.steps[0]
			v.steps = v.steps[1:]
		} else {
			step = respSpec{status: 599, body: `{"error":"script exhausted: unexpected extra request"}`}
		}
		v.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if step.status == 0 {
			step.status = http.StatusOK
		}
		w.WriteHeader(step.status)
		fmt.Fprint(w, step.body)
	}))
	t.Cleanup(srv.Close)
	return srv, v
}

func msg(key string) Message {
	return Message{
		To:             "billing@acme-corp.example",
		From:           "noreply@ourapp.example",
		Subject:        "Your March statement",
		Body:           "The statement is attached to your dashboard.",
		IdempotencyKey: key,
	}
}

// deadEndpoint returns a URL on which nothing is listening.
func deadEndpoint(t *testing.T) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	url := srv.URL
	srv.Close()
	return url
}

func client() *http.Client {
	return &http.Client{Timeout: 5 * time.Second}
}

func TestMeteorWireShapeAndReceipt(t *testing.T) {
	srv, v := newVendor(t, respSpec{body: `{"message_id":"mtr-4471","status":"queued"}`})
	rc, err := NewMeteor(srv.URL, client()).Send(context.Background(), msg("stmt-2026-03-77"))
	if err != nil {
		t.Fatalf("Send() error = %v", err)
	}
	if rc.Provider != "meteor" || rc.MessageID != "mtr-4471" {
		t.Fatalf("receipt = %+v, want provider meteor and the vendor's message_id", rc)
	}
	reqs := v.requests()
	if len(reqs) != 1 {
		t.Fatalf("vendor saw %d requests, want exactly 1", len(reqs))
	}
	r := reqs[0]
	if r.method != http.MethodPost || r.path != "/v1/messages" {
		t.Fatalf("request = %s %s, want POST /v1/messages", r.method, r.path)
	}
	if !strings.HasPrefix(r.contentType, "application/json") {
		t.Fatalf("Content-Type = %q, want application/json", r.contentType)
	}
	if !reflect.DeepEqual(r.idemHeader, []string{"stmt-2026-03-77"}) {
		t.Fatalf("Idempotency-Key header = %v, want exactly [stmt-2026-03-77]", r.idemHeader)
	}
	want := map[string]any{
		"to":      "billing@acme-corp.example",
		"from":    "noreply@ourapp.example",
		"subject": "Your March statement",
		"body":    "The statement is attached to your dashboard.",
	}
	if !reflect.DeepEqual(r.body, want) {
		t.Fatalf("meteor body = %v, want exactly %v (no extra fields)", r.body, want)
	}
}

func TestMeteorNilClientUsesADefault(t *testing.T) {
	srv, _ := newVendor(t, respSpec{body: `{"message_id":"mtr-1","status":"queued"}`})
	rc, err := NewMeteor(srv.URL, nil).Send(context.Background(), msg(""))
	if err != nil {
		t.Fatalf("Send() with a nil *http.Client must still work, got %v", err)
	}
	if rc.MessageID != "mtr-1" {
		t.Fatalf("receipt = %+v", rc)
	}
}

func TestPelicanWireShapeAcceptsA202(t *testing.T) {
	srv, v := newVendor(t, respSpec{status: 202, body: `{"id":"pel_98213","accepted":true}`})
	rc, err := NewPelican(srv.URL, client()).Send(context.Background(), msg("stmt-2026-03-77"))
	if err != nil {
		t.Fatalf("Send() error = %v", err)
	}
	if rc.Provider != "pelican" || rc.MessageID != "pel_98213" {
		t.Fatalf("receipt = %+v, want provider pelican and the vendor's id", rc)
	}
	r := v.requests()[0]
	if r.method != http.MethodPost || r.path != "/send" {
		t.Fatalf("request = %s %s, want POST /send", r.method, r.path)
	}
	if !strings.HasPrefix(r.contentType, "application/json") {
		t.Fatalf("Content-Type = %q, want application/json", r.contentType)
	}
	want := map[string]any{
		"recipient":    "billing@acme-corp.example",
		"sender":       "noreply@ourapp.example",
		"title":        "Your March statement",
		"content":      "The statement is attached to your dashboard.",
		"dedupe_token": "stmt-2026-03-77",
	}
	if !reflect.DeepEqual(r.body, want) {
		t.Fatalf("pelican body = %v, want exactly %v", r.body, want)
	}
	if r.idemHeader != nil {
		t.Fatalf("pelican got an Idempotency-Key header %v; its dedupe token rides in the JSON body", r.idemHeader)
	}
}

func TestEmptyKeyOmitsMeteorHeaderAndPelicanField(t *testing.T) {
	msrv, mv := newVendor(t, respSpec{body: `{"message_id":"mtr-2","status":"queued"}`})
	if _, err := NewMeteor(msrv.URL, client()).Send(context.Background(), msg("")); err != nil {
		t.Fatalf("meteor Send() error = %v", err)
	}
	if got := mv.requests()[0].idemHeader; got != nil {
		t.Fatalf("meteor sent Idempotency-Key %v for a message without a key; the header must be absent", got)
	}

	psrv, pv := newVendor(t, respSpec{status: 202, body: `{"id":"pel_1","accepted":true}`})
	if _, err := NewPelican(psrv.URL, client()).Send(context.Background(), msg("")); err != nil {
		t.Fatalf("pelican Send() error = %v", err)
	}
	if _, present := pv.requests()[0].body["dedupe_token"]; present {
		t.Fatalf("pelican body = %v; dedupe_token must be omitted entirely when the message has no key", pv.requests()[0].body)
	}
}

func TestMeteor4xxBecomesTypedProviderError(t *testing.T) {
	srv, v := newVendor(t, respSpec{
		status: 422,
		body:   `{"error":{"code":"invalid_recipient","message":"recipient address failed vendor checks"}}`,
	})
	_, err := NewMeteor(srv.URL, client()).Send(context.Background(), msg("k-1"))
	var pe *ProviderError
	if !errors.As(err, &pe) {
		t.Fatalf("Send() error = %v (%T), want a *ProviderError", err, err)
	}
	if pe.Provider != "meteor" || pe.Status != 422 || pe.Code != "invalid_recipient" {
		t.Fatalf("ProviderError = %+v, want provider/status/code from the meteor envelope", pe)
	}
	if !strings.Contains(pe.Detail, "recipient address failed vendor checks") {
		t.Fatalf("Detail = %q, want the vendor's message text", pe.Detail)
	}
	if !strings.Contains(err.Error(), "meteor") || !strings.Contains(err.Error(), "422") {
		t.Fatalf("Error() = %q, want the provider name and status visible", err.Error())
	}
	if v.count() != 1 {
		t.Fatalf("vendor saw %d requests, want exactly 1 — adapters never retry on their own", v.count())
	}
}

func TestPelicanErrorArrayIsParsed(t *testing.T) {
	srv, v := newVendor(t, respSpec{
		status: 403,
		body:   `{"errors":[{"reason":"account_suspended","detail":"contract 4411 is past due"},{"reason":"secondary","detail":"ignored"}]}`,
	})
	_, err := NewPelican(srv.URL, client()).Send(context.Background(), msg("k-2"))
	var pe *ProviderError
	if !errors.As(err, &pe) {
		t.Fatalf("Send() error = %v (%T), want a *ProviderError", err, err)
	}
	if pe.Provider != "pelican" || pe.Status != 403 || pe.Code != "account_suspended" {
		t.Fatalf("ProviderError = %+v, want the FIRST entry of the pelican errors array", pe)
	}
	if !strings.Contains(pe.Detail, "contract 4411 is past due") {
		t.Fatalf("Detail = %q, want the vendor's detail text", pe.Detail)
	}
	if v.count() != 1 {
		t.Fatalf("vendor saw %d requests, want exactly 1", v.count())
	}
}

func TestUnparseableErrorBodyStillCarriesStatusAndRawText(t *testing.T) {
	srv, _ := newVendor(t, respSpec{status: 503, body: "upstream drain in progress\n"})
	_, err := NewMeteor(srv.URL, client()).Send(context.Background(), msg("k-3"))
	var pe *ProviderError
	if !errors.As(err, &pe) {
		t.Fatalf("Send() error = %v (%T), want a *ProviderError even when the body is not the documented envelope", err, err)
	}
	if pe.Provider != "meteor" || pe.Status != 503 || pe.Code != "" {
		t.Fatalf("ProviderError = %+v, want status 503 and an empty Code", pe)
	}
	if !strings.Contains(pe.Detail, "upstream drain in progress") {
		t.Fatalf("Detail = %q, want the raw body text preserved", pe.Detail)
	}
}

func TestFailoverPrefersPrimaryAndNeverTouchesFallbackOnSuccess(t *testing.T) {
	msrv, mv := newVendor(t, respSpec{body: `{"message_id":"mtr-77","status":"queued"}`})
	psrv, pv := newVendor(t)
	s := NewFailover(NewMeteor(msrv.URL, client()), NewPelican(psrv.URL, client()))
	rc, err := s.Send(context.Background(), msg("k-4"))
	if err != nil {
		t.Fatalf("Send() error = %v", err)
	}
	if rc.Provider != "meteor" || rc.MessageID != "mtr-77" {
		t.Fatalf("receipt = %+v, want the primary's receipt", rc)
	}
	if mv.count() != 1 || pv.count() != 0 {
		t.Fatalf("requests: primary=%d fallback=%d, want 1 and 0", mv.count(), pv.count())
	}
}

func TestFailoverOn5xxCarriesTheSameKeyToBothVendors(t *testing.T) {
	msrv, mv := newVendor(t, respSpec{status: 502, body: `{"error":{"code":"upstream_down","message":"meteor edge is misbehaving"}}`})
	psrv, pv := newVendor(t, respSpec{status: 202, body: `{"id":"pel_55","accepted":true}`})
	s := NewFailover(NewMeteor(msrv.URL, client()), NewPelican(psrv.URL, client()))
	rc, err := s.Send(context.Background(), msg("stmt-2026-04-19"))
	if err != nil {
		t.Fatalf("Send() error = %v, want the fallback to carry the send", err)
	}
	if rc.Provider != "pelican" || rc.MessageID != "pel_55" {
		t.Fatalf("receipt = %+v, want the fallback's receipt", rc)
	}
	if mv.count() != 1 || pv.count() != 1 {
		t.Fatalf("requests: primary=%d fallback=%d, want exactly 1 each", mv.count(), pv.count())
	}
	if got := mv.requests()[0].idemHeader; !reflect.DeepEqual(got, []string{"stmt-2026-04-19"}) {
		t.Fatalf("primary Idempotency-Key = %v, want [stmt-2026-04-19]", got)
	}
	if got := pv.requests()[0].body["dedupe_token"]; got != "stmt-2026-04-19" {
		t.Fatalf("fallback dedupe_token = %v, want the SAME key the primary saw", got)
	}
}

func TestFailoverNeverFallsBackOnA4xx(t *testing.T) {
	msrv, mv := newVendor(t, respSpec{status: 422, body: `{"error":{"code":"invalid_recipient","message":"bad address"}}`})
	psrv, pv := newVendor(t, respSpec{status: 202, body: `{"id":"pel_9","accepted":true}`})
	s := NewFailover(NewMeteor(msrv.URL, client()), NewPelican(psrv.URL, client()))
	_, err := s.Send(context.Background(), msg("k-5"))
	var pe *ProviderError
	if !errors.As(err, &pe) {
		t.Fatalf("Send() error = %v (%T), want the primary's *ProviderError passed straight through", err, err)
	}
	if pe.Provider != "meteor" || pe.Status != 422 {
		t.Fatalf("ProviderError = %+v, want the primary's 422 untouched", pe)
	}
	if pv.count() != 0 {
		t.Fatalf("fallback saw %d requests after a 4xx, want 0 — a rejected request must not be re-sent anywhere", pv.count())
	}
	if mv.count() != 1 {
		t.Fatalf("primary saw %d requests, want exactly 1", mv.count())
	}
}

func TestFailoverOnTransportError(t *testing.T) {
	dead := deadEndpoint(t)
	psrv, pv := newVendor(t, respSpec{status: 202, body: `{"id":"pel_31","accepted":true}`})
	s := NewFailover(NewMeteor(dead, client()), NewPelican(psrv.URL, client()))
	rc, err := s.Send(context.Background(), msg("k-6"))
	if err != nil {
		t.Fatalf("Send() error = %v, want a connection failure on the primary to fail over", err)
	}
	if rc.Provider != "pelican" || rc.MessageID != "pel_31" {
		t.Fatalf("receipt = %+v, want the fallback's receipt", rc)
	}
	if pv.count() != 1 {
		t.Fatalf("fallback saw %d requests, want exactly 1", pv.count())
	}
}

func TestFailoverBothFailingReportsBothErrors(t *testing.T) {
	msrv, mv := newVendor(t, respSpec{status: 500, body: `{"error":{"code":"internal","message":"meteor exploded"}}`})
	psrv, pv := newVendor(t, respSpec{status: 502, body: `{"errors":[{"reason":"gateway_error","detail":"pelican edge is down"}]}`})
	s := NewFailover(NewMeteor(msrv.URL, client()), NewPelican(psrv.URL, client()))
	_, err := s.Send(context.Background(), msg("k-7"))
	var fe *FailoverError
	if !errors.As(err, &fe) {
		t.Fatalf("Send() error = %v (%T), want a *FailoverError carrying both attempts", err, err)
	}
	var pp, pf *ProviderError
	if !errors.As(fe.Primary, &pp) || pp.Provider != "meteor" || pp.Status != 500 || pp.Code != "internal" {
		t.Fatalf("FailoverError.Primary = %v, want the meteor 500", fe.Primary)
	}
	if !errors.As(fe.Fallback, &pf) || pf.Provider != "pelican" || pf.Status != 502 || pf.Code != "gateway_error" {
		t.Fatalf("FailoverError.Fallback = %v, want the pelican 502", fe.Fallback)
	}
	for _, needle := range []string{"meteor", "pelican"} {
		if !strings.Contains(err.Error(), needle) {
			t.Fatalf("Error() = %q, want both provider names visible for the on-call page", err.Error())
		}
	}
	if mv.count() != 1 || pv.count() != 1 {
		t.Fatalf("requests: primary=%d fallback=%d, want exactly 1 each — no retry loops in here", mv.count(), pv.count())
	}
}

func TestFallback4xxAfterPrimary5xxIsStillACombinedFailure(t *testing.T) {
	msrv, _ := newVendor(t, respSpec{status: 500, body: `{"error":{"code":"internal","message":"boom"}}`})
	psrv, pv := newVendor(t, respSpec{status: 400, body: `{"errors":[{"reason":"missing_field","detail":"content is required"}]}`})
	s := NewFailover(NewMeteor(msrv.URL, client()), NewPelican(psrv.URL, client()))
	_, err := s.Send(context.Background(), msg("k-8"))
	var fe *FailoverError
	if !errors.As(err, &fe) {
		t.Fatalf("Send() error = %v (%T), want a *FailoverError once both providers were attempted", err, err)
	}
	var pf *ProviderError
	if !errors.As(fe.Fallback, &pf) || pf.Status != 400 || pf.Code != "missing_field" {
		t.Fatalf("FailoverError.Fallback = %v, want the pelican 400", fe.Fallback)
	}
	if pv.count() != 1 {
		t.Fatalf("fallback saw %d requests, want exactly 1 — a 4xx there ends the attempt, no third try", pv.count())
	}
}

func TestFailoverSkipsFallbackWhenContextIsAlreadyDead(t *testing.T) {
	msrv, mv := newVendor(t, respSpec{body: `{"message_id":"never","status":"queued"}`})
	psrv, pv := newVendor(t, respSpec{status: 202, body: `{"id":"never","accepted":true}`})
	s := NewFailover(NewMeteor(msrv.URL, client()), NewPelican(psrv.URL, client()))
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := s.Send(ctx, msg("k-9"))
	if err == nil {
		t.Fatal("Send() with a cancelled context returned nil error")
	}
	if mv.count() != 0 || pv.count() != 0 {
		t.Fatalf("requests: primary=%d fallback=%d, want 0 and 0 — a dead context must not produce sends", mv.count(), pv.count())
	}
}
