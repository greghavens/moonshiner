package opsadapter

// NameValue is a single name/value tuple.
type NameValue struct {
	Name  string
	Value string
}

// Credential is the credential instance an adapter instance authenticates with.
//
// Fields is optional.
type Credential struct {
	Name              string
	AdapterKindKey    string
	CredentialKindKey string
	Fields            []NameValue
}

// CreateAdapterInstance is the payload describing the adapter instance to
// register.
//
// Name and AdapterKindKey are mandatory. Every other member is optional and
// must be left out of the request entirely when it is not set.
//
// MonitoringInterval and MonitoringIntervalSeconds are pointers so that an
// explicit zero can be distinguished from "not set": a nil pointer means the
// member is omitted, a pointer to 0 means the member is sent as 0.
type CreateAdapterInstance struct {
	Name                      string
	AdapterKindKey            string
	Description               string
	CollectorID               string
	CollectorGroupID          string
	PhysicalDatacenterID      string
	MonitoringInterval        *int32
	MonitoringIntervalSeconds *int32
	ResourceIdentifiers       []NameValue
	Credential                *Credential
}

// AdapterInstance is the registered adapter instance reported by the server.
type AdapterInstance struct {
	// ID is the UUID of the adapter instance.
	ID string
	// Name, AdapterKindKey and ResourceKindKey come from the composite
	// resource key of the response.
	Name            string
	AdapterKindKey  string
	ResourceKindKey string
}
