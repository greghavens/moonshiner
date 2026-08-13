// Package mock is a loopback stand-in for a VCF Automation appliance, pinned
// to docs/contract.json.
//
// The mock is not a general HTTP fixture. It is driven from the contract and
// serves *only* what the contract names: a request whose method and path match
// no contract operation is a 404, a query parameter the operation does not
// declare is a 400, and a body field the operation does not declare is a 400.
// That is what makes it useful — a client that drifts from the documented wire
// shape fails against the mock instead of failing in production.
//
// It also keeps a log of every request it received, decoded, so that tests can
// assert on the exact bytes the client put on the wire. Every request is
// logged, including the ones it rejects.
//
// # Request handling order
//
// The server applies these steps in this order, and replies as soon as one of
// them fails:
//
//  1. Route. Match the request method and path against the contract's
//     operations, treating {placeholder} segments as wildcards. No match is
//     404 — including a known path reached with a method the contract does not
//     document for it, and including the token operation addressed to a tenant
//     other than Options.Tenant.
//  2. Validate. Reject a query parameter the matched operation does not
//     declare, or a body field it does not declare, with 400.
//  3. Authenticate. Every operation except the token operation requires
//     Authorization: Bearer <access token>. A missing, unrecognised or expired
//     token is 401.
//  4. Serve.
//
// Validating before authenticating is deliberate: it lets a test confirm that
// the contract is being enforced without first having to hold a token.
//
// Every exported symbol here has a fixed signature. The bodies are yours.
package mock

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/url"

	"vcfauto/contract"
)

var errNotImplemented = errors.New("mock: not implemented")

// Options configures a Server.
type Options struct {
	// Contract is the loaded docs/contract.json. The server refuses to
	// serve anything it does not name.
	Contract *contract.Contract

	// Tenant is the organization the token operation is scoped to. A token
	// request for any other tenant is a 404.
	Tenant string

	// APIToken is the long-lived API token the server accepts in exchange
	// for an access token.
	APIToken string

	// Deployments and CatalogItems are the corpora the list and get
	// operations page over and look up. See Fixtures.
	Deployments  []map[string]any
	CatalogItems []map[string]any

	// ExpireAccessTokenAfter makes an issued access token stop being
	// accepted once it has authorized this many requests; the next request
	// bearing it gets a 401. Exchanging the API token again issues a fresh
	// access token and resets the count. Zero means tokens never expire.
	//
	// The count is deliberately request-based rather than clock-based so
	// that expiry lands in exactly the same place on every run.
	ExpireAccessTokenAfter int

	// AccessTokenTTLSeconds is reported as the token response's expires_in.
	// Defaults to 3600. It does not by itself cause expiry; see
	// ExpireAccessTokenAfter.
	AccessTokenTTLSeconds int
}

// Server is a running loopback mock.
type Server struct {
	// Unexported state is yours. Requests() is called concurrently with
	// requests being served, so guard the log.
}

// Start validates opts and starts a server listening on 127.0.0.1 on an
// ephemeral port. Call Close when done.
//
// What the operations serve:
//
//   - The token operation takes a form-encoded body. The grant type and the
//     presented token must be the ones the contract names, and the token must
//     equal Options.APIToken; otherwise 400. On success it issues a fresh
//     access token and replies with the response fields the contract declares,
//     reporting AccessTokenTTLSeconds as the lifetime.
//   - The list operations page over their corpus using the page and size
//     parameters the contract declares, falling back to the defaults the
//     contract records when the client omits them, and reply with the page
//     envelope the contract declares.
//   - The deployment get operation looks its ID up in the corpus, 404 if absent.
//   - The catalog request operation replies with one entry per requested
//     instance, echoing the requested deployment name.
func Start(opts Options) (*Server, error) {
	return nil, errNotImplemented
}

// URL is the server's base URL, with no trailing slash.
func (s *Server) URL() string { return "" }

// Close shuts the server down.
func (s *Server) Close() {}

// RecordedRequest is one request as the server received it.
type RecordedRequest struct {
	// Seq is the 0-based order in which the server received the request.
	Seq int

	// Operation is the contract operation ID this request matched, or ""
	// if it matched none.
	Operation string

	Method string

	// Path is the request path, with path parameters still substituted —
	// i.e. what the client actually sent, not the template.
	Path string

	// Query is the parsed query string, exactly as sent. A parameter the
	// client omitted is absent from this map; a parameter the client sent
	// empty is present with an empty value. The difference is the point.
	Query url.Values

	// Header is the request header.
	Header http.Header

	// Body is the raw request body, unmodified.
	Body []byte

	// Status is the status code the server replied with.
	Status int
}

// JSONBody decodes the recorded body as a JSON object.
func (r RecordedRequest) JSONBody() (map[string]any, error) {
	var m map[string]any
	if err := json.Unmarshal(r.Body, &m); err != nil {
		return nil, err
	}
	return m, nil
}

// FormBody decodes the recorded body as form-encoded values.
func (r RecordedRequest) FormBody() (url.Values, error) {
	return url.ParseQuery(string(r.Body))
}

// Requests returns a copy of the request log, in the order received. It is
// safe to call while the server is serving.
func (s *Server) Requests() []RecordedRequest { return nil }

// RequestsFor returns the logged requests that matched the given operation ID.
func (s *Server) RequestsFor(operation string) []RecordedRequest {
	var out []RecordedRequest
	for _, r := range s.Requests() {
		if r.Operation == operation {
			out = append(out, r)
		}
	}
	return out
}
