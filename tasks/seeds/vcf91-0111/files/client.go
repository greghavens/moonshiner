// Package vcentercategories retrieves a complete vCenter tag-category collection.
package vcentercategories

import (
	"context"
	"errors"
	"net/http"
)

const listCategoriesOperationID = "Vcenter.Tagging.Categories_list"

// Config configures a vCenter Automation API client.
type Config struct {
	BaseURL    string
	SessionID  string
	HTTPClient *http.Client
}

// ListOptions controls the contract's category filter and iteration page size.
type ListOptions struct {
	Names    []string
	PageSize *int64
}

// Category is one Vcenter.Tagging.Categories.ListItem.
type Category struct {
	CategoryID string       `json:"category_id"`
	Info       CategoryInfo `json:"info"`
}

// CategoryInfo is the projected Vcenter.Tagging.Categories.Info schema.
type CategoryInfo struct {
	Name            string   `json:"name"`
	Description     string   `json:"description"`
	Cardinality     string   `json:"cardinality"`
	AssociableTypes []string `json:"associable_types"`
	UsedBy          []string `json:"used_by"`
}

// LocalizableMessage is the projected error message returned by vAPI.
type LocalizableMessage struct {
	ID             string `json:"id"`
	DefaultMessage string `json:"default_message"`
}

// APIError represents a non-success response from an operation.
type APIError struct {
	OperationID string
	StatusCode  int
	ErrorType   string
	Messages    []LocalizableMessage
}

func (e *APIError) Error() string { return "vCenter API request failed" }

// ProtocolError represents a malformed or inconsistent successful response.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	return "vCenter response violated the focused contract"
}

// Client is a focused vCenter tag-category client.
type Client struct{}

// NewClient validates config without performing network I/O.
func NewClient(config Config) (*Client, error) {
	return nil, errors.New("not implemented")
}

// ListAllCategories retrieves every page and returns a contract-sorted collection.
func (c *Client) ListAllCategories(
	ctx context.Context,
	options ListOptions,
) ([]Category, error) {
	return nil, errors.New("not implemented")
}
