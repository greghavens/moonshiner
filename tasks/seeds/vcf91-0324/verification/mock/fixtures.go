package mock

import "fmt"

// This file is complete. It gives the mock a deterministic corpus to serve so
// that every run of the test suite sees exactly the same bytes.

// DeploymentID is the ID of the i'th fixture deployment (0-based).
func DeploymentID(i int) string {
	return fmt.Sprintf("d5f6a4c2-0000-4000-8000-%012d", i)
}

// CatalogItemID is the ID of the i'th fixture catalog item (0-based).
func CatalogItemID(i int) string {
	return fmt.Sprintf("c1e2b3a4-0000-4000-8000-%012d", i)
}

// Deployments builds n fixture deployments, in a stable order.
func Deployments(n int) []map[string]any {
	statuses := []string{"CREATE_SUCCESSFUL", "UPDATE_SUCCESSFUL", "CREATE_INPROGRESS"}
	out := make([]map[string]any, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, map[string]any{
			"id":            DeploymentID(i),
			"name":          fmt.Sprintf("deployment-%02d", i),
			"description":   fmt.Sprintf("fixture deployment %d", i),
			"orgId":         "0f1e2d3c-4b5a-4000-8000-000000000001",
			"projectId":     fmt.Sprintf("proj-%d", i%3),
			"status":        statuses[i%len(statuses)],
			"createdAt":     fmt.Sprintf("2026-01-%02dT08:00:00.000Z", (i%28)+1),
			"lastUpdatedAt": fmt.Sprintf("2026-02-%02dT08:00:00.000Z", (i%28)+1),
			"createdBy":     "svc-automation@example.com",
			"ownedBy":       "svc-automation@example.com",
		})
	}
	return out
}

// CatalogItems builds n fixture catalog items, in a stable order.
func CatalogItems(n int) []map[string]any {
	out := make([]map[string]any, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, map[string]any{
			"id":               CatalogItemID(i),
			"name":             fmt.Sprintf("catalog-item-%02d", i),
			"description":      fmt.Sprintf("fixture catalog item %d", i),
			"sourceId":         "src-0000-4000-8000-000000000001",
			"sourceName":       "fixture-source",
			"iconId":           "icon-0000-4000-8000-000000000001",
			"formId":           fmt.Sprintf("form-%d", i),
			"isRequestable":    true,
			"global":           i%2 == 0,
			"bulkRequestLimit": 10,
			"projectIds":       []any{"proj-0", "proj-1"},
			"createdBy":        "svc-automation@example.com",
			"createdAt":        fmt.Sprintf("2026-01-%02dT08:00:00.000Z", (i%28)+1),
		})
	}
	return out
}
