package webhook

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

var ErrInvalidSignature = errors.New("invalid webhook signature")

// Verifier validates webhook signatures against the service's active signing
// keys. More than one key may be active while a key rotation is in progress.
type Verifier struct {
	keys [][]byte
}

func NewVerifier(keys ...[]byte) *Verifier {
	return &Verifier{keys: keys}
}

func (v *Verifier) Verify(body []byte, signature string) error {
	if len(v.keys) == 0 {
		return errors.New("webhook signing key is not configured")
	}

	const prefix = "sha256="
	if !strings.HasPrefix(signature, prefix) {
		return fmt.Errorf("unsupported signature format %q", signature)
	}

	provided, err := hex.DecodeString(strings.TrimPrefix(signature, prefix))
	if err != nil {
		return fmt.Errorf("decode webhook signature: %w", err)
	}

	var value any
	if err := json.Unmarshal(body, &value); err != nil {
		return fmt.Errorf("decode webhook body: %w", err)
	}
	canonical, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("encode webhook body: %w", err)
	}

	mac := hmac.New(sha256.New, v.keys[0])
	_, _ = mac.Write(canonical)
	expected := mac.Sum(nil)
	if !bytes.Equal(provided, expected) {
		return fmt.Errorf("webhook signature mismatch: got %s", hex.EncodeToString(provided))
	}

	return nil
}
