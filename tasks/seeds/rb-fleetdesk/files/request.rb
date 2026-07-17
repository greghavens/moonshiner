module FleetDesk
  # One inbound request exactly as the kiosk front end hands it over.
  class Request
    attr_reader :verb, :path, :params

    def initialize(verb, path, params = {})
      @verb = verb
      @path = path
      @params = params
    end
  end
end
