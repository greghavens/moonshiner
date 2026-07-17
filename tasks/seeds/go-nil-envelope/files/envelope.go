// Package envelope shapes JSON responses for the fulfillment API's
// order-intake endpoint.
package envelope

import "fmt"

// APIError is the structured error every layer of the service reports.
// Callers classify failures by Code (errors.As), never by string match.
type APIError struct {
	Code   int
	Reason string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("api error %d: %s", e.Code, e.Reason)
}

// Order is an incoming order payload after JSON decoding.
type Order struct {
	ID    string
	Qty   int
	Items []string
}

// Envelope is the wire format returned to clients.
type Envelope struct {
	Status string `json:"status"`
	Error  string `json:"error,omitempty"`
	Data   string `json:"data,omitempty"`
}

// checkOrder applies the intake validation rules in order.
func checkOrder(o Order) *APIError {
	switch {
	case o.ID == "":
		return &APIError{Code: 400, Reason: "missing order id"}
	case o.Qty <= 0:
		return &APIError{Code: 422, Reason: "quantity must be positive"}
	case len(o.Items) == 0:
		return &APIError{Code: 422, Reason: "order has no items"}
	}
	return nil
}

// validate adapts the rule table to the error interface used by the
// rest of the pipeline.
func validate(o Order) error {
	return checkOrder(o)
}

// Process validates the order and hands it to the store layer.
func Process(o Order, store func(Order) error) error {
	if err := validate(o); err != nil {
		return err
	}
	if err := store(o); err != nil {
		return fmt.Errorf("store order %s: %v", o.ID, err)
	}
	return nil
}

// Build renders the terminal envelope for a processed order.
func Build(o Order, err error) Envelope {
	if err != nil {
		return Envelope{Status: "error", Error: err.Error()}
	}
	return Envelope{Status: "accepted", Data: o.ID}
}

// Handle runs the full intake path and returns the wire envelope.
func Handle(o Order, store func(Order) error) Envelope {
	return Build(o, Process(o, store))
}
