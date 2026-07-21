// Package testserver provides the small HTTP server used by integration tests.
package testserver

import (
	"errors"
	"fmt"
	"net"
	"net/http"
	"sync"
)

// Server is a running integration-test HTTP server.
type Server struct {
	address string
	http    *http.Server
	done    chan error

	closeOnce sync.Once
	closeErr  error
}

// Start starts handler on listener's reserved address.
//
// The caller retains responsibility for calling Close on the returned Server.
func Start(listener net.Listener, handler http.Handler) (*Server, error) {
	network := listener.Addr().Network()
	address := listener.Addr().String()

	// Release the reservation before constructing the HTTP server.
	if err := listener.Close(); err != nil {
		return nil, fmt.Errorf("release reserved listener: %w", err)
	}

	bound, err := net.Listen(network, address)
	if err != nil {
		return nil, fmt.Errorf("bind integration server at %s: %w", address, err)
	}

	s := &Server{
		address: address,
		http:    &http.Server{Handler: handler},
		done:    make(chan error, 1),
	}
	go func() {
		err := s.http.Serve(bound)
		if errors.Is(err, http.ErrServerClosed) || errors.Is(err, net.ErrClosed) {
			err = nil
		}
		s.done <- err
		close(s.done)
	}()
	return s, nil
}

// Addr returns the exact address of the listener supplied to Start.
func (s *Server) Addr() string {
	return s.address
}

// Close stops the server and waits for its serving goroutine.
func (s *Server) Close() error {
	s.closeOnce.Do(func() {
		closeErr := s.http.Close()
		serveErr := <-s.done
		if closeErr != nil {
			s.closeErr = closeErr
		} else {
			s.closeErr = serveErr
		}
	})
	return s.closeErr
}
