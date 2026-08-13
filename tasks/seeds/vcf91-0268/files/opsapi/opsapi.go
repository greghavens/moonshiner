// Package opsapi holds the data types exchanged between the VCF Operations
// client and the code that exercises it.
//
// This package is fixed infrastructure. Do not edit it; implement against it.
package opsapi

import "net/http"

// Config configures a VCF Operations client.
type Config struct {
	// BaseURL is the scheme://host[:port] of the VCF Operations appliance.
	// The API base path declared by the specification is appended to it.
	BaseURL string

	// Username and Password are the credentials presented to the token
	// acquisition operation.
	Username string
	Password string

	// AuthSource names the authentication source. It is optional: when it is
	// empty the field must not appear on the wire at all.
	AuthSource string

	// HTTPClient, when non-nil, is used for every request. When nil the client
	// supplies its own.
	HTTPClient *http.Client
}

// ResourceFilter selects which resources a listing walk returns. Every field is
// optional: an empty slice or a zero PageSize means the corresponding query
// parameter must not be sent at all.
type ResourceFilter struct {
	Name         []string
	AdapterKind  []string
	ResourceKind []string

	// PageSize is the number of resources requested per page. Zero means the
	// server-side default applies and no pageSize parameter is sent.
	PageSize int
}

// Resource is the subset of a VCF Operations resource this client cares about.
type Resource struct {
	Identifier      string
	Name            string
	AdapterKindKey  string
	ResourceKindKey string
}

// PropertySample is one property observation to push for one resource.
//
// Values and Data are mutually exclusive: a sample carries either string values
// or numeric data, never both. Whichever one is unset must not appear on the
// wire.
type PropertySample struct {
	ResourceID string
	StatKey    string
	Timestamps []int64
	Values     []string
	Data       []float64
}

// Stats reports what a client did. Only exchanges that the server answered
// successfully are counted; attempts rejected for an expired token are not.
type Stats struct {
	// TokensAcquired counts successful token acquisitions, including the
	// initial one.
	TokensAcquired int
	// ResourcePagesFetched counts successfully retrieved resource pages.
	ResourcePagesFetched int
	// PropertyBatchesSent counts successfully accepted property batches.
	PropertyBatchesSent int
}
