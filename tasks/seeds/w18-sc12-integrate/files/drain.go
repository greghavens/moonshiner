package wakedrain

import "errors"

// DrainWake processes the durable inbox until it is empty or an operation
// fails. The low-power wake-budget component has been ported into this package,
// but this legacy loop has not yet been integrated with it.
func DrainWake(inbox Inbox, decoder Decoder, store Store, health Health) (Report, error) {
	var report Report

	if inbox == nil || decoder == nil || store == nil || health == nil {
		return report, ErrInvalidArgument
	}

	for {
		frame, ok, err := inbox.Peek()
		if err != nil {
			return report, err
		}
		if !ok {
			return report, nil
		}

		command, err := decoder.Decode(frame.Payload)
		if err != nil {
			if !errors.Is(err, ErrMalformed) {
				return report, err
			}
			if err := health.RecordMalformed(1); err != nil {
				return report, err
			}
			if err := inbox.Ack(frame.ID); err != nil {
				return report, err
			}
			report.Dropped++
			health.KickWatchdog()
			continue
		}

		// The old radio task retried here to hide occasional flash contention.
		// That policy predates the supervisor's next-wake retry ownership.
		for attempt := 0; attempt < 3; attempt++ {
			err = store.Apply(frame.ID, command)
			if err == nil {
				break
			}
		}
		if err != nil {
			return report, err
		}
		if err := inbox.Ack(frame.ID); err != nil {
			return report, err
		}
		report.Applied++
		health.KickWatchdog()
	}
}
