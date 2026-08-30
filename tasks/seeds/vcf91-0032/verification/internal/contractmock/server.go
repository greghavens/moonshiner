// Package contractmock provides the protected, contract-pinned loopback
// SDDC Manager used by the acceptance tests.
package contractmock

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"time"
)

const (
	GetTask                 = "getTask"
	GetNotifications        = "getNotifications"
	StartSupportBundle      = "startSupportBundle"
	GetSupportBundleStatus  = "getSupportBundleStatus"
	ExportSupportBundleByID = "exportSupportBundleByID"
)

// VCFError is the focused contract error shape.
type VCFError struct {
	ErrorCode          string `json:"errorCode,omitempty"`
	Message            string `json:"message,omitempty"`
	RemediationMessage string `json:"remediationMessage,omitempty"`
	ReferenceToken     string `json:"referenceToken,omitempty"`
}

// Resource is the focused Task resource shape.
type Resource struct {
	ResourceID string `json:"resourceId"`
	FQDN       string `json:"fqdn,omitempty"`
	Type       string `json:"type"`
	Name       string `json:"name,omitempty"`
}

// Task is the focused getTask response.
type Task struct {
	ID                string     `json:"id"`
	Name              string     `json:"name"`
	Type              string     `json:"type,omitempty"`
	Status            string     `json:"status"`
	CreationTimestamp string     `json:"creationTimestamp"`
	Errors            []VCFError `json:"errors,omitempty"`
	Resources         []Resource `json:"resources,omitempty"`
}

// Message is the focused Notification message shape.
type Message struct {
	ID               string   `json:"id,omitempty"`
	LocalizedMessage string   `json:"localizedMessage,omitempty"`
	Arguments        []string `json:"arguments,omitempty"`
}

// NotifiableResource is a Notification resource.
type NotifiableResource struct {
	ID   string `json:"id,omitempty"`
	Type string `json:"type,omitempty"`
	Name string `json:"name,omitempty"`
}

// Notification is the focused getNotifications response element.
type Notification struct {
	Type              string               `json:"type,omitempty"`
	Severity          string               `json:"severity,omitempty"`
	Message           Message              `json:"message,omitempty"`
	CreationTimestamp string               `json:"creationTimestamp,omitempty"`
	Resources         []NotifiableResource `json:"resources,omitempty"`
}

// SupportBundle is the focused SoS response.
type SupportBundle struct {
	Status              string `json:"status,omitempty"`
	CreationTimestamp   string `json:"creationTimestamp,omitempty"`
	Description         string `json:"description,omitempty"`
	BundleAvailable     string `json:"bundleAvailable,omitempty"`
	ID                  string `json:"id,omitempty"`
	CompletionTimestamp string `json:"completionTimestamp,omitempty"`
	BundleName          string `json:"bundleName,omitempty"`
	Size                string `json:"size,omitempty"`
}

// BundleReply controls one getSupportBundleStatus response.
type BundleReply struct {
	HTTPStatus int
	Status     string
	APIError   VCFError
}

// ArchiveFile controls one entry in the generated tar.gz.
type ArchiveFile struct {
	Name     string
	Mode     int64
	Typeflag byte
	Data     []byte
}

// Plan controls responses. Tests construct it after receiving runtime values.
type Plan struct {
	TaskHTTPStatus          int
	Task                    Task
	TaskAPIError            VCFError
	NotificationsHTTPStatus int
	Notifications           []Notification
	NotificationsAPIError   VCFError
	StartHTTPStatus         int
	StartStatus             string
	StartBundleID           *string
	StartAPIError           VCFError
	BundlePolls             []BundleReply
	ExportHTTPStatus        int
	ExportAPIError          VCFError
	ExportContentType       string
	ArchiveFiles            []ArchiveFile
	RawArchive              []byte
}

// Request is one request observed by the loopback mock.
type Request struct {
	OperationID      string
	Method           string
	Path             string
	EscapedPath      string
	RawQuery         string
	ForceQuery       bool
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
}

// RuntimeValues are independently generated for every mock instance.
type RuntimeValues struct {
	AccessToken    string
	TaskID         string
	ResourceID     string
	EventID        string
	ReferenceToken string
	BundleID       string
}

type contractOperation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

