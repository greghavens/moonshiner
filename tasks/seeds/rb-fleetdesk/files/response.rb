module FleetDesk
  # Plain response value the dispatcher hands back to the front end.
  class Response
    attr_reader :status, :body

    def initialize(status, body)
      @status = status
      @body = body
    end

    def self.ok(body)
      new(200, body)
    end

    def self.created(body)
      new(201, body)
    end

    def self.not_found(message)
      new(404, { error: message })
    end

    def self.unprocessable(message)
      new(422, { error: message })
    end
  end
end
