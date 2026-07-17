# frozen_string_literal: true

# Minimal Terraform Enterprise /api/v2 transport (stdlib only).
#
# Small and already in production: sends the bearer token and JSON:API
# media type, and never raises on non-2xx responses — callers get the
# status and parsed body back so they can decode JSON:API error documents
# themselves.

require "json"
require "net/http"
require "uri"

class TFEClient
  API_MEDIA_TYPE = "application/vnd.api+json"

  METHODS = {
    "GET" => Net::HTTP::Get,
    "POST" => Net::HTTP::Post,
    "PATCH" => Net::HTTP::Patch,
    "DELETE" => Net::HTTP::Delete,
  }.freeze

  attr_reader :base_url, :token

  def initialize(base_url, token)
    @base_url = base_url.sub(%r{/+\z}, "")
    @token = token
  end

  # Send one /api/v2 request; +path+ may include a query string.
  # Returns [status, parsed_json_or_nil]. Non-2xx responses are not
  # raised; their status and parsed error document are returned.
  def request(method, path, body: nil)
    uri = URI.parse(@base_url + path)
    req = METHODS.fetch(method).new(uri)
    req["Authorization"] = "Bearer #{@token}"
    if body
      req["Content-Type"] = API_MEDIA_TYPE
      req.body = JSON.generate(body)
    end
    resp = Net::HTTP.start(uri.host, uri.port) { |http| http.request(req) }
    raw = resp.body.to_s
    [resp.code.to_i, raw.empty? ? nil : JSON.parse(raw)]
  end
end