// Server is a loopback-only server scoped to the operations in contract.json.
type Server struct {
	httpServer *httptest.Server
	plan       Plan
	runtime    RuntimeValues
	allowed    map[string]contractOperation

	mu          sync.Mutex
	requests    []Request
	bundlePolls int
	closeOnce   sync.Once
}

// New loads the focused contract, creates runtime values, and starts the mock.
func New(
	contractPath string,
	planFactory func(RuntimeValues) Plan,
) (*Server, error) {
	allowed, err := loadOperations(contractPath)
	if err != nil {
		return nil, err
	}
	runtime := RuntimeValues{
		AccessToken:    randomValue("access"),
		TaskID:         "task id/" + randomValue("task"),
		ResourceID:     randomValue("resource"),
		EventID:        randomValue("event"),
		ReferenceToken: randomValue("reference"),
		BundleID:       randomValue("bundle"),
	}
	plan := Plan{}
	if planFactory != nil {
		plan = planFactory(runtime)
	}
	server := &Server{
		plan:    plan,
		runtime: runtime,
		allowed: allowed,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, errors.New("cannot start loopback contract mock")
	}
	server.httpServer = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: http.HandlerFunc(server.serveHTTP)},
	}
	server.httpServer.Start()
	return server, nil
}

func loadOperations(path string) (map[string]contractOperation, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, errors.New("cannot read focused contract")
	}
	var contract struct {
		Operations []contractOperation `json:"operations"`
	}
	if json.Unmarshal(data, &contract) != nil {
		return nil, errors.New("cannot decode focused contract")
	}
	allowed := make(map[string]contractOperation, len(contract.Operations))
	for _, operation := range contract.Operations {
		if operation.OperationID == "" || operation.Method == "" || operation.Path == "" {
			return nil, errors.New("focused contract contains an incomplete operation")
		}
		if _, exists := allowed[operation.OperationID]; exists {
			return nil, errors.New("focused contract contains a duplicate operationId")
		}
		allowed[operation.OperationID] = operation
	}
	required := map[string]contractOperation{
		GetTask: {
			OperationID: GetTask,
			Method:      http.MethodGet,
			Path:        "/v1/tasks/{id}",
		},
		GetNotifications: {
			OperationID: GetNotifications,
			Method:      http.MethodGet,
			Path:        "/v1/notifications",
		},
		StartSupportBundle: {
			OperationID: StartSupportBundle,
			Method:      http.MethodPost,
			Path:        "/v1/system/support-bundles",
		},
		GetSupportBundleStatus: {
			OperationID: GetSupportBundleStatus,
			Method:      http.MethodGet,
			Path:        "/v1/system/support-bundles/{id}",
		},
		ExportSupportBundleByID: {
			OperationID: ExportSupportBundleByID,
			Method:      http.MethodGet,
			Path:        "/v1/system/support-bundles/{id}/data",
		},
	}
	if len(allowed) != len(required) {
		return nil, errors.New("focused contract operation set is not pinned")
	}
	for operationID, want := range required {
		if got, ok := allowed[operationID]; !ok || got != want {
			return nil, errors.New("focused contract operation does not match pinned route")
		}
	}
	return allowed, nil
}

// Close stops the mock.
func (s *Server) Close() {
	s.closeOnce.Do(s.httpServer.Close)
}

// URL returns the loopback origin.
func (s *Server) URL() string {
	return s.httpServer.URL
}

// Client returns an HTTP client configured for this server.
func (s *Server) Client() *http.Client {
	return s.httpServer.Client()
}

// Runtime returns this mock's generated values.
func (s *Server) Runtime() RuntimeValues {
	return s.runtime
}

