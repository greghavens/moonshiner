// Acceptance tests for the REST Proxy v2 batch producer.
//
// Runs loopback fake REST Proxy instances implementing the v2 produce
// subset pinned in docs/contract.json. No vendor network, no real
// credentials.
package restproduce

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
)

const (
	username = "produce.bot"
	password = "dummy-cred-77ab21" // dummy; must never reach an untrusted host
)

var expectedAuth = "Basic " + base64.StdEncoding.EncodeToString(
	[]byte(username+":"+password))

func check(t *testing.T, cond bool, label string) {
	t.Helper()
	if !cond {
		t.Fatalf("FAILED: %s", label)
	}
}

type recordedRequest struct {
	method string
	path   string
	query  string
	header http.Header
	body   []byte
}

type fakeProxy struct {
	mu       sync.Mutex
	requests []recordedRequest
	plan     []func(http.ResponseWriter)
	srv      *httptest.Server
}

func newFakeProxy(t *testing.T) *fakeProxy {
	f := &fakeProxy{}
	f.srv = httptest.NewServer(http.HandlerFunc(
		func(w http.ResponseWriter, r *http.Request) {
			body, _ := io.ReadAll(r.Body)
			f.mu.Lock()
			f.requests = append(f.requests, recordedRequest{
				r.Method, r.URL.Path, r.URL.RawQuery,
				r.Header.Clone(), body})
			var step func(http.ResponseWriter)
			if len(f.plan) > 0 {
				step = f.plan[0]
				f.plan = f.plan[1:]
			}
			f.mu.Unlock()
			if step == nil {
				jsonResponse(404,
					`{"error_code":404,"message":"HTTP 404 Not Found"}`)(w)
				return
			}
			step(w)
		}))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeProxy) recorded() []recordedRequest {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]recordedRequest(nil), f.requests...)
}

func jsonResponse(status int, body string) func(http.ResponseWriter) {
	return func(w http.ResponseWriter) {
		w.Header().Set("Content-Type", "application/vnd.kafka.v2+json")
		w.WriteHeader(status)
		io.WriteString(w, body)
	}
}

func redirectTo(url string) func(http.ResponseWriter) {
	return func(w http.ResponseWriter) {
		w.Header().Set("Location", url)
		w.WriteHeader(307)
	}
}

func newTestClient(f *fakeProxy) *Client {
	return NewClient(f.srv.URL, &http.Client{}, username, password)
}

func raw(s string) json.RawMessage { return json.RawMessage(s) }

func int32p(v int32) *int32 { return &v }

func topKeys(t *testing.T, body []byte) map[string]json.RawMessage {
	t.Helper()
	var top map[string]json.RawMessage
	if err := json.Unmarshal(body, &top); err != nil {
		t.Fatalf("request body is not JSON: %v (%s)", err, body)
	}
	return top
}

func wireRecords(t *testing.T, body []byte) []map[string]json.RawMessage {
	t.Helper()
	top := topKeys(t, body)
	var recs []map[string]json.RawMessage
	if err := json.Unmarshal(top["records"], &recs); err != nil {
		t.Fatalf("records member missing or malformed: %v (%s)", err, body)
	}
	return recs
}

