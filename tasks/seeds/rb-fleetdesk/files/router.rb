module FleetDesk
  # Tiny first-match-wins route table. Path patterns are literal segments
  # or :captures ("/work_orders/:id"); no globs, no regexps.
  class Router
    Route = Struct.new(:verb, :segments, :handler)

    def initialize
      @routes = []
    end

    def get(path, &handler)
      add("GET", path, handler)
    end

    def post(path, &handler)
      add("POST", path, handler)
    end

    # [handler, captured params] for the first route that matches, else nil.
    def match(verb, path)
      parts = split(path)
      @routes.each do |route|
        next unless route.verb == verb

        params = match_segments(route.segments, parts)
        return [route.handler, params] if params
      end
      nil
    end

    private

    def add(verb, path, handler)
      @routes << Route.new(verb, split(path), handler)
    end

    def split(path)
      path.split("/").reject(&:empty?)
    end

    def match_segments(pattern, parts)
      return nil unless pattern.length == parts.length

      params = {}
      pattern.zip(parts) do |expected, actual|
        if expected.start_with?(":")
          params[expected[1..].to_sym] = actual
        elsif expected != actual
          return nil
        end
      end
      params
    end
  end
end
