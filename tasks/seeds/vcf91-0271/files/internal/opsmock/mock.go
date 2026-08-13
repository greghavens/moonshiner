// Package opsmock is a loopback stand-in for a VMware Cloud Foundation
// Operations 9.1 appliance. It serves only the four operations named in
// docs/contract.json and records every request it receives so that a test can
// assert the exact wire shape a client produced.
//
// It listens on 127.0.0.1 only. No VMware endpoint is contacted.
package opsmock

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync"
)

// BasePath is contract.api.basePath.
const BasePath = "/suite-api"

// Routes served by this mock, one per contract operation. Anything else is not
// served. PathAssignPolicy is a prefix route: the {id} path parameter and the
// trailing /assign segment are checked by the handler.
const (
	PathAcquireToken      = BasePath + "/api/auth/token/acquire"  // acquireToken
	PathCustomGroups      = BasePath + "/api/resources/groups"    // createCustomGroup
	PathPolicies          = BasePath + "/api/policies/"           // assignPolicy
	PathNotificationRules = BasePath + "/api/notifications/rules" // createNotificationPluginRule
)

// AssignPolicyPath builds the assignPolicy path for a policy id.
func AssignPolicyPath(policyID string) string { return PathPolicies + policyID + "/assign" }

// TokenPrefix is contract.clientRules.transport.authorizationHeader.valuePrefix.
const TokenPrefix = "vRealizeOpsToken "

// Values the mock hands out or accepts. They are fixed so a test can assert an
// exact wire shape.
const (
	IssuedToken    = "a4f0c9d1-73ea-4d18-9d5e-6b1c02f4a7d8::5eeded"
	TokenExpiresAt = "Wednesday, May 13, 2026 08:00:00 AM UTC"

	ValidUsername   = "svc-ops-admin"
	ValidPassword   = "Rq4-still-lantern-88"
	ValidAuthSource = "Imported LDAP Server"

	// AssignedGroupID is the custom-group.id createCustomGroup assigns.
	AssignedGroupID = "9613f1e4-6b93-4d9d-ba82-09beb46d75a6"
	// AssignedRuleID is the notification-rule.id createNotificationPluginRule assigns.
	AssignedRuleID = "f0a1d6b2-4c8e-4a37-b5d9-71e3c8a04b62"

	// KnownPolicyID is the only policy id assignPolicy accepts.
	KnownPolicyID = "ed050fc6-5136-46bb-93b4-6e2597296974"
	// KnownAdapterKind and KnownResourceKind are the only resource-key kinds
	// createCustomGroup accepts.
	KnownAdapterKind  = "Container"
	KnownResourceKind = "Environment"
	// KnownPluginID is the only notification plugin instance the appliance has.
	KnownPluginID = "8e9b3d17-2c40-4f6a-9e51-a7bd0c62f3aa"
	// KnownAlertDefinitionID is the only alert definition the appliance knows.
	KnownAlertDefinitionID = "AlertDefinition-VMWARE-VirtualMachine-CpuContention"
	// KnownResourceID is the only resource id assignPolicy accepts.
	KnownResourceID = "529c2a31-a993-430f-ae30-e467d04f8d6e"
)

// Property sets declared by the request schemas the contract names. A property
// outside these sets is rejected: it is not part of the schema.
var (
	usernamePasswordProps = set("username", "password", "authSource")
	customGroupProps      = set("resourceKey", "membershipDefinition", "autoResolveMembership", "id", "links", "policy")
	resourceKeyProps      = set("name", "adapterKindKey", "resourceKindKey", "extension", "links", "resourceIdentifiers")
	membershipProps       = set("includedResources", "excludedResources", "custom-group-properties", "rules")
	assignmentParamProps  = set("groupIds", "resourceAssignments")
	resourceAssignProps   = set("resourceId", "depth")
	notificationRuleProps = set(
		"name", "pluginId", "enabled", "templateId", "ruleType", "sendHeartbeat",
		"alertControlStates", "alertStatuses", "criticalities", "actionStatuses",
		"alertDefinitionIdFilters", "alertImpactFilters", "alertTypeFilters",
		"collectorGroupId", "collectorUUId", "properties",
		"resourceFilter", "resourceFilters", "resourceKindFilter", "resourceKindFilters",
		"id", "links",
	)
)

func set(keys ...string) map[string]bool {
	m := make(map[string]bool, len(keys))
	for _, k := range keys {
		m[k] = true
	}
	return m
}