// Requests returns a deep copy of the race-safe request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	for index, request := range s.requests {
		out[index] = request
		out[index].Header = request.Header.Clone()
		out[index].TransferEncoding = append([]string(nil), request.TransferEncoding...)
		out[index].Body = append([]byte(nil), request.Body...)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, request *http.Request) {
	body, _ := io.ReadAll(request.Body)
	operationID := s.operationFor(request.Method, request.URL.Path)
	s.record(Request{
		OperationID:      operationID,
		Method:           request.Method,
		Path:             request.URL.Path,
		EscapedPath:      request.URL.EscapedPath(),
		RawQuery:         request.URL.RawQuery,
		ForceQuery:       request.URL.ForceQuery,
		Header:           request.Header.Clone(),
		ContentLength:    request.ContentLength,
		TransferEncoding: append([]string(nil), request.TransferEncoding...),
		Body:             append([]byte(nil), body...),
	})

	if operationID == "" {
		writeJSON(w, http.StatusNotFound, VCFError{
			ErrorCode: "NOT_IN_CONTRACT",
			Message:   "the focused contract does not serve this operation",
		})
		return
	}
	if request.URL.RawQuery != "" || request.URL.ForceQuery {
		writeJSON(w, http.StatusBadRequest, VCFError{
			ErrorCode: "QUERY_NOT_IN_CONTRACT",
			Message:   "the selected operation has no query parameters",
		})
		return
	}

	switch operationID {
	case GetTask:
		s.getTask(w, request.URL.Path)
	case GetNotifications:
		s.getNotifications(w)
	case StartSupportBundle:
		s.startBundle(w)
	case GetSupportBundleStatus:
		s.getBundleStatus(w, request.URL.Path)
	case ExportSupportBundleByID:
		s.exportBundle(w, request.URL.Path)
	}
}

func (s *Server) operationFor(method string, path string) string {
	if operation, ok := s.allowed[GetNotifications]; ok &&
		method == operation.Method && path == operation.Path {
		return operation.OperationID
	}
	if operation, ok := s.allowed[StartSupportBundle]; ok &&
		method == operation.Method && path == operation.Path {
		return operation.OperationID
	}
	if operation, ok := s.allowed[ExportSupportBundleByID]; ok &&
		method == operation.Method &&
		path == strings.ReplaceAll(operation.Path, "{id}", s.runtime.BundleID) {
		return operation.OperationID
	}
	if operation, ok := s.allowed[GetSupportBundleStatus]; ok &&
		method == operation.Method &&
		path == strings.ReplaceAll(operation.Path, "{id}", s.runtime.BundleID) {
		return operation.OperationID
	}
	if operation, ok := s.allowed[GetTask]; ok &&
		method == operation.Method &&
		strings.HasPrefix(path, strings.TrimSuffix(operation.Path, "{id}")) &&
		path != strings.TrimSuffix(operation.Path, "{id}") {
		return operation.OperationID
	}
	return ""
}

func (s *Server) getTask(w http.ResponseWriter, path string) {
	status := s.plan.TaskHTTPStatus
	if status == 0 {
		status = http.StatusOK
	}
	if path != "/v1/tasks/"+s.runtime.TaskID {
		writeJSON(w, http.StatusNotFound, VCFError{
			ErrorCode: "TASK_NOT_FOUND",
			Message:   "the requested task does not exist",
		})
		return
	}
	if status != http.StatusOK {
		writeJSON(w, status, defaultError(s.plan.TaskAPIError, "TASK_READ_FAILED"))
		return
	}
	writeJSON(w, status, s.plan.Task)
}

func (s *Server) getNotifications(w http.ResponseWriter) {
	status := s.plan.NotificationsHTTPStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		writeJSON(
			w,
			status,
			defaultError(s.plan.NotificationsAPIError, "NOTIFICATIONS_READ_FAILED"),
		)
		return
	}
	writeJSON(w, status, s.plan.Notifications)
}

func (s *Server) startBundle(w http.ResponseWriter) {
	status := s.plan.StartHTTPStatus
	if status == 0 {
		status = http.StatusAccepted
	}
	if status != http.StatusAccepted {
		writeJSON(w, status, defaultError(s.plan.StartAPIError, "BUNDLE_START_FAILED"))
		return
	}
	bundleStatus := s.plan.StartStatus
	if bundleStatus == "" {
		bundleStatus = "PENDING"
	}
	bundle := s.bundle(bundleStatus)
	if s.plan.StartBundleID != nil {
		bundle.ID = *s.plan.StartBundleID
	}
	writeJSON(w, status, bundle)
}

