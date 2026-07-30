// Package vksdiag diagnoses one narrowly defined VKS workload failure while
// preserving the authentication boundary between vCenter and Kubernetes.
package vksdiag

import (
	"fmt"
	"net/http"
)

const (
	OperationGetSupervisorNamespace = "getSupervisorNamespace"
	OperationListPodEvents          = "listPodEvents"
	OperationReadPodLog             = "readPodLog"
)

type Outcome string

const (
	OutcomeConfirmed    Outcome = "confirmed"
	OutcomeInconclusive Outcome = "inconclusive"
)

type Cause string

const (
	CauseMissingRequiredEnvironment Cause = "missing-required-environment"
)

type Config struct {
	VCenterURL            string
	KubernetesURL         string
	VCenterSessionID      string
	KubernetesBearerToken string
	HTTPClient            *http.Client
}

type DiagnoseRequest struct {
	Supervisor string
	Namespace  string
	Pod        string
	Container  string
	Previous   *bool
}

type Diagnosis struct {
	Outcome                    Outcome
	Cause                      Cause
	MissingEnvironmentVariable string
	EventName                  string
}

type APIError struct {
	Operation  string
	StatusCode int
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s returned HTTP %d", e.Operation, e.StatusCode)
}

type ProtocolError struct {
	Operation string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("%s returned an invalid success response", e.Operation)
}

type NamespaceNotReadyError struct {
	Operation string
	Status    string
}

func (e *NamespaceNotReadyError) Error() string {
	return fmt.Sprintf("%s reported namespace status %s", e.Operation, e.Status)
}