// Request is one recorded inbound request.
type Request struct {
	Seq      int
	Method   string
	Path     string
	RawQuery string
	// OperationID is the contract operation the route belongs to, or "" for a
	// route the contract does not name.
	OperationID string
	Accept      string
	ContentType string
	// AuthorizationPresent distinguishes an absent header from an empty one.
	AuthorizationPresent bool
	Authorization        string
	Body                 []byte
	// BodyKeys is the sorted set of top-level JSON object keys in Body, or nil
	// when Body is empty or is not a JSON object.
	BodyKeys []string
	Status   int
	// Rejection is the mock's reason for a 4xx, empty on success.
	Rejection string
}

// Server is a loopback VCF Operations mock.
type Server struct {
	http *httptest.Server

	mu  sync.Mutex
	log []Request
}

// New starts a mock. The caller owns Close.
func New() *Server {
	s := &Server{}
	mux := http.NewServeMux()
	mux.HandleFunc(PathAcquireToken, s.handleAcquireToken)
	mux.HandleFunc(PathCustomGroups, s.handleCustomGroups)
	mux.HandleFunc(PathPolicies, s.handlePolicies)
	mux.HandleFunc(PathNotificationRules, s.handleNotificationRules)
	mux.HandleFunc("/", s.handleUnserved)
	s.http = httptest.NewServer(mux)
	return s
}

// URL is the base URL of the appliance, including the contract base path. A
// client should treat it as the value it would otherwise build from the
// appliance FQDN.
func (s *Server) URL() string { return s.http.URL + BasePath }

// Client returns an HTTP client configured to reach this mock.
func (s *Server) Client() *http.Client { return s.http.Client() }

// Close shuts the mock down.
func (s *Server) Close() { s.http.Close() }

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.log))
	copy(out, s.log)
	return out
}

// Reset clears the request log.
func (s *Server) Reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = nil
}

func (s *Server) record(rec Request) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	rec.Seq = len(s.log)
	s.log = append(s.log, rec)
	return rec.Seq
}

func (s *Server) finish(seq, status int, rejection string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log[seq].Status = status
	s.log[seq].Rejection = rejection
}

func capture(r *http.Request, operationID string, body []byte) Request {
	auth, authOK := r.Header["Authorization"]
	rec := Request{
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		OperationID: operationID,
		Accept:      r.Header.Get("Accept"),
		ContentType: r.Header.Get("Content-Type"),
		Body:        body,
	}
	if authOK {
		rec.AuthorizationPresent = true
		rec.Authorization = auth[0]
	}
	var obj map[string]json.RawMessage
	if len(body) > 0 && json.Unmarshal(body, &obj) == nil {
		for k := range obj {
			rec.BodyKeys = append(rec.BodyKeys, k)
		}
		sort.Strings(rec.BodyKeys)
	}
	return rec
}

func readBody(r *http.Request) []byte {
	if r.Body == nil {
		return nil
	}
	buf := make([]byte, 0, 512)
	tmp := make([]byte, 512)
	for {
		n, err := r.Body.Read(tmp)
		buf = append(buf, tmp[:n]...)
		if err != nil {
			break
		}
	}
	return buf
}

// fail writes a JSON error body and records the rejection reason. The body
// shape mirrors what the appliance returns for a rejected request.
func (s *Server) fail(w http.ResponseWriter, seq, status int, reason string) {
	s.finish(seq, status, reason)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"httpStatusCode": status,
		"message":        reason,
	})
}

