# frozen_string_literal: true

module EmailDuplicateIncident
  class SerializationFailure < StandardError; end

  Account = Struct.new(
    :id,
    :email,
    :renewed_by_job_id,
    keyword_init: true
  )

  MailIntent = Struct.new(
    :delivery_key,
    :account_id,
    :recipient,
    :template,
    keyword_init: true
  )

  class AuditTrail
    def initialize
      @entries = []
    end

    def record(event, fields = {})
      @entries << fields.merge(event: event).freeze
    end

    def entries(event = nil)
      selected = event ? @entries.select { |entry| entry[:event] == event } : @entries
      selected.map(&:dup)
    end
  end

  class Transaction
    def initialize(accounts:, mail_intents:, attempt:, audit:)
      @accounts = accounts
      @mail_intents = mail_intents
      @attempt = attempt
      @audit = audit
    end

    def account(id)
      account = @accounts.fetch(id)
      Account.new(**account.to_h)
    end

    def mark_renewed(account_id:, job_id:)
      account = @accounts.fetch(account_id)
      return false if account.renewed_by_job_id

      account.renewed_by_job_id = job_id
      @audit.record(
        "renewal_staged",
        account_id: account_id,
        job_id: job_id,
        transaction_attempt: @attempt
      )
      true
    end

    def enqueue_mail(delivery_key:, account_id:, recipient:, template:)
      @mail_intents[delivery_key] ||= MailIntent.new(
        delivery_key: delivery_key,
        account_id: account_id,
        recipient: recipient,
        template: template
      )
      @audit.record(
        "mail_intent_staged",
        delivery_key: delivery_key,
        transaction_attempt: @attempt
      )
      delivery_key
    end
  end

  class TransactionStore
    attr_reader :transaction_attempts

    def initialize(accounts:, audit:, conflicts_before_commit: 0)
      @accounts = accounts.to_h do |attributes|
        account = Account.new(**attributes)
        [account.id, account]
      end
      @mail_intents = {}
      @delivered_keys = {}
      @audit = audit
      @conflicts_before_commit = conflicts_before_commit
      @transaction_attempts = 0
    end

    def transaction(max_retries:)
      attempt = 0

      loop do
        attempt += 1
        @transaction_attempts += 1
        accounts = copy_accounts(@accounts)
        mail_intents = copy_intents(@mail_intents)
        transaction = Transaction.new(
          accounts: accounts,
          mail_intents: mail_intents,
          attempt: attempt,
          audit: @audit
        )
        @audit.record("transaction_started", transaction_attempt: attempt)

        begin
          result = yield transaction
        rescue StandardError => error
          @audit.record(
            "transaction_rolled_back",
            transaction_attempt: attempt,
            reason: error.class.name
          )
          raise
        end

        if @conflicts_before_commit.positive?
          @conflicts_before_commit -= 1
          @audit.record(
            "transaction_rolled_back",
            transaction_attempt: attempt,
            reason: "serialization_failure"
          )

          if attempt <= max_retries
            @audit.record(
              "transaction_retry_scheduled",
              failed_attempt: attempt,
              next_attempt: attempt + 1
            )
            next
          end

          raise SerializationFailure, "commit conflict after #{attempt} attempts"
        end

        @accounts = accounts
        @mail_intents = mail_intents
        @audit.record("transaction_committed", transaction_attempt: attempt)
        return result
      end
    end

    def account(id)
      account = @accounts.fetch(id)
      Account.new(**account.to_h)
    end

    def pending_mail
      @mail_intents.values
                   .reject { |intent| @delivered_keys[intent.delivery_key] }
                   .sort_by(&:delivery_key)
                   .map { |intent| MailIntent.new(**intent.to_h) }
    end

    def mark_mail_delivered(delivery_key)
      raise KeyError, "unknown mail intent #{delivery_key}" unless @mail_intents.key?(delivery_key)

      @delivered_keys[delivery_key] = true
    end

    private

    def copy_accounts(source)
      source.to_h { |id, account| [id, Account.new(**account.to_h)] }
    end

    def copy_intents(source)
      source.to_h { |key, intent| [key, MailIntent.new(**intent.to_h)] }
    end
  end

  class RecordingMailer
    attr_reader :deliveries

    def initialize(audit:)
      @audit = audit
      @deliveries = []
    end

    def deliver(delivery_key:, account_id:, recipient:, template:)
      provider_message_id = "provider-message-#{@deliveries.length + 1}"
      delivery = {
        delivery_key: delivery_key,
        account_id: account_id,
        recipient: recipient,
        template: template,
        provider_message_id: provider_message_id
      }
      @deliveries << delivery.freeze
      @audit.record("provider_accepted_mail", delivery)
      provider_message_id
    end
  end

  class DurableMailQueue
    def initialize(store:, mailer:, audit:)
      @store = store
      @mailer = mailer
      @audit = audit
    end

    def enqueue(transaction:, delivery_key:, account_id:, recipient:, template:)
      transaction.enqueue_mail(
        delivery_key: delivery_key,
        account_id: account_id,
        recipient: recipient,
        template: template
      )
    end

    # Immediate delivery is intentionally available for non-transactional callers.
    # A retried transaction must use enqueue so a rolled-back attempt has no effect.
    def deliver_now(delivery_key:, account_id:, recipient:, template:)
      @mailer.deliver(
        delivery_key: delivery_key,
        account_id: account_id,
        recipient: recipient,
        template: template
      )
    end

    def drain
      delivered = []
      @store.pending_mail.each do |intent|
        @mailer.deliver(**intent.to_h)
        @store.mark_mail_delivered(intent.delivery_key)
        @audit.record("durable_mail_dispatched", delivery_key: intent.delivery_key)
        delivered << intent.delivery_key
      end
      delivered
    end
  end

  class RenewalReceiptJob
    MAX_RETRIES = 2
    TEMPLATE = "renewal_receipt"

    def initialize(store:, mail_queue:, audit:)
      @store = store
      @mail_queue = mail_queue
      @audit = audit
    end

    def perform(account_id:, job_id:)
      @audit.record("job_started", account_id: account_id, job_id: job_id)

      result = @store.transaction(max_retries: MAX_RETRIES) do |transaction|
        account = transaction.account(account_id)

        if account.renewed_by_job_id
          :already_renewed
        else
          transaction.mark_renewed(account_id: account_id, job_id: job_id)
          delivery_key = "renewal-receipt:#{account_id}"
          @mail_queue.deliver_now(
            delivery_key: delivery_key,
            account_id: account_id,
            recipient: account.email,
            template: TEMPLATE
          )
          :renewed
        end
      end

      @audit.record("job_completed", account_id: account_id, job_id: job_id, result: result)
      result
    rescue StandardError => error
      @audit.record(
        "job_failed",
        account_id: account_id,
        job_id: job_id,
        error: error.class.name
      )
      raise
    end
  end
end