func TestKeyedJSONBatch(t *testing.T) {
	f := newFakeProxy(t)
	f.plan = append(f.plan, jsonResponse(200, `{
		"key_schema_id": null, "value_schema_id": null,
		"offsets": [
			{"partition": 0, "offset": 100, "error_code": null, "error": null},
			{"partition": 1, "offset": 7, "error_code": null, "error": null},
			{"partition": 0, "offset": 101, "error_code": null, "error": null}
		]}`))
	c := newTestClient(f)
	// value_schema deliberately set: the json embedded format takes no
	// schema fields, so it must never reach the wire.
	req := BatchRequest{Format: FormatJSON, ValueSchema: "junk-should-not-appear"}
	records := []Record{
		{Key: raw(`"k0"`), Value: raw(`{"a":1}`), Partition: int32p(0)},
		{Value: raw(`{"b":2}`)},
		{Key: raw(`"k2"`), Partition: int32p(0)},
	}
	result, err := c.Produce(context.Background(), "orders.v1", req, records)
	check(t, err == nil, "produce succeeds")
	reqs := f.recorded()
	check(t, len(reqs) == 1, "exactly one produce request")
	r0 := reqs[0]
	check(t, r0.method == "POST", "produce uses POST")
	check(t, r0.path == "/topics/orders.v1", "produce path is /topics/{topic}")
	check(t, r0.header.Get("Content-Type") ==
		"application/vnd.kafka.json.v2+json",
		"json embedded format Content-Type")
	check(t, r0.header.Get("Accept") == "application/vnd.kafka.v2+json",
		"Accept is the v2 API media type")
	check(t, r0.header.Get("Authorization") == expectedAuth,
		"Basic auth header present")

	top := topKeys(t, r0.body)
	check(t, len(top) == 1, "json format body carries only records")
	_, hasKS := top["key_schema"]
	_, hasVS := top["value_schema"]
	check(t, !hasKS && !hasVS, "no schema fields for the json format")

	recs := wireRecords(t, r0.body)
	check(t, len(recs) == 3, "three records on the wire")
	check(t, string(recs[0]["key"]) == `"k0"`, "record 0 key preserved")
	check(t, string(recs[0]["value"]) == `{"a":1}`, "record 0 value preserved")
	check(t, string(recs[0]["partition"]) == `0`, "record 0 partition pinned")
	_, k1 := recs[1]["key"]
	_, p1 := recs[1]["partition"]
	check(t, !k1, "null key omitted from the wire record")
	check(t, !p1, "unset partition omitted from the wire record")
	_, v2 := recs[2]["value"]
	check(t, !v2, "null value (tombstone) omitted from the wire record")

	check(t, len(result.Results) == 3, "one result per record")
	check(t, result.Results[0].Succeeded(), "record 0 succeeded")
	check(t, *result.Results[0].Offset == 100 &&
		*result.Results[0].Partition == 0, "record 0 offset/partition decoded")
	check(t, *result.Results[1].Offset == 7 &&
		*result.Results[1].Partition == 1, "record 1 offset/partition decoded")
	check(t, result.KeySchemaID == nil && result.ValueSchemaID == nil,
		"null schema ids decode to nil")
}

func TestAvroNullKeysOmitKeySchema(t *testing.T) {
	f := newFakeProxy(t)
	f.plan = append(f.plan, jsonResponse(200, `{
		"key_schema_id": null, "value_schema_id": 21,
		"offsets": [
			{"partition": 0, "offset": 0, "error_code": null, "error": null},
			{"partition": 0, "offset": 1, "error_code": null, "error": null}
		]}`))
	c := newTestClient(f)
	valueSchema := `{"type": "record", "name": "User", "fields":` +
		` [{"name": "name", "type": "string"}]}`
	req := BatchRequest{
		Format: FormatAvro,
		// Caller sloppily left a key schema behind; every key below is
		// null, so the documented contract says it must be excluded.
		KeySchema:   `{"name":"user_id","type":"int"}`,
		ValueSchema: valueSchema,
	}
	records := []Record{
		{Value: raw(`{"name": "alice"}`)},
		{Value: raw(`{"name": "bob"}`)},
	}
	result, err := c.Produce(context.Background(), "avrotest", req, records)
	check(t, err == nil, "avro produce succeeds")
	r0 := f.recorded()[0]
	check(t, r0.header.Get("Content-Type") ==
		"application/vnd.kafka.avro.v2+json", "avro Content-Type")
	top := topKeys(t, r0.body)
	_, hasKS := top["key_schema"]
	_, hasKSID := top["key_schema_id"]
	check(t, !hasKS && !hasKSID,
		"key schema fields excluded when every key is null")
	var vs string
	check(t, json.Unmarshal(top["value_schema"], &vs) == nil &&
		vs == valueSchema, "value_schema sent as the full schema string")
	check(t, result.ValueSchemaID != nil && *result.ValueSchemaID == 21,
		"registered value schema id surfaced")
	check(t, result.KeySchemaID == nil, "no key schema id when keys are null")
}

func TestKeyedAvroSendsBothSchemas(t *testing.T) {
	f := newFakeProxy(t)
	f.plan = append(f.plan, jsonResponse(200, `{
		"key_schema_id": 11, "value_schema_id": 21,
		"offsets": [
			{"partition": 2, "offset": 5, "error_code": null, "error": null}
		]}`))
	c := newTestClient(f)
	req := BatchRequest{
		Format:      FormatAvro,
		KeySchema:   `{"name":"user_id","type":"int"}`,
		ValueSchema: `{"type":"string"}`,
	}
	records := []Record{{Key: raw(`1`), Value: raw(`"v"`), Partition: int32p(2)}}
	result, err := c.Produce(context.Background(), "avrokeytest", req, records)
	check(t, err == nil, "keyed avro produce succeeds")
	top := topKeys(t, f.recorded()[0].body)
	_, hasKS := top["key_schema"]
	_, hasVS := top["value_schema"]
	check(t, hasKS && hasVS, "keyed avro batch carries both schemas")
	check(t, result.KeySchemaID != nil && *result.KeySchemaID == 11,
		"key schema id surfaced")
}

