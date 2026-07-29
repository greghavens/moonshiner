// Package contractmock provides the loopback-only network-pool fixture pinned
// to docs/contract.json. It is test infrastructure, not an SDDC Manager
// emulator.
package contractmock

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
)

// Mode selects deterministic response behavior.
type Mode string

const (
	ModeOK               Mode = "ok"
	ModeEmpty            Mode = "empty"
	ModeExisting         Mode = "existing"
	ModeDrift            Mode = "drift"
	ModeAmbiguous        Mode = "ambiguous"
	ModeCommitThenDrop   Mode = "commit-then-drop"
	ModeListAPIError     Mode = "list-api-error"
	ModeCreateAPIError   Mode = "create-api-error"
	ModeMalformedList    Mode = "malformed-list"
	ModeMalformedCreate  Mode = "malformed-create"
	ModeWrongListMedia   Mode = "wrong-list-media"
	ModeWrongCreateMedia Mode = "wrong-create-media"
	ModeTrailingList     Mode = "trailing-list"
	ModeTrailingCreate   Mode = "trailing-create"
	ModeOversizedList    Mode = "oversized-list"
	ModeOversizedCreate  Mode = "oversized-create"
	ModeBadMetadata      Mode = "bad-metadata"
	ModeRedirect         Mode = "redirect"
)

// IPPool is one fixture IP range.
type IPPool struct {
	Start string `json:"start"`
	End   string `json:"end"`
}

// NetworkSpec is the writable request projection.
type NetworkSpec struct {
	Type                    string   `json:"type"`
	IPAddressVersion        *string  `json:"ipAddressVersion,omitempty"`
	IPAddressAssignmentMode *string  `json:"ipAddressAssignmentMode,omitempty"`
	VLANID                  int      `json:"vlanId"`
	MTU                     int      `json:"mtu"`
	Subnet                  *string  `json:"subnet,omitempty"`
	Mask                    *string  `json:"mask,omitempty"`
	Gateway                 *string  `json:"gateway,omitempty"`
	IPPools                 []IPPool `json:"ipPools,omitempty"`
}

// NetworkPoolSpec is the create request projection.
type NetworkPoolSpec struct {
	Name     string        `json:"name"`
	Networks []NetworkSpec `json:"networks"`
}

// Network is one response network.
type Network struct {
	ID                      string   `json:"id,omitempty"`
	Type                    string   `json:"type"`
	IPAddressVersion        *string  `json:"ipAddressVersion,omitempty"`
	IPAddressAssignmentMode *string  `json:"ipAddressAssignmentMode,omitempty"`
	VLANID                  int      `json:"vlanId"`
	MTU                     int      `json:"mtu"`
	Subnet                  *string  `json:"subnet,omitempty"`
	Mask                    *string  `json:"mask,omitempty"`
	Gateway                 *string  `json:"gateway,omitempty"`
	IPPools                 []IPPool `json:"ipPools,omitempty"`
	FreeIPs                 []string `json:"freeIps,omitempty"`
	UsedIPs                 []string `json:"usedIps,omitempty"`
	UsedIPCount             string   `json:"usedIpCount,omitempty"`
	FreeIPCount             string   `json:"freeIpCount,omitempty"`
}

// NetworkPool is one fixture pool.
type NetworkPool struct {
	ID         string    `json:"id,omitempty"`
	Name       string    `json:"name"`
	Networks   []Network `json:"networks"`
	HostsCount int       `json:"hostsCount,omitempty"`
}

// Runtime contains values generated when the fixture starts.
type Runtime struct {
	AccessToken    string
	TargetName     string
	Pools          []NetworkPool
	ErrorCode      string
	ErrorMessage   string
	Remediation    string
	ReferenceToken string
}

// Request records one received HTTP request. Header and Body are copied.
type Request struct {
	Method           string
	Path             string
	RawQuery         string
	Header           http.Header
	Body             []byte
	TransferEncoding []string
	Reversed         bool
}

// Server is a loopback-only contract fixture with race-safe state and logs.
type Server struct {
	mode       Mode
	runtime    Runtime
	listener   net.Listener
	httpServer *http.Server

	mu             sync.Mutex
	pools          []NetworkPool
	requests       []Request
	responseSerial int
	effects        int
}

