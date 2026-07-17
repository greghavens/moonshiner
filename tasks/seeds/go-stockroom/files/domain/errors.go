package domain

import "errors"

// Sentinel errors shared across layers; the api package maps them onto
// HTTP status codes.
var (
	ErrUnknownWarehouse  = errors.New("unknown warehouse")
	ErrWarehouseExists   = errors.New("warehouse already exists")
	ErrUnknownSKU        = errors.New("unknown sku")
	ErrInsufficientStock = errors.New("insufficient stock")
	ErrInvalidQty        = errors.New("quantity must be positive")
	ErrLocked            = errors.New("warehouse is locked for counting")
	ErrNotLocked         = errors.New("warehouse is not locked")
)
