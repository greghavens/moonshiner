package scheduler

import (
	"context"
	"fmt"

	"go-generated-client-seam/internal/fleetcompat"
)

type Service struct {
	nodes fleetcompat.NodeLookup
}

func New(nodes fleetcompat.NodeLookup) *Service {
	return &Service{nodes: nodes}
}

func (s *Service) RouteJob(ctx context.Context, nodeID, jobID string) (string, error) {
	node, err := s.nodes.Lookup(ctx, nodeID)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%s/jobs/%s", node.Endpoint, jobID), nil
}