type contractFile struct {
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

// New loads the protected contract and starts an ephemeral IPv4 loopback
// server. The fixture exposes only the operations named by that contract.
func New(mode Mode) (*Server, error) {
	if err := validateContract("docs/contract.json"); err != nil {
		return nil, err
	}
	if mode == "" {
		mode = ModeOK
	}
	nonce, err := randomHex(12)
	if err != nil {
		return nil, fmt.Errorf("generate fixture values: %w", err)
	}
	runtime := Runtime{
		AccessToken:    "access-" + nonce,
		TargetName:     "retry-pool-" + nonce,
		ErrorCode:      "ERR-" + nonce,
		ErrorMessage:   "server-message-" + nonce,
		Remediation:    "server-remediation-" + nonce,
		ReferenceToken: "reference-" + nonce,
		Pools: []NetworkPool{
			{
				ID:         nonce + "-z",
				Name:       "zulu",
				Networks:   []Network{},
				HostsCount: 3,
			},
			{
				ID:         nonce + "-b",
				Name:       "alpha",
				Networks:   []Network{},
				HostsCount: 2,
			},
			{
				ID:         nonce + "-a",
				Name:       "alpha",
				Networks:   []Network{},
				HostsCount: 1,
			},
			{
				ID:         nonce + "-c",
				Name:       "charlie",
				Networks:   []Network{},
				HostsCount: 4,
			},
		},
	}

	target := fixtureTarget(runtime.TargetName)
	switch mode {
	case ModeEmpty:
		runtime.Pools = []NetworkPool{}
	case ModeExisting:
		runtime.Pools = append(
			runtime.Pools,
			responsePool(nonce+"-existing", target),
		)
	case ModeDrift:
		drift := fixtureTarget(runtime.TargetName)
		drift.Networks[0].MTU = 1500
		runtime.Pools = append(
			runtime.Pools,
			responsePool(nonce+"-drift", drift),
		)
	case ModeAmbiguous:
		runtime.Pools = append(
			runtime.Pools,
			responsePool(nonce+"-first", target),
			responsePool(nonce+"-second", target),
		)
	}

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("listen on loopback: %w", err)
	}
	server := &Server{
		mode:     mode,
		runtime:  runtime,
		listener: listener,
		pools:    clonePools(runtime.Pools),
	}
	server.httpServer = &http.Server{Handler: server}
	go func() {
		_ = server.httpServer.Serve(listener)
	}()
	return server, nil
}

// URL returns the fixture's loopback origin.
func (s *Server) URL() string {
	if s == nil || s.listener == nil {
		return ""
	}
	return "http://" + s.listener.Addr().String()
}

// Client returns an HTTP client suitable for the fixture.
func (s *Server) Client() *http.Client {
	return &http.Client{}
}

// Runtime returns a deep copy of runtime-generated fixture values.
func (s *Server) Runtime() Runtime {
	if s == nil {
		return Runtime{}
	}
	out := s.runtime
	out.Pools = clonePools(s.runtime.Pools)
	return out
}

// Requests returns a deep copy of the race-safe request log.
func (s *Server) Requests() []Request {
	if s == nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	for index, request := range s.requests {
		out[index] = request
		out[index].Header = request.Header.Clone()
		out[index].Body = append([]byte(nil), request.Body...)
		out[index].TransferEncoding = append(
			[]string(nil),
			request.TransferEncoding...,
		)
	}
	return out
}

// EffectCount reports how many network pools the POST handler committed.
func (s *Server) EffectCount() int {
	if s == nil {
		return 0
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.effects
}

// Close stops the fixture.
func (s *Server) Close() {
	if s == nil || s.httpServer == nil {
		return
	}
	_ = s.httpServer.Close()
}

// ServeHTTP exposes exactly getNetworkPool and createNetworkPool.
func (s *Server) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	body, _ := io.ReadAll(io.LimitReader(request.Body, (1<<20)+1))
	if request.URL.Path != "/v1/network-pools" ||
		(request.Method != http.MethodGet &&
			request.Method != http.MethodPost) {
		s.record(request, body, false)
		http.NotFound(writer, request)
		return
	}

	if request.Method == http.MethodGet {
		s.serveList(writer, request, body)
		return
	}
	s.serveCreate(writer, request, body)
}

func (s *Server) serveList(
	writer http.ResponseWriter,
	request *http.Request,
	body []byte,
) {
	reversed := s.nextReversal()
	s.record(request, body, reversed)
	if !s.validHeaders(request, false) ||
		request.URL.RawQuery != "" ||
		len(body) != 0 ||
		len(request.TransferEncoding) != 0 {
		s.writeAPIError(writer, http.StatusBadRequest)
		return
	}
	switch s.mode {
	case ModeListAPIError:
		s.writeAPIError(writer, http.StatusInternalServerError)
		return
	case ModeMalformedList:
		writeRaw(writer, http.StatusOK, "application/json", `{"elements":[`)
		return
	case ModeWrongListMedia:
		writeRaw(writer, http.StatusOK, "text/plain", `{}`)
		return
	case ModeOversizedList:
		writeRaw(
			writer,
			http.StatusOK,
			"application/json",
			strings.Repeat("x", (1<<20)+1),
		)
		return
	case ModeRedirect:
		writer.Header().Set("Location", "/v1/network-pools")
		writer.WriteHeader(http.StatusFound)
		return
	}

	pools := s.snapshotPools(reversed)
	totalPages := 1
	if len(pools) == 0 {
		totalPages = 0
	}
	metadata := pageMetadata{
		PageNumber:    0,
		PageSize:      len(pools),
		TotalElements: len(pools),
		TotalPages:    totalPages,
	}
	if s.mode == ModeBadMetadata {
		metadata.TotalElements++
	}
	writer.Header().Set("Content-Type", "application/json; charset=utf-8")
	writer.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(writer).Encode(networkPoolPage{
		Elements:     pools,
		PageMetadata: metadata,
	})
	if s.mode == ModeTrailingList {
		_, _ = io.WriteString(writer, "{}\n")
	}
}