// ok writes a success body and records the status.
func (s *Server) ok(w http.ResponseWriter, seq, status int, payload any) {
	s.finish(seq, status, "")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func mediaType(v string) string { return strings.TrimSpace(strings.SplitN(v, ";", 2)[0]) }

func acceptsJSON(accept string) bool {
	if accept == "" {
		return false
	}
	for _, part := range strings.Split(accept, ",") {
		switch mediaType(part) {
		case "application/json", "application/*", "*/*":
			return true
		}
	}
	return false
}

// handleUnserved answers every route the contract does not name.
func (s *Server) handleUnserved(w http.ResponseWriter, r *http.Request) {
	seq := s.record(capture(r, "", readBody(r)))
	s.fail(w, seq, http.StatusNotFound,
		fmt.Sprintf("no contract operation is served at %s %s", r.Method, r.URL.Path))
}

// checkJSONRequest applies the checks every contract operation shares: method,
// no query string, JSON in and out, and a decodable object body. It returns the
// decoded top-level object, or ok=false once it has answered the request.
func (s *Server) checkJSONRequest(w http.ResponseWriter, r *http.Request, seq int, method, operationID string, body []byte) (map[string]json.RawMessage, bool) {
	if r.Method != method {
		s.fail(w, seq, http.StatusMethodNotAllowed,
			fmt.Sprintf("%s is %s", operationID, method))
		return nil, false
	}
	if r.URL.RawQuery != "" {
		s.fail(w, seq, http.StatusBadRequest,
			fmt.Sprintf("%s declares no query parameters", operationID))
		return nil, false
	}
	if ct := mediaType(r.Header.Get("Content-Type")); ct != "application/json" {
		s.fail(w, seq, http.StatusUnsupportedMediaType,
			fmt.Sprintf("Content-Type must be application/json, got %q", r.Header.Get("Content-Type")))
		return nil, false
	}
	if !acceptsJSON(r.Header.Get("Accept")) {
		s.fail(w, seq, http.StatusNotAcceptable,
			fmt.Sprintf("Accept must allow application/json, got %q", r.Header.Get("Accept")))
		return nil, false
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(body, &obj); err != nil {
		s.fail(w, seq, http.StatusBadRequest, "body is not a JSON object")
		return nil, false
	}
	return obj, true
}

// requireToken enforces the Token-based-authorization scheme.
func (s *Server) requireToken(w http.ResponseWriter, r *http.Request, seq int) bool {
	auth, ok := r.Header["Authorization"]
	if !ok {
		s.fail(w, seq, http.StatusUnauthorized, "missing Authorization header")
		return false
	}
	if auth[0] != TokenPrefix+IssuedToken {
		s.fail(w, seq, http.StatusUnauthorized, fmt.Sprintf(
			"Authorization must be %q followed by the acquired token, got %q", TokenPrefix, auth[0]))
		return false
	}
	return true
}

// checkProps rejects a property the schema does not declare, and rejects an
// optional property that was sent empty instead of being omitted. required
// names the properties that must be present; optionalEmptyOK names properties
// that are allowed to carry an empty value because the caller set them so.
func checkProps(obj map[string]json.RawMessage, schema string, declared map[string]bool, required []string) (int, string) {
	for k := range obj {
		if !declared[k] {
			return http.StatusBadRequest, fmt.Sprintf("%s declares no property %q", schema, k)
		}
	}
	req := set(required...)
	for _, k := range required {
		if _, ok := obj[k]; !ok {
			return http.StatusBadRequest, fmt.Sprintf("%s.%s is required", schema, k)
		}
	}
	for k, raw := range obj {
		if req[k] {
			continue
		}
		if status, reason := rejectEmptyOptional(schema, k, raw); status != 0 {
			return status, reason
		}
	}
	return 0, ""
}

// rejectEmptyOptional reports an optional property that was sent as null, as an
// empty string, or as an empty array or object instead of being omitted.
func rejectEmptyOptional(schema, key string, raw json.RawMessage) (int, string) {
	trimmed := strings.TrimSpace(string(raw))
	switch trimmed {
	case "null":
		return http.StatusBadRequest, fmt.Sprintf(
			"%s.%s is optional: omit the property instead of sending null", schema, key)
	case `""`:
		return http.StatusBadRequest, fmt.Sprintf(
			"%s.%s is optional: omit the property instead of sending an empty string", schema, key)
	case "[]":
		return http.StatusBadRequest, fmt.Sprintf(
			"%s.%s is optional: omit the property instead of sending an empty array", schema, key)
	case "{}":
		return http.StatusBadRequest, fmt.Sprintf(
			"%s.%s is optional: omit the property instead of sending an empty object", schema, key)
	}
	return 0, ""
}

// handleAcquireToken serves operationId acquireToken.
func (s *Server) handleAcquireToken(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)
	seq := s.record(capture(r, "acquireToken", body))

	// acquireToken declares security: [] and must be unauthenticated.
	if _, ok := r.Header["Authorization"]; ok && r.Method == http.MethodPost {
		s.fail(w, seq, http.StatusBadRequest,
			"acquireToken declares security: [] and must not carry an Authorization header")
		return
	}
	obj, ok := s.checkJSONRequest(w, r, seq, http.MethodPost, "acquireToken", body)
	if !ok {
		return
	}
	if status, reason := checkProps(obj, "username-password", usernamePasswordProps, []string{"username", "password"}); status != 0 {
		s.fail(w, seq, status, reason)
		return
	}

	var creds struct {
		Username   string `json:"username"`
		Password   string `json:"password"`
		AuthSource string `json:"authSource"`
	}
	_ = json.Unmarshal(body, &creds)
	if creds.AuthSource != "" && creds.AuthSource != ValidAuthSource {
		s.fail(w, seq, http.StatusUnauthorized, "unknown auth source")
		return
	}
	if creds.Username != ValidUsername || creds.Password != ValidPassword {
		s.fail(w, seq, http.StatusUnauthorized, "authentication failed")
		return
	}

	s.ok(w, seq, http.StatusOK, map[string]any{
		"token":     IssuedToken,
		"validity":  int64(1778688000000),
		"expiresAt": TokenExpiresAt,
		"roles":     []string{"Administrator"},
	})
}

// handleCustomGroups serves operationId createCustomGroup.
func (s *Server) handleCustomGroups(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)
	seq := s.record(capture(r, "createCustomGroup", body))

	if r.Method == http.MethodPost && !s.requireToken(w, r, seq) {
		return
	}
	obj, ok := s.checkJSONRequest(w, r, seq, http.MethodPost, "createCustomGroup", body)
	if !ok {
		return
	}
	if status, reason := checkProps(obj, "custom-group", customGroupProps,
		[]string{"resourceKey", "membershipDefinition"}); status != 0 {
		s.fail(w, seq, status, reason)
		return
	}

	// membershipDefinition is required, so the empty object is the correct value
	// when nothing is configured. Its own properties are all optional.
	var membership map[string]json.RawMessage
	if err := json.Unmarshal(obj["membershipDefinition"], &membership); err != nil {
		s.fail(w, seq, http.StatusBadRequest, "custom-group.membershipDefinition must be an object")
		return
	}
	if status, reason := checkProps(membership, "custom-group-membership", membershipProps, nil); status != 0 {
		s.fail(w, seq, status, reason)
		return
	}

	var key map[string]json.RawMessage
	if err := json.Unmarshal(obj["resourceKey"], &key); err != nil {
		s.fail(w, seq, http.StatusBadRequest, "custom-group.resourceKey must be an object")
		return
	}
	if status, reason := checkProps(key, "resource-key", resourceKeyProps,
		[]string{"name", "adapterKindKey", "resourceKindKey"}); status != 0 {
		s.fail(w, seq, status, reason)
		return
	}

	var rk struct {
		Name            string `json:"name"`
		AdapterKindKey  string `json:"adapterKindKey"`
		ResourceKindKey string `json:"resourceKindKey"`
	}
	_ = json.Unmarshal(obj["resourceKey"], &rk)
	if rk.AdapterKindKey != KnownAdapterKind || rk.ResourceKindKey != KnownResourceKind {
		s.fail(w, seq, http.StatusUnprocessableEntity, fmt.Sprintf(
			"unknown resource kind %s/%s", rk.AdapterKindKey, rk.ResourceKindKey))
		return
	}

	out := map[string]any{
		"id":                    AssignedGroupID,
		"resourceKey":           json.RawMessage(obj["resourceKey"]),
		"membershipDefinition":  json.RawMessage(obj["membershipDefinition"]),
		"autoResolveMembership": json.RawMessage(orDefault(obj["autoResolveMembership"], "false")),
	}
	s.ok(w, seq, http.StatusCreated, out)
}

