package router

import (
	"fmt"
	"strconv"
	"strings"
)

// evalCond evaluates one rule condition against an item.
//
// A condition is a dotted field path, optionally compared to a literal:
//
//	.rush                     truthy check on the field
//	.total >= 100             numeric comparison
//	.customer.tier == "gold"  string equality
//
// Operators: == != > >= < <=. Literals are numbers, double-quoted
// strings, or true/false. Missing fields resolve to nil, which is falsy.
func evalCond(cond string, item map[string]any) (bool, error) {
	fields := strings.Fields(cond)
	switch len(fields) {
	case 1:
		val, err := lookup(fields[0], item)
		if err != nil {
			return false, err
		}
		return truthy(val), nil
	case 3:
		val, err := lookup(fields[0], item)
		if err != nil {
			return false, err
		}
		lit, err := parseLiteral(fields[2])
		if err != nil {
			return false, err
		}
		return compare(val, fields[1], lit)
	default:
		return false, fmt.Errorf("malformed condition %q", cond)
	}
}

func lookup(path string, item map[string]any) (any, error) {
	if !strings.HasPrefix(path, ".") || path == "." {
		return nil, fmt.Errorf("condition path %q must start with '.'", path)
	}
	var cur any = item
	for _, seg := range strings.Split(path[1:], ".") {
		m, ok := cur.(map[string]any)
		if !ok {
			return nil, nil
		}
		cur = m[seg]
	}
	return cur, nil
}

func parseLiteral(tok string) (any, error) {
	switch {
	case tok == "true":
		return true, nil
	case tok == "false":
		return false, nil
	case len(tok) >= 2 && strings.HasPrefix(tok, `"`) && strings.HasSuffix(tok, `"`):
		return tok[1 : len(tok)-1], nil
	}
	if n, err := strconv.ParseFloat(tok, 64); err == nil {
		return n, nil
	}
	return nil, fmt.Errorf("bad literal %q in condition", tok)
}

func compare(left any, op string, lit any) (bool, error) {
	if ln, ok := toNumber(left); ok {
		if rn, ok := toNumber(lit); ok {
			switch op {
			case "==":
				return ln == rn, nil
			case "!=":
				return ln != rn, nil
			case ">":
				return ln > rn, nil
			case ">=":
				return ln >= rn, nil
			case "<":
				return ln < rn, nil
			case "<=":
				return ln <= rn, nil
			}
			return false, fmt.Errorf("unknown operator %q", op)
		}
	}
	switch op {
	case "==":
		return left == lit, nil
	case "!=":
		return left != lit, nil
	case ">", ">=", "<", "<=":
		return false, fmt.Errorf("operator %q needs two numbers", op)
	}
	return false, fmt.Errorf("unknown operator %q", op)
}

func toNumber(v any) (float64, bool) {
	switch n := v.(type) {
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case float64:
		return n, true
	}
	return 0, false
}

func truthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	default:
		if n, ok := toNumber(v); ok {
			return n != 0
		}
		return true
	}
}