func TestPartialFailurePreserved(t *testing.T) {
	f := newFakeProxy(t)
	f.plan = append(f.plan, jsonResponse(200, `{
		"key_schema_id": null, "value_schema_id": null,
		"offsets": [
			{"partition": 0, "offset": 40, "error_code": null, "error": null},
			{"partition": null, "offset": null, "error_code": 2,
			 "error": "Retriable Kafka exception: NotEnoughReplicas"},
			{"partition": null, "offset": null, "error_code": 1,
			 "error": "Non-retriable Kafka exception: RecordTooLarge"}
		]}`))
	c := newTestClient(f)
	records := []Record{
		{Key: raw(`"a"`), Value: raw(`1`)},
		{Key: raw(`"b"`), Value: raw(`2`)},
		{Key: raw(`"c"`), Value: raw(`3`)},
	}
	result, err := c.Produce(context.Background(), "orders.v1",
		BatchRequest{Format: FormatJSON}, records)
	check(t, err == nil, "HTTP 200 with per-record failures is not a Go error")
	check(t, result.Results[0].Succeeded(), "record 0 fine")
	check(t, !result.Results[1].Succeeded() && result.Results[1].Retriable(),
		"error_code 2 is a retriable per-record failure")
	check(t, !result.Results[2].Succeeded() && !result.Results[2].Retriable(),
		"error_code 1 is a non-retriable per-record failure")
	check(t, result.Results[1].Error ==
		"Retriable Kafka exception: NotEnoughReplicas",
		"per-record error text preserved")
	check(t, result.Results[1].Offset == nil &&
		result.Results[1].Partition == nil,
		"failed record has no offset/partition")
}

func TestRetryResendsOnlyRetriableRecords(t *testing.T) {
	f := newFakeProxy(t)
	f.plan = append(f.plan,
		jsonResponse(200, `{
			"key_schema_id": null, "value_schema_id": null,
			"offsets": [
				{"partition": 0, "offset": 40, "error_code": null, "error": null},
				{"partition": null, "offset": null, "error_code": 2,
				 "error": "Retriable Kafka exception"},
				{"partition": null, "offset": null, "error_code": 1,
				 "error": "Non-retriable Kafka exception"}
			]}`),
		jsonResponse(200, `{
			"key_schema_id": null, "value_schema_id": null,
			"offsets": [
				{"partition": 1, "offset": 41, "error_code": null, "error": null}
			]}`))
	c := newTestClient(f)
	records := []Record{
		{Key: raw(`"a"`), Value: raw(`1`)},
		{Key: raw(`"b"`), Value: raw(`2`)},
		{Key: raw(`"c"`), Value: raw(`3`)},
	}
	var waits []int
	result, err := c.ProduceWithRetry(context.Background(), "orders.v1",
		BatchRequest{Format: FormatJSON}, records, 3,
		func(attempt int) { waits = append(waits, attempt) })
	check(t, err == nil, "retry produce succeeds")
	reqs := f.recorded()
	check(t, len(reqs) == 2, "two attempts total")
	retryRecs := wireRecords(t, reqs[1].body)
	check(t, len(retryRecs) == 1, "retry carries only the retriable record")
	check(t, string(retryRecs[0]["key"]) == `"b"` &&
		string(retryRecs[0]["value"]) == `2`,
		"retried record payload preserved exactly")
	check(t, len(waits) == 1 && waits[0] == 1,
		"backoff invoked once, before attempt 2")
	check(t, result.Results[0].Succeeded() && *result.Results[0].Offset == 40,
		"attempt-1 success kept, never re-sent")
	check(t, result.Results[1].Succeeded() && *result.Results[1].Offset == 41 &&
		*result.Results[1].Partition == 1,
		"retried record outcome merged into its original slot")
	check(t, !result.Results[2].Succeeded() && !result.Results[2].Retriable(),
		"non-retriable failure preserved, never re-sent")
}