func (s *Server) serveCreate(
	writer http.ResponseWriter,
	request *http.Request,
	body []byte,
) {
	s.record(request, body, false)
	if !s.validHeaders(request, true) ||
		request.URL.RawQuery != "" ||
		len(request.TransferEncoding) != 0 {
		s.writeAPIError(writer, http.StatusBadRequest)
		return
	}
	spec, ok := decodeCreate(body)
	if !ok {
		s.writeAPIError(writer, http.StatusBadRequest)
		return
	}
	if s.mode == ModeCreateAPIError {
		s.writeAPIError(writer, http.StatusInternalServerError)
		return
	}

	created, effect := s.commit(spec)
	if s.mode == ModeCommitThenDrop && effect == 1 {
		hijacker, ok := writer.(http.Hijacker)
		if !ok {
			panic("loopback response writer does not support hijacking")
		}
		connection, _, err := hijacker.Hijack()
		if err == nil {
			_ = connection.Close()
		}
		return
	}
	switch s.mode {
	case ModeMalformedCreate:
		writeRaw(writer, http.StatusCreated, "application/json", `{"id":`)
		return
	case ModeWrongCreateMedia:
		writeRaw(writer, http.StatusCreated, "text/plain", `{}`)
		return
	case ModeOversizedCreate:
		writeRaw(
			writer,
			http.StatusCreated,
			"application/json",
			strings.Repeat("x", (1<<20)+1),
		)
		return
	}

	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(writer).Encode(created)
	if s.mode == ModeTrailingCreate {
		_, _ = io.WriteString(writer, "{}\n")
	}
}

func (s *Server) validHeaders(
	request *http.Request,
	isCreate bool,
) bool {
	if request.Header.Get("Authorization") !=
		"Bearer "+s.runtime.AccessToken ||
		request.Header.Get("Accept") != "application/json" {
		return false
	}
	if isCreate {
		return request.Header.Get("Content-Type") == "application/json"
	}
	return request.Header.Get("Content-Type") == ""
}

type pageMetadata struct {
	PageNumber    int `json:"pageNumber"`
	PageSize      int `json:"pageSize"`
	TotalElements int `json:"totalElements"`
	TotalPages    int `json:"totalPages"`
}

type networkPoolPage struct {
	Elements     []NetworkPool `json:"elements"`
	PageMetadata pageMetadata  `json:"pageMetadata"`
}

func (s *Server) nextReversal() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	reversed := s.responseSerial%2 == 0
	s.responseSerial++
	return reversed
}

func (s *Server) snapshotPools(reversed bool) []NetworkPool {
	s.mu.Lock()
	pools := clonePools(s.pools)
	s.mu.Unlock()
	if reversed {
		for left, right := 0, len(pools)-1; left < right; left, right =
			left+1, right-1 {
			pools[left], pools[right] = pools[right], pools[left]
		}
	}
	return pools
}

func (s *Server) commit(spec NetworkPoolSpec) (NetworkPool, int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.effects++
	created := responsePool(
		fmt.Sprintf("%s-created-%d", s.runtime.TargetName, s.effects),
		spec,
	)
	s.pools = append(s.pools, created)
	return clonePools([]NetworkPool{created})[0], s.effects
}

func (s *Server) record(
	request *http.Request,
	body []byte,
	reversed bool,
) {
	entry := Request{
		Method:           request.Method,
		Path:             request.URL.Path,
		RawQuery:         request.URL.RawQuery,
		Header:           request.Header.Clone(),
		Body:             append([]byte(nil), body...),
		TransferEncoding: append([]string(nil), request.TransferEncoding...),
		Reversed:         reversed,
	}
	s.mu.Lock()
	s.requests = append(s.requests, entry)
	s.mu.Unlock()
}

func (s *Server) writeAPIError(writer http.ResponseWriter, status int) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(map[string]string{
		"errorCode":          s.runtime.ErrorCode,
		"message":            s.runtime.ErrorMessage,
		"remediationMessage": s.runtime.Remediation,
		"referenceToken":     s.runtime.ReferenceToken,
	})
}

