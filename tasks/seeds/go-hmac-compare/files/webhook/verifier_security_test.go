package webhook

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

func signed(secret, body []byte) string {
	mac := hmac.New(sha256.New, secret)
	_, _ = mac.Write(body)
	return "sha256=" + hex.EncodeToString(mac.Sum(nil))
}

func TestVerifyUsesExactBodyBytesAndEveryActiveKey(t *testing.T) {
	oldKey := []byte("old-key-kept-active-during-rotation")
	currentKey := []byte("current-production-signing-key")
	body := []byte("{\n  \"event\": \"invoice.paid\",\n  \"amount\": 1200\n}\n")
	verifier := NewVerifier(oldKey, currentKey)

	cases := []struct {
		name      string
		signature string
	}{
		{"old key", signed(oldKey, body)},
		{"current key", signed(currentKey, body)},
		{"uppercase hex", "sha256=" + strings.ToUpper(strings.TrimPrefix(signed(currentKey, body), "sha256="))},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := verifier.Verify(body, tc.signature); err != nil {
				t.Fatalf("valid signature was rejected: %v", err)
			}
		})
	}

	differentBytes := []byte(`{"amount":1200,"event":"invoice.paid"}`)
	if err := verifier.Verify(body, signed(currentKey, differentBytes)); err != ErrInvalidSignature {
		t.Fatalf("signature for re-encoded JSON must fail with ErrInvalidSignature, got %v", err)
	}
}

func TestVerifyRejectsEmptyKeysAndMalformedSignaturesUniformly(t *testing.T) {
	body := []byte(`{"event":"ping"}`)
	key := []byte("active-key")
	validHex := strings.Repeat("ab", sha256.Size)
	cases := []struct {
		name      string
		verifier  *Verifier
		signature string
	}{
		{"no keys", NewVerifier(), signed(key, body)},
		{"empty key", NewVerifier(nil, []byte{}), signed(nil, body)},
		{"missing", NewVerifier(key), ""},
		{"wrong scheme", NewVerifier(key), "SHA256=" + validHex},
		{"empty digest", NewVerifier(key), "sha256="},
		{"odd length", NewVerifier(key), "sha256=abc"},
		{"short digest", NewVerifier(key), "sha256=" + strings.Repeat("ab", sha256.Size-1)},
		{"long digest", NewVerifier(key), "sha256=" + strings.Repeat("ab", sha256.Size+1)},
		{"invalid hex", NewVerifier(key), "sha256=" + strings.Repeat("zz", sha256.Size)},
		{"trailing space", NewVerifier(key), "sha256=" + validHex + " "},
		{"multiple values", NewVerifier(key), "sha256=" + validHex + ",sha256=" + validHex},
		{"digest mismatch", NewVerifier(key), "sha256=" + validHex},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := tc.verifier.Verify(body, tc.signature); err != ErrInvalidSignature {
				t.Fatalf("want the sentinel ErrInvalidSignature, got %#v", err)
			}
		})
	}
}

func TestVerifierUsesHMACEqualAndDoesNotReturnFromKeyScan(t *testing.T) {
	source, err := os.ReadFile("verifier.go")
	if err != nil {
		t.Fatal(err)
	}
	f, err := parser.ParseFile(token.NewFileSet(), "verifier.go", source, 0)
	if err != nil {
		t.Fatal(err)
	}

	usesHMACEqual := false
	usesBytesEqual := false
	ast.Inspect(f, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		selector, ok := call.Fun.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		pkg, ok := selector.X.(*ast.Ident)
		if !ok || selector.Sel.Name != "Equal" {
			return true
		}
		switch pkg.Name {
		case "hmac":
			usesHMACEqual = true
		case "bytes":
			usesBytesEqual = true
		}
		return true
	})
	if !usesHMACEqual {
		t.Error("signature digest comparisons must call hmac.Equal")
	}
	if usesBytesEqual {
		t.Error("bytes.Equal is not appropriate for signature digest comparisons")
	}

	var verify *ast.FuncDecl
	for _, decl := range f.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if ok && fn.Name.Name == "Verify" {
			verify = fn
			break
		}
	}
	if verify == nil {
		t.Fatal("Verify method not found")
	}
	ast.Inspect(verify.Body, func(n ast.Node) bool {
		switch loop := n.(type) {
		case *ast.RangeStmt:
			ast.Inspect(loop.Body, func(inner ast.Node) bool {
				if _, ok := inner.(*ast.ReturnStmt); ok {
					t.Error("Verify must scan every configured key instead of returning from the key loop")
				}
				return true
			})
			return false
		case *ast.ForStmt:
			ast.Inspect(loop.Body, func(inner ast.Node) bool {
				if _, ok := inner.(*ast.ReturnStmt); ok {
					t.Error("Verify must scan every configured key instead of returning from the key loop")
				}
				return true
			})
			return false
		default:
			return true
		}
	})
}

func TestHandlerReturnsOneGenericAuthenticationFailure(t *testing.T) {
	body := []byte("{ \"event\": \"ping\" }\n")
	key := []byte("active-key")
	next := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("unauthenticated request reached downstream handler")
		w.WriteHeader(http.StatusNoContent)
	})
	handler := NewHandler(NewVerifier(key), next)

	cases := []struct {
		name       string
		signatures []string
	}{
		{"missing", nil},
		{"malformed", []string{"sha256=not-hex"}},
		{"mismatch", []string{"sha256=" + strings.Repeat("00", sha256.Size)}},
		{"duplicate", []string{signed(key, body), signed(key, body)}},
	}
	var firstStatus int
	var firstBody string
	var firstContentType string
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodPost, "/webhooks", bytes.NewReader(body))
			for _, signature := range tc.signatures {
				request.Header.Add("X-Hub-Signature-256", signature)
			}
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)

			if response.Code != http.StatusUnauthorized {
				t.Fatalf("want 401, got %d", response.Code)
			}
			if firstStatus == 0 {
				firstStatus = response.Code
				firstBody = response.Body.String()
				firstContentType = response.Header().Get("Content-Type")
			}
			if response.Code != firstStatus ||
				response.Body.String() != firstBody ||
				response.Header().Get("Content-Type") != firstContentType {
				t.Fatalf("authentication failures are distinguishable: status=%d body=%q content-type=%q",
					response.Code, response.Body.String(), response.Header().Get("Content-Type"))
			}
			lowerBody := strings.ToLower(response.Body.String())
			for _, leaked := range []string{"hex", "decode", "format", "mismatch", "signature"} {
				if strings.Contains(lowerBody, leaked) {
					t.Errorf("response leaks verification detail %q: %q", leaked, response.Body.String())
				}
			}
		})
	}
}

func TestHandlerPreservesExactBodyAfterSuccessfulVerification(t *testing.T) {
	key := []byte("active-key")
	body := []byte("{\n\t\"event\": \"ping\"\n}\n")
	var received []byte
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var err error
		received, err = io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read downstream body: %v", err)
		}
		w.WriteHeader(http.StatusNoContent)
	})
	handler := NewHandler(NewVerifier(key), next)
	request := httptest.NewRequest(http.MethodPost, "/webhooks", bytes.NewReader(body))
	request.Header.Set("X-Hub-Signature-256", signed(key, body))
	response := httptest.NewRecorder()

	handler.ServeHTTP(response, request)

	if response.Code != http.StatusNoContent {
		t.Fatalf("want downstream status 204, got %d: %s", response.Code, response.Body.String())
	}
	if !bytes.Equal(received, body) {
		t.Fatalf("downstream body changed: got %q, want %q", received, body)
	}
}
