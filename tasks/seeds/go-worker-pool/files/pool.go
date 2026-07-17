// Package pool provides a fixed-size worker pool for CPU-bound transforms.
package pool

import "sync"

// Process applies fn to every item using the given number of workers and
// returns the results (order not guaranteed).
func Process(items []int, workers int, fn func(int) int) []int {
	if workers < 1 {
		workers = 1
	}
	results := make([]int, 0, len(items))
	jobs := make(chan int)
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		go func() {
			wg.Add(1)
			defer wg.Done()
			for item := range jobs {
				results = append(results, fn(item))
			}
		}()
	}
	for _, item := range items {
		jobs <- item
	}
	wg.Wait()
	return results
}
