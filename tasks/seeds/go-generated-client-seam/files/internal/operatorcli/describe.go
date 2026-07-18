package operatorcli

import (
	"context"
	"fmt"

	"go-generated-client-seam/internal/fleetcompat"
)

func DescribeNode(ctx context.Context, nodes fleetcompat.NodeLookup, nodeID string) (string, error) {
	node, err := nodes.Lookup(ctx, nodeID)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%s\t%s\t%s", node.ID, node.Name, node.Endpoint), nil
}
