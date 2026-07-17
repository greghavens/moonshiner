package envelope

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func validOrder() Order {
	return Order{ID: "ord-9", Qty: 2, Items: []string{"mug", "lid"}}
}

func TestProcessReturnsNilForValidOrder(t *testing.T) {
	err := Process(validOrder(), func(Order) error { return nil })
	if err != nil {
		t.Fatalf("Process returned an error for a valid order")
	}
}

func TestValidOrderAccepted(t *testing.T) {
	var stored []string
	store := func(o Order) error {
		stored = append(stored, o.ID)
		return nil
	}
	env := Handle(validOrder(), store)
	if env.Status != "accepted" {
		t.Fatalf("Status = %q, want %q", env.Status, "accepted")
	}
	if env.Error != "" {
		t.Fatalf("Error = %q, want empty", env.Error)
	}
	if env.Data != "ord-9" {
		t.Fatalf("Data = %q, want %q", env.Data, "ord-9")
	}
	if len(stored) != 1 || stored[0] != "ord-9" {
		t.Fatalf("store calls = %v, want exactly one for ord-9", stored)
	}
}

func TestInvalidOrderEnvelope(t *testing.T) {
	calls := 0
	store := func(Order) error { calls++; return nil }
	env := Handle(Order{Qty: 1, Items: []string{"mug"}}, store)
	if env.Status != "error" {
		t.Fatalf("Status = %q, want %q", env.Status, "error")
	}
	if env.Error != "api error 400: missing order id" {
		t.Fatalf("Error = %q, want %q", env.Error, "api error 400: missing order id")
	}
	if calls != 0 {
		t.Fatalf("store was called %d times for a rejected order", calls)
	}
}

func TestValidationRulesReportConcreteErrors(t *testing.T) {
	cases := []struct {
		name string
		o    Order
		code int
	}{
		{"missing id", Order{Qty: 1, Items: []string{"mug"}}, 400},
		{"zero quantity", Order{ID: "ord-1", Qty: 0, Items: []string{"mug"}}, 422},
		{"no items", Order{ID: "ord-2", Qty: 3}, 422},
	}
	for _, tc := range cases {
		err := Process(tc.o, func(Order) error { return nil })
		if err == nil {
			t.Fatalf("%s: Process accepted a bad order", tc.name)
		}
		var apiErr *APIError
		if !errors.As(err, &apiErr) {
			t.Fatalf("%s: error %v does not carry an *APIError", tc.name, err)
		}
		if apiErr.Code != tc.code {
			t.Fatalf("%s: code = %d, want %d", tc.name, apiErr.Code, tc.code)
		}
	}
}

func TestStoreFailureKeepsIdentity(t *testing.T) {
	sentinel := &APIError{Code: 503, Reason: "warehouse offline"}
	store := func(Order) error { return sentinel }
	o := validOrder()
	o.ID = "ord-7"
	err := Process(o, store)
	if err == nil {
		t.Fatal("Process ignored a store failure")
	}
	if !strings.HasPrefix(err.Error(), "store order ord-7: ") {
		t.Fatalf("message = %q, want the store-context prefix", err.Error())
	}
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("APIError identity lost through the store path: %v", err)
	}
	if apiErr.Code != 503 {
		t.Fatalf("code = %d, want 503", apiErr.Code)
	}
}

func TestEnvelopeJSON(t *testing.T) {
	ok, err := json.Marshal(Handle(validOrder(), func(Order) error { return nil }))
	if err != nil {
		t.Fatalf("marshal accepted envelope: %v", err)
	}
	if string(ok) != `{"status":"accepted","data":"ord-9"}` {
		t.Fatalf("accepted JSON = %s", ok)
	}
	bad, err := json.Marshal(Handle(Order{Qty: 1, Items: []string{"mug"}}, func(Order) error { return nil }))
	if err != nil {
		t.Fatalf("marshal rejected envelope: %v", err)
	}
	if string(bad) != `{"status":"error","error":"api error 400: missing order id"}` {
		t.Fatalf("rejected JSON = %s", bad)
	}
}