func orDefault(raw json.RawMessage, def string) json.RawMessage {
	if len(raw) == 0 {
		return json.RawMessage(def)
	}
	return raw
}

// handlePolicies serves operationId assignPolicy at /api/policies/{id}/assign.
func (s *Server) handlePolicies(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)

	rest := strings.TrimPrefix(r.URL.Path, PathPolicies)
	policyID, suffix, found := strings.Cut(rest, "/")
	if !found || suffix != "assign" || policyID == "" {
		seq := s.record(capture(r, "", body))
		s.fail(w, seq, http.StatusNotFound,
			fmt.Sprintf("no contract operation is served at %s %s", r.Method, r.URL.Path))
		return
	}
	seq := s.record(capture(r, "assignPolicy", body))

	if r.Method == http.MethodPut && !s.requireToken(w, r, seq) {
		return
	}
	obj, ok := s.checkJSONRequest(w, r, seq, http.MethodPut, "assignPolicy", body)
	if !ok {
		return
	}
	if status, reason := checkProps(obj, "policy-assignment-param", assignmentParamProps, nil); status != 0 {
		s.fail(w, seq, status, reason)
		return
	}
	if policyID != KnownPolicyID {
		s.fail(w, seq, http.StatusNotFound, fmt.Sprintf("no policy with id %s", policyID))
		return
	}

	var param struct {
		GroupIDs            []string                     `json:"groupIds"`
		ResourceAssignments []map[string]json.RawMessage `json:"resourceAssignments"`
	}
	if err := json.Unmarshal(body, &param); err != nil {
		s.fail(w, seq, http.StatusBadRequest, "policy-assignment-param could not be decoded")
		return
	}
	// resource-assignment declares depth and resourceId required: an assignment
	// at depth 0 must still send the property.
	for i, a := range param.ResourceAssignments {
		if status, reason := checkProps(a, fmt.Sprintf("resource-assignment[%d]", i),
			resourceAssignProps, []string{"resourceId", "depth"}); status != 0 {
			s.fail(w, seq, status, reason)
			return
		}
	}

	assigned, failed := []string{}, []string{}
	for _, id := range param.GroupIDs {
		if id == AssignedGroupID {
			assigned = append(assigned, id)
		} else {
			failed = append(failed, id)
		}
	}
	assignedResources, failedResources := []string{}, []string{}
	for _, a := range param.ResourceAssignments {
		var id string
		_ = json.Unmarshal(a["resourceId"], &id)
		if id == KnownResourceID {
			assignedResources = append(assignedResources, id)
		} else {
			failedResources = append(failedResources, id)
		}
	}

	s.ok(w, seq, http.StatusOK, map[string]any{
		"assignedGroupIds":    assigned,
		"assignedResourceIds": assignedResources,
		"failedGroupIds":      failed,
		"failedResourceIds":   failedResources,
	})
}

