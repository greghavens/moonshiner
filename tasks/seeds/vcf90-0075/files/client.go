// Package maintenancechange applies ordered VCF Operations maintenance
// schedule changes against the focused OpenAPI-derived contract.
package maintenancechange

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

// ErrNotImplemented marks the incomplete integration.
var ErrNotImplemented = errors.New("maintenance change integration is not implemented")

// Config configures a Client. AccessToken is sent as the value of the
// contract's Authorization apiKey header.
type Config struct {
	BaseURL     string
	AccessToken string
	HTTPClient  *http.Client
}

// Schedule is the focused projection of the contract's schedule schema.
// Pointer and slice fields are optional; their zero value leaves the
// corresponding JSON member off the wire.
type Schedule struct {
	Hour            int32    `json:"hour"`
	MinuteOfTheHour int32    `json:"minuteOfTheHour"`
	Duration        int32    `json:"duration"`
	ScheduleType    string   `json:"scheduleType"`
	Recurrence      *int32   `json:"recurrence,omitempty"`
	DayOfTheMonth   *int32   `json:"dayOfTheMonth,omitempty"`
	DaysOfTheMonth  []string `json:"daysOfTheMonth,omitempty"`
	WeeksOfTheMonth []string `json:"weeksOfTheMonth,omitempty"`
	DaysOfTheWeek   []string `json:"daysOfTheWeek,omitempty"`
	Month           *int32   `json:"month,omitempty"`
	Months          []int32  `json:"months,omitempty"`
	StartDate       *string  `json:"startDate,omitempty"`
	ExpirationDate  *string  `json:"expirationDate,omitempty"`
	TimeZone        *string  `json:"timeZone,omitempty"`
	ExpireRuns      *int32   `json:"expireRuns,omitempty"`
}

// MaintenanceScheduleSpec is one createMaintenanceSchedules request.
// The server-generated id is deliberately absent from this request model.
type MaintenanceScheduleSpec struct {
	Key      string   `json:"key"`
	Schedule Schedule `json:"schedule"`
}

// MaintenanceSchedule is one createMaintenanceSchedules response.
type MaintenanceSchedule struct {
	ID       string   `json:"id,omitempty"`
	Key      string   `json:"key"`
	Schedule Schedule `json:"schedule"`
}

// StepStatus describes an attempted change step.
type StepStatus string

const (
	StepSucceeded StepStatus = "SUCCEEDED"
	StepFailed    StepStatus = "FAILED"
)

// StepResult records one attempted schedule creation. Created is populated
// only for a successful step.
type StepResult struct {
	Index   int
	Key     string
	Status  StepStatus
	Created *MaintenanceSchedule
}

// ApplyReport preserves the order and outcome of every attempted step.
type ApplyReport struct {
	Results []StepResult
}

// APIError reports a non-success response from VCF Operations.
type APIError struct {
	OperationID string
	StatusCode  int
}

func (e *APIError) Error() string {
	if e == nil {
		return "VCF Operations API request failed"
	}
	return fmt.Sprintf("VCF Operations operation %s failed with HTTP %d", e.OperationID, e.StatusCode)
}

// ProtocolError reports a success response that violates the focused
// response contract.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	if e == nil {
		return "VCF Operations protocol error"
	}
	return fmt.Sprintf("VCF Operations operation %s violated the response contract: %s", e.OperationID, e.Reason)
}

// Client is a focused createMaintenanceSchedules client.
type Client struct{}

// NewClient validates configuration without making a request.
func NewClient(config Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// Apply creates schedules in input order, stopping at the first failed step.
// Its report contains every attempted step, including successful steps before
// the returned error.
func (c *Client) Apply(ctx context.Context, changes []MaintenanceScheduleSpec) (ApplyReport, error) {
	return ApplyReport{}, ErrNotImplemented
}
