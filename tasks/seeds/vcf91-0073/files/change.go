package nsxchange

import "context"

type GroupChange struct {
	ID          string
	DisplayName string
	TagScope    string
	TagValue    string
}

type PolicyChange struct {
	ID              string
	DisplayName     string
	Category        string
	RuleDisplayName string
	ServicePath     string
}

type Change struct {
	DomainID         string
	SourceGroup      GroupChange
	DestinationGroup GroupChange
	Policy           PolicyChange
}

type StepStatus string

const (
	StepApplied StepStatus = "applied"
	StepFailed  StepStatus = "failed"
)

type StepResult struct {
	Name         string
	OperationID  string
	ResourcePath string
	Status       StepStatus
	HTTPStatus   int
}

type Report struct {
	Steps []StepResult
}

// Apply applies source group, destination group, and security policy in order.
func (c *Client) Apply(ctx context.Context, change Change) (Report, error) {
	return Report{}, ErrNotImplemented
}
