package router

import "fmt"

// Handler is invoked with the item routed to its queue.
type Handler func(item map[string]any)

// Router dispatches items through a rule set to registered handlers.
type Router struct {
	rules    []Rule
	handlers map[string]Handler
}

// NewRouter builds a Router over a loaded rule set.
func NewRouter(rs *RuleSet) *Router {
	return &Router{rules: rs.Rules, handlers: map[string]Handler{}}
}

// Handle registers fn for a destination queue.
func (r *Router) Handle(queue string, fn Handler) {
	r.handlers[queue] = fn
}

// Dispatch routes item through the switch rules, invokes the handler
// registered for the destination queue, and returns the queue name.
func (r *Router) Dispatch(item map[string]any) (string, error) {
	dest := ""
	for _, rule := range r.rules {
		applies := true
		if rule.When != "" {
			ok, err := evalCond(rule.When, item)
			if err != nil {
				return "", err
			}
			applies = ok
		}
		if !applies {
			continue
		}
		dest = rule.Then
		if h := r.handlers[rule.Then]; h != nil {
			h(item)
		}
	}
	if dest == "" {
		return "", fmt.Errorf("no route matched")
	}
	return dest, nil
}