func fixtureTarget(name string) NetworkPoolSpec {
	ipv4 := "IPv4"
	static := "STATIC"
	subnet := "10.0.0.0"
	mask := "255.255.255.0"
	gateway := "10.0.0.1"
	return NetworkPoolSpec{
		Name: name,
		Networks: []NetworkSpec{{
			Type:                    "VMOTION",
			IPAddressVersion:        &ipv4,
			IPAddressAssignmentMode: &static,
			VLANID:                  120,
			MTU:                     9000,
			Subnet:                  &subnet,
			Mask:                    &mask,
			Gateway:                 &gateway,
			IPPools: []IPPool{{
				Start: "10.0.0.10",
				End:   "10.0.0.20",
			}},
		}},
	}
}

func responsePool(id string, spec NetworkPoolSpec) NetworkPool {
	networks := make([]Network, len(spec.Networks))
	for index, network := range spec.Networks {
		networks[index] = Network{
			ID:                      id + "-network",
			Type:                    network.Type,
			IPAddressVersion:        cloneString(network.IPAddressVersion),
			IPAddressAssignmentMode: cloneString(network.IPAddressAssignmentMode),
			VLANID:                  network.VLANID,
			MTU:                     network.MTU,
			Subnet:                  cloneString(network.Subnet),
			Mask:                    cloneString(network.Mask),
			Gateway:                 cloneString(network.Gateway),
			IPPools:                 append([]IPPool(nil), network.IPPools...),
		}
	}
	return NetworkPool{
		ID:       id,
		Name:     spec.Name,
		Networks: networks,
	}
}

func decodeCreate(body []byte) (NetworkPoolSpec, bool) {
	var spec NetworkPoolSpec
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&spec); err != nil {
		return NetworkPoolSpec{}, false
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return NetworkPoolSpec{}, false
	}
	if strings.TrimSpace(spec.Name) == "" ||
		spec.Name != strings.TrimSpace(spec.Name) ||
		spec.Networks == nil {
		return NetworkPoolSpec{}, false
	}
	for _, network := range spec.Networks {
		if strings.TrimSpace(network.Type) == "" {
			return NetworkPoolSpec{}, false
		}
	}
	return spec, true
}

func clonePools(input []NetworkPool) []NetworkPool {
	output := make([]NetworkPool, len(input))
	for poolIndex, pool := range input {
		output[poolIndex] = pool
		output[poolIndex].Networks = make([]Network, len(pool.Networks))
		for networkIndex, network := range pool.Networks {
			output[poolIndex].Networks[networkIndex] = network
			cloned := &output[poolIndex].Networks[networkIndex]
			cloned.IPAddressVersion = cloneString(network.IPAddressVersion)
			cloned.IPAddressAssignmentMode =
				cloneString(network.IPAddressAssignmentMode)
			cloned.Subnet = cloneString(network.Subnet)
			cloned.Mask = cloneString(network.Mask)
			cloned.Gateway = cloneString(network.Gateway)
			cloned.IPPools = append([]IPPool(nil), network.IPPools...)
			cloned.FreeIPs = append([]string(nil), network.FreeIPs...)
			cloned.UsedIPs = append([]string(nil), network.UsedIPs...)
		}
	}
	return output
}

func cloneString(value *string) *string {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}

func writeRaw(
	writer http.ResponseWriter,
	status int,
	contentType string,
	body string,
) {
	writer.Header().Set("Content-Type", contentType)
	writer.WriteHeader(status)
	_, _ = io.WriteString(writer, body)
}

func validateContract(path string) error {
	content, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read contract: %w", err)
	}
	var contract contractFile
	if err := json.Unmarshal(content, &contract); err != nil {
		return fmt.Errorf("decode contract: %w", err)
	}
	if len(contract.Operations) != 2 {
		return errors.New("contract fixture requires exactly two operations")
	}
	want := map[string]string{
		"getNetworkPool":    http.MethodGet,
		"createNetworkPool": http.MethodPost,
	}
	for _, operation := range contract.Operations {
		method, ok := want[operation.OperationID]
		if !ok ||
			operation.Method != method ||
			operation.Path != "/v1/network-pools" {
			return errors.New("contract fixture has an unexpected operation")
		}
		delete(want, operation.OperationID)
	}
	if len(want) != 0 {
		return errors.New("contract fixture is missing a required operation")
	}
	return nil
}

func randomHex(bytesCount int) (string, error) {
	buffer := make([]byte, bytesCount)
	if _, err := rand.Read(buffer); err != nil {
		return "", err
	}
	return hex.EncodeToString(buffer), nil
}