// handleNotificationRules serves operationId createNotificationPluginRule.
func (s *Server) handleNotificationRules(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)
	seq := s.record(capture(r, "createNotificationPluginRule", body))

	if r.Method == http.MethodPost && !s.requireToken(w, r, seq) {
		return
	}
	obj, ok := s.checkJSONRequest(w, r, seq, http.MethodPost, "createNotificationPluginRule", body)
	if !ok {
		return
	}
	if status, reason := checkProps(obj, "notification-rule", notificationRuleProps,
		[]string{"name", "pluginId"}); status != 0 {
		s.fail(w, seq, status, reason)
		return
	}

	var rule struct {
		Name                     string `json:"name"`
		PluginID                 string `json:"pluginId"`
		AlertDefinitionIDFilters *struct {
			Values []string `json:"values"`
		} `json:"alertDefinitionIdFilters"`
	}
	if err := json.Unmarshal(body, &rule); err != nil {
		s.fail(w, seq, http.StatusUnprocessableEntity, "notification-rule could not be decoded")
		return
	}
	if rule.Name == "" {
		s.fail(w, seq, http.StatusUnprocessableEntity, "notification-rule.name must not be blank")
		return
	}
	if rule.PluginID != KnownPluginID {
		s.fail(w, seq, http.StatusUnprocessableEntity,
			fmt.Sprintf("no notification plugin instance with id %s", rule.PluginID))
		return
	}
	// The spec's documented 404 for this operation: an invalid alert definition
	// identifier in the alert definition filter.
	if rule.AlertDefinitionIDFilters != nil {
		if len(rule.AlertDefinitionIDFilters.Values) == 0 {
			s.fail(w, seq, http.StatusUnprocessableEntity,
				"notification-rule.alertDefinitionIdFilters is optional: omit the property instead of sending a filter with no values")
			return
		}
		for _, id := range rule.AlertDefinitionIDFilters.Values {
			if id != KnownAlertDefinitionID {
				s.fail(w, seq, http.StatusNotFound,
					fmt.Sprintf("unknown alert definition identifier %s in alertDefinitionIdFilters", id))
				return
			}
		}
	}

	out := map[string]any{"id": AssignedRuleID}
	for k, v := range obj {
		out[k] = json.RawMessage(v)
	}
	s.ok(w, seq, http.StatusCreated, out)
}
