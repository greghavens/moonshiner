// Package syncer retrieves complete widget snapshots from the catalog API.
package syncer

import (
	"context"
	"fmt"

	"example.com/catalogsync/internal/catalogapi"
)

type widgetLister interface {
	ListWidgets(context.Context, catalogapi.ListWidgetsOptions) (catalogapi.ListWidgetsResponse, error)
}

type Syncer struct {
	client   widgetLister
	pageSize int
}

func New(client *catalogapi.Client, pageSize int) *Syncer {
	return &Syncer{client: client, pageSize: pageSize}
}

// SyncAll walks every page. A repeated nonempty token is rejected so a server
// or client pagination regression cannot spin forever.
func (s *Syncer) SyncAll(ctx context.Context) ([]catalogapi.Widget, error) {
	var widgets []catalogapi.Widget
	token := ""
	seen := make(map[string]struct{})

	for {
		page, err := s.client.ListWidgets(ctx, catalogapi.ListWidgetsOptions{
			PageSize:  s.pageSize,
			PageToken: token,
		})
		if err != nil {
			return nil, err
		}
		widgets = append(widgets, page.Widgets...)
		if page.NextPageToken == "" {
			return widgets, nil
		}
		if _, exists := seen[page.NextPageToken]; exists {
			return nil, fmt.Errorf("pagination token cycle: %q", page.NextPageToken)
		}
		seen[page.NextPageToken] = struct{}{}
		token = page.NextPageToken
	}
}