func (s *Server) getBundleStatus(w http.ResponseWriter, path string) {
	if path != "/v1/system/support-bundles/"+s.runtime.BundleID {
		writeJSON(w, http.StatusNotFound, VCFError{
			ErrorCode: "BUNDLE_NOT_FOUND",
			Message:   "the requested bundle does not exist",
		})
		return
	}
	reply := s.nextBundleReply()
	status := reply.HTTPStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		writeJSON(w, status, defaultError(reply.APIError, "BUNDLE_STATUS_FAILED"))
		return
	}
	bundleStatus := reply.Status
	if bundleStatus == "" {
		bundleStatus = "IN_PROGRESS"
	}
	writeJSON(w, status, s.bundle(bundleStatus))
}

func (s *Server) nextBundleReply() BundleReply {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.plan.BundlePolls) == 0 {
		return BundleReply{Status: "IN_PROGRESS"}
	}
	index := s.bundlePolls
	if index >= len(s.plan.BundlePolls) {
		index = len(s.plan.BundlePolls) - 1
	} else {
		s.bundlePolls++
	}
	return s.plan.BundlePolls[index]
}

func (s *Server) exportBundle(w http.ResponseWriter, path string) {
	if path != "/v1/system/support-bundles/"+s.runtime.BundleID+"/data" {
		writeJSON(w, http.StatusNotFound, VCFError{
			ErrorCode: "BUNDLE_NOT_FOUND",
			Message:   "the requested bundle does not exist",
		})
		return
	}
	status := s.plan.ExportHTTPStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		writeJSON(w, status, defaultError(s.plan.ExportAPIError, "BUNDLE_EXPORT_FAILED"))
		return
	}
	contentType := s.plan.ExportContentType
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	data := append([]byte(nil), s.plan.RawArchive...)
	if data == nil {
		var err error
		data, err = buildArchive(s.plan.ArchiveFiles)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, VCFError{
				ErrorCode: "FIXTURE_ARCHIVE_FAILED",
				Message:   "the fixture archive could not be created",
			})
			return
		}
	}
	w.Header().Set("Content-Type", contentType)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}

func (s *Server) bundle(status string) SupportBundle {
	bundle := SupportBundle{
		Status:            status,
		CreationTimestamp: "2026-05-13T12:00:00Z",
		Description:       "Collect focused diagnostic logs",
		ID:                s.runtime.BundleID,
	}
	if status == "COMPLETED_WITH_SUCCESS" {
		bundle.BundleAvailable = "true"
		bundle.CompletionTimestamp = "2026-05-13T12:01:00Z"
		bundle.BundleName = s.runtime.BundleID + ".tar.gz"
	}
	return bundle
}

func (s *Server) record(request Request) {
	s.mu.Lock()
	s.requests = append(s.requests, request)
	s.mu.Unlock()
}

func buildArchive(files []ArchiveFile) ([]byte, error) {
	var output bytes.Buffer
	gzipWriter := gzip.NewWriter(&output)
	gzipWriter.Header.ModTime = time.Unix(0, 0)
	tarWriter := tar.NewWriter(gzipWriter)
	for _, file := range files {
		mode := file.Mode
		if mode == 0 {
			mode = 0o600
		}
		typeflag := file.Typeflag
		if typeflag == 0 {
			typeflag = tar.TypeReg
		}
		header := &tar.Header{
			Name:     file.Name,
			Mode:     mode,
			Size:     int64(len(file.Data)),
			Typeflag: typeflag,
			ModTime:  time.Unix(0, 0),
		}
		if typeflag != tar.TypeReg && typeflag != tar.TypeRegA {
			header.Size = 0
		}
		if err := tarWriter.WriteHeader(header); err != nil {
			return nil, err
		}
		if header.Size > 0 {
			if _, err := tarWriter.Write(file.Data); err != nil {
				return nil, err
			}
		}
	}
	if err := tarWriter.Close(); err != nil {
		return nil, err
	}
	if err := gzipWriter.Close(); err != nil {
		return nil, err
	}
	return output.Bytes(), nil
}

func defaultError(got VCFError, code string) VCFError {
	if got.ErrorCode == "" {
		got.ErrorCode = code
	}
	if got.Message == "" {
		got.Message = "the planned request failed"
	}
	return got
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func randomValue(prefix string) string {
	var data [12]byte
	if _, err := rand.Read(data[:]); err != nil {
		panic("cannot generate protected mock value")
	}
	return prefix + "-" + hex.EncodeToString(data[:])
}
