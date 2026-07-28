package eventwire

import (
	"encoding/json"
	"fmt"
)

// Envelope is the stable wire wrapper shared by all generated event types.
type Envelope struct {
	Code    string          `json:"code"`
	Payload json.RawMessage `json:"payload"`
}

// Marshal wraps a generated event in its wire envelope.
func Marshal(event interface {
	EventCode() string
}) ([]byte, error) {
	payload, err := json.Marshal(event)
	if err != nil {
		return nil, fmt.Errorf("marshal event payload: %w", err)
	}
	encoded, err := json.Marshal(Envelope{Code: event.EventCode(), Payload: payload})
	if err != nil {
		return nil, fmt.Errorf("marshal event envelope: %w", err)
	}
	return encoded, nil
}
