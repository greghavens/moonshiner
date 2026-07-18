package clientseam_test

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"go-generated-client-seam/internal/fleetcompat"
	"go-generated-client-seam/internal/generated/fleetapi"
	"go-generated-client-seam/internal/operatorcli"
	"go-generated-client-seam/internal/scheduler"
)

type result struct {
	node fleetapi.Node
	err  error
}

type scriptedClient struct {
	results []result
	calls   []string
}

var _ fleetapi.Client = (*scriptedClient)(nil)

func (c *scriptedClient) GetNode(_ context.Context, id string) (fleetapi.Node, error) {
	c.calls = append(c.calls, id)
	if len(c.calls) > len(c.results) {
		panic("unexpected generated client call")
	}
	return c.results[len(c.calls)-1].node, c.results[len(c.calls)-1].err
}

func busy(delay int) *fleetapi.Problem {
	return &fleetapi.Problem{
		Status: 503, Code: "fleet_busy", Message: "control plane warming",
		RetryAfterMillis: delay,
	}
}

func TestSchedulerGetsTypedBusyCompatibilityThroughTheSeam(t *testing.T) {
	raw := &scriptedClient{results: []result{
		{err: busy(25)},
		{node: fleetapi.Node{ID: "node-7", Name: "render-7", Endpoint: "http://node-7.test"}},
	}}
	var delays []time.Duration
	client := fleetcompat.New(raw, 3, func(delay time.Duration) { delays = append(delays, delay) })
	got, err := scheduler.New(client).RouteJob(context.Background(), "node-7", "job-41")
	if err != nil {
		t.Fatal(err)
	}
	if got != "http://node-7.test/jobs/job-41" {
		t.Fatalf("route = %q", got)
	}
	if !reflect.DeepEqual(raw.calls, []string{"node-7", "node-7"}) ||
		!reflect.DeepEqual(delays, []time.Duration{25 * time.Millisecond}) {
		t.Fatalf("calls=%v delays=%v", raw.calls, delays)
	}
}

func TestOperatorCLIUsesTheSameTypedCompatibility(t *testing.T) {
	raw := &scriptedClient{results: []result{
		{err: busy(40)},
		{node: fleetapi.Node{ID: "node-2", Name: "batch-2", Endpoint: "http://node-2.test"}},
	}}
	var delays []time.Duration
	client := fleetcompat.New(raw, 2, func(delay time.Duration) { delays = append(delays, delay) })
	got, err := operatorcli.DescribeNode(context.Background(), client, "node-2")
	if err != nil {
		t.Fatal(err)
	}
	if got != "node-2\tbatch-2\thttp://node-2.test" {
		t.Fatalf("description = %q", got)
	}
	if !reflect.DeepEqual(delays, []time.Duration{40 * time.Millisecond}) {
		t.Fatalf("delays = %v", delays)
	}
}

func TestLegacyBusyResponseStillRetries(t *testing.T) {
	raw := &scriptedClient{results: []result{
		{err: fleetapi.ErrServiceBusy},
		{node: fleetapi.Node{ID: "legacy-node"}},
	}}
	var delays []time.Duration
	node, err := fleetcompat.New(raw, 2, func(d time.Duration) { delays = append(delays, d) }).
		Lookup(context.Background(), "legacy-node")
	if err != nil || node.ID != "legacy-node" {
		t.Fatalf("node=%+v err=%v", node, err)
	}
	if !reflect.DeepEqual(delays, []time.Duration{10 * time.Millisecond}) {
		t.Fatalf("legacy delays = %v", delays)
	}
}

func TestTypedBusyExhaustionKeepsStableAndGeneratedErrors(t *testing.T) {
	last := busy(15)
	raw := &scriptedClient{results: []result{{err: busy(5)}, {err: busy(10)}, {err: last}}}
	var delays []time.Duration
	_, err := fleetcompat.New(raw, 3, func(d time.Duration) { delays = append(delays, d) }).
		Lookup(context.Background(), "node-stuck")
	if !errors.Is(err, fleetcompat.ErrUnavailable) {
		t.Fatalf("error = %v, want ErrUnavailable", err)
	}
	var gotProblem *fleetapi.Problem
	if !errors.As(err, &gotProblem) || gotProblem != last {
		t.Fatalf("generated cause = %#v, want final problem %#v", gotProblem, last)
	}
	want := []time.Duration{5 * time.Millisecond, 10 * time.Millisecond}
	if !reflect.DeepEqual(delays, want) || len(raw.calls) != 3 {
		t.Fatalf("calls=%d delays=%v, want 3 and %v", len(raw.calls), delays, want)
	}
}

func TestNotFoundTranslationRemainsStableAndCausal(t *testing.T) {
	problem := &fleetapi.Problem{Status: 404, Code: "node_missing", Message: "gone"}
	raw := &scriptedClient{results: []result{{err: problem}}}
	var delays []time.Duration
	_, err := fleetcompat.New(raw, 3, func(d time.Duration) { delays = append(delays, d) }).
		Lookup(context.Background(), "missing")
	if !errors.Is(err, fleetcompat.ErrNotFound) {
		t.Fatalf("error = %v, want ErrNotFound", err)
	}
	var got *fleetapi.Problem
	if !errors.As(err, &got) || got != problem || len(delays) != 0 || len(raw.calls) != 1 {
		t.Fatalf("cause=%#v calls=%d delays=%v", got, len(raw.calls), delays)
	}
}

func TestUnrelated503AndCancellationAreNotRetried(t *testing.T) {
	tests := []struct {
		name string
		err  error
	}{
		{"different-code", &fleetapi.Problem{Status: 503, Code: "maintenance", Message: "planned"}},
		{"cancelled", context.Canceled},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			raw := &scriptedClient{results: []result{{err: tc.err}}}
			var delays []time.Duration
			_, err := fleetcompat.New(raw, 4, func(d time.Duration) { delays = append(delays, d) }).
				Lookup(context.Background(), "node-1")
			if !errors.Is(err, tc.err) || len(raw.calls) != 1 || len(delays) != 0 {
				t.Fatalf("err=%v calls=%d delays=%v", err, len(raw.calls), delays)
			}
		})
	}
}
