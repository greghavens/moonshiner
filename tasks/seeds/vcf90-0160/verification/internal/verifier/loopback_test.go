package verifier_test

import "net"

func newLocalListener() (net.Listener, error) {
	return net.Listen("tcp", "127.0.0.1:0")
}