func TestRetryExhaustion(t *testing.T) {
	f := newFakeProxy(t)
	failing := `{
		"key_schema_id": null, "value_schema_id": null,
		"offsets": [
			{"partition": null, "offset": null, "error_code": 2,
			 "error": "Retriable Kafka exception"}
		]}`
	f.plan = append(f.plan, jsonResponse(200, failing),
		jsonResponse(200, failing), jsonResponse(200, failing))
	c := newTestClient(f)
	var waits []int
	result, err := c.ProduceWithRetry(context.Background(), "orders.v1",
		BatchRequest{Format: FormatJSON},
		[]Record{{Key: raw(`"a"`), Value: raw(`1`)}}, 3,
		func(attempt int) { waits = append(waits, attempt) })
	check(t, err == nil, "exhaustion is reported per record, not as a Go error")
	check(t, len(f.recorded()) == 3, "maxAttempts bounds the attempt count")
	check(t, len(waits) == 2, "backoff between attempts only")
	check(t, result.Results[0].Retriable(),
		"record still marked retriable after exhaustion")
}

func TestTopicNotFound(t *testing.T) {
	f := newFakeProxy(t)
	f.plan = append(f.plan, jsonResponse(404,
		`{"error_code":40401,"message":"Topic not found."}`))
	c := newTestClient(f)
	result, err := c.Produce(context.Background(), "nope",
		BatchRequest{Format: FormatJSON},
		[]Record{{Value: raw(`1`)}})
	check(t, result == nil, "no result on a request-level error")
	var apiErr *APIError
	check(t, errors.As(err, &apiErr), "request-level failures are *APIError")
	check(t, apiErr.HTTPStatus == 404, "APIError carries the HTTP status")
	check(t, apiErr.ErrorCode == 40401, "APIError carries error_code 40401")
	check(t, apiErr.Message == "Topic not found.", "APIError carries message")
}

func TestMissingValueSchemaSurfaced(t *testing.T) {
	f := newFakeProxy(t)
	f.plan = append(f.plan, jsonResponse(422,
		`{"error_code":42202,"message":"Request includes avro records but does not include value_schema or value_schema_id"}`))
	c := newTestClient(f)
	_, err := c.Produce(context.Background(), "avrotest",
		BatchRequest{Format: FormatAvro},
		[]Record{{Value: raw(`{"name":"x"}`)}})
	var apiErr *APIError
	check(t, errors.As(err, &apiErr), "422 becomes *APIError")
	check(t, apiErr.HTTPStatus == 422 && apiErr.ErrorCode == 42202,
		"missing value_schema error code preserved")
}

func TestRedirectRefused(t *testing.T) {
	evil := newFakeProxy(t)
	f := newFakeProxy(t)
	f.plan = append(f.plan, redirectTo(evil.srv.URL+"/topics/orders.v1"))
	c := newTestClient(f)
	result, err := c.Produce(context.Background(), "orders.v1",
		BatchRequest{Format: FormatJSON},
		[]Record{{Value: raw(`1`)}})
	check(t, result == nil, "redirect yields no result")
	var apiErr *APIError
	check(t, errors.As(err, &apiErr), "redirect is a request-level error")
	check(t, apiErr.HTTPStatus == 307, "redirect status surfaced, not followed")
	check(t, len(evil.recorded()) == 0,
		"credentials never follow a redirect to another host")
}

func TestCancellationBetweenAttempts(t *testing.T) {
	f := newFakeProxy(t)
	failing := `{
		"key_schema_id": null, "value_schema_id": null,
		"offsets": [
			{"partition": null, "offset": null, "error_code": 2,
			 "error": "Retriable Kafka exception"}
		]}`
	f.plan = append(f.plan, jsonResponse(200, failing),
		jsonResponse(200, failing), jsonResponse(200, failing))
	ctx, cancel := context.WithCancel(context.Background())
	c := newTestClient(f)
	result, err := c.ProduceWithRetry(ctx, "orders.v1",
		BatchRequest{Format: FormatJSON},
		[]Record{{Value: raw(`1`)}}, 5,
		func(attempt int) { cancel() })
	check(t, errors.Is(err, context.Canceled),
		"cancellation between attempts returns the context error")
	check(t, len(f.recorded()) == 1, "no further attempts after cancellation")
	check(t, result != nil && result.Results[0].Retriable(),
		"partial outcomes preserved on cancellation")
}

func TestEmptyBatchRejectedLocally(t *testing.T) {
	f := newFakeProxy(t)
	c := newTestClient(f)
	result, err := c.Produce(context.Background(), "orders.v1",
		BatchRequest{Format: FormatJSON}, nil)
	check(t, result == nil && err != nil, "empty batch rejected")
	check(t, len(f.recorded()) == 0, "empty batch never reaches the wire")
}
