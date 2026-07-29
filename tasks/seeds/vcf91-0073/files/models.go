package nsxchange

// Condition is the NSX Policy Condition expression used by this exercise.
// Optional fields must remain omitted when they are unset.
type Condition struct {
	ResourceType string `json:"resource_type,omitempty"`
	MemberType   string `json:"member_type,omitempty"`
	Key          string `json:"key,omitempty"`
	Operator     string `json:"operator,omitempty"`
	Value        string `json:"value,omitempty"`
}

// Group is the writable subset of the NSX Policy Group schema used here.
type Group struct {
	DisplayName        string      `json:"display_name,omitempty"`
	Description        string      `json:"description,omitempty"`
	ResourceType       string      `json:"resource_type,omitempty"`
	Expression         []Condition `json:"expression,omitempty"`
	ExtendedExpression []Condition `json:"extended_expression,omitempty"`
	GroupType          []string    `json:"group_type,omitempty"`
	Tags               []Tag       `json:"tags,omitempty"`
}

type Tag struct {
	Scope string `json:"scope,omitempty"`
	Tag   string `json:"tag,omitempty"`
}

// SecurityPolicy is the writable subset needed for the final operation.
type SecurityPolicy struct {
	DisplayName  string   `json:"display_name,omitempty"`
	Description  string   `json:"description,omitempty"`
	ResourceType string   `json:"resource_type,omitempty"`
	Category     string   `json:"category,omitempty"`
	Stateful     *bool    `json:"stateful,omitempty"`
	TCPStrict    *bool    `json:"tcp_strict,omitempty"`
	Locked       *bool    `json:"locked,omitempty"`
	Scope        []string `json:"scope,omitempty"`
	Tags         []Tag    `json:"tags,omitempty"`
	Rules        []Rule   `json:"rules,omitempty"`
}

// Rule is the writable subset of an NSX distributed-firewall rule.
type Rule struct {
	DisplayName       string   `json:"display_name,omitempty"`
	Description       string   `json:"description,omitempty"`
	ResourceType      string   `json:"resource_type,omitempty"`
	Action            string   `json:"action,omitempty"`
	Direction         string   `json:"direction,omitempty"`
	SourceGroups      []string `json:"source_groups,omitempty"`
	DestinationGroups []string `json:"destination_groups,omitempty"`
	Services          []string `json:"services,omitempty"`
	Scope             []string `json:"scope,omitempty"`
	Logged            *bool    `json:"logged,omitempty"`
	Disabled          *bool    `json:"disabled,omitempty"`
	Tags              []Tag    `json:"tags,omitempty"`
}
