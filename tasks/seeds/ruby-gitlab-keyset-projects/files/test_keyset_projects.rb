# frozen_string_literal: true

# Acceptance harness for the GitLab project-inventory tool. Runs a loopback
# fake GitLab REST v4 API pinning the wire contract in docs/contract.json.
# No real GitLab, no real credentials, no network, no sleeps.
# Protected — do not modify. Run: ruby test_keyset_projects.rb

require "minitest/autorun"
require "json"
require "socket"
require "uri"

require_relative "gitlab_client"
require_relative "project_inventory"

TOKEN = "glpat-dummy-1nvent0ry-77"
API = "/api/v4/projects"

# Tiny scripted loopback HTTP origin. Routes are keyed on method + path +
# order-insensitive query; every request is recorded with its RAW target so
# tests can prove Link URLs were followed verbatim.
class FakeGitLab
  Request = Struct.new(:method, :target, :path, :query, :headers)

  attr_reader :requests

  def initialize
    @requests = []
    @routes = {}
    @server = TCPServer.new("127.0.0.1", 0)
    @port = @server.addr[1]
    @thread = Thread.new { serve }
  end

  def base_url
    "http://127.0.0.1:#{@port}"
  end

  def route(method, path, query, status, headers, body)
    key = [method, path, query.map { |k, v| [k.to_s, v.to_s] }.sort]
    @routes[key] = [status, headers, body]
  end

  def shutdown
    @server.close
    @thread.kill
  end

  private

  def serve
    loop do
      sock = @server.accept
      Thread.new(sock) { |s| handle(s) }
    end
  rescue IOError, Errno::EBADF
    nil
  end

  def handle(sock)
    request_line = sock.gets
    return sock.close if request_line.nil?

    method, target, = request_line.split(" ", 3)
    headers = {}
    while (line = sock.gets) && line != "\r\n"
      name, value = line.split(":", 2)
      headers[name.strip.downcase] = value.to_s.strip
    end

    uri = URI.parse(target)
    query = {}
    URI.decode_www_form(uri.query || "").each { |k, v| query[k] = v }
    @requests << Request.new(method, target, uri.path, query, headers)

    key = [method, uri.path, query.map { |k, v| [k, v] }.sort]
    status, extra, body = @routes[key] ||
                          [599, {}, { "message" => "unscripted #{method} #{target}" }]
    payload = body.is_a?(String) ? body : JSON.generate(body)
    head = +"HTTP/1.1 #{status} Scripted\r\n"
    head << "Content-Type: application/json\r\n"
    head << "Content-Length: #{payload.bytesize}\r\n"
    extra.each { |k, v| head << "#{k}: #{v}\r\n" }
    head << "Connection: close\r\n\r\n"
    sock.write(head + payload)
  rescue Errno::EPIPE, IOError
    nil
  ensure
    sock.close unless sock.closed?
  end
end

def project(id, path_with_namespace, emails_disabled:, visibility: "private", archived: false)
  name = path_with_namespace.split("/").last
  {
    "id" => id,
    "description" => "Internal service #{name}",
    "name" => name,
    "path" => name,
    "path_with_namespace" => path_with_namespace,
    "default_branch" => "main",
    "visibility" => visibility,
    "archived" => archived,
    "emails_disabled" => emails_disabled,
    "created_at" => "2025-11-0#{(id % 9) + 1}T09:00:00.000Z",
    "last_activity_at" => "2026-07-01T09:00:00.000Z",
    "web_url" => "https://gitlab.example.com/#{path_with_namespace}",
  }
end

PROJ_101 = project(101, "platform/deploy-runner", emails_disabled: true)
PROJ_207 = project(207, "platform/metrics-relay", emails_disabled: false, visibility: "internal")
PROJ_310 = project(310, "dev-tools/widget-lab", emails_disabled: nil, archived: true)

class KeysetProjectsTest < Minitest::Test
  def setup
    @gitlab = FakeGitLab.new
    @client = GitlabClient.new(base_url: @gitlab.base_url, token: TOKEN)
    @inventory = ProjectInventory.new(@client)
  end

  def teardown
    @gitlab.shutdown
  end

  def wire_keyset_pages
    base = @gitlab.base_url
    # Deliberately reordered params in the Link URLs: a client that rebuilds
    # the query instead of following the documented URL verbatim will miss.
    next1 = "#{API}?per_page=2&id_after=207&pagination=keyset&sort=asc&order_by=id"
    next2 = "#{API}?id_after=310&per_page=2&pagination=keyset&sort=asc&order_by=id"
    first = "#{API}?pagination=keyset&per_page=2&order_by=id&sort=asc"

    @gitlab.route(
      "GET", API,
      { "pagination" => "keyset", "order_by" => "id", "sort" => "asc", "per_page" => "2" },
      200,
      { "Link" => "<#{base}#{next1}>; rel=\"next\", <#{base}#{first}>; rel=\"first\"" },
      [PROJ_101, PROJ_207]
    )
    @gitlab.route(
      "GET", API,
      { "pagination" => "keyset", "order_by" => "id", "sort" => "asc",
        "per_page" => "2", "id_after" => "207" },
      200,
      { "Link" => "<#{base}#{next2}>; rel=\"next\", <#{base}#{first}>; rel=\"first\"" },
      [PROJ_310]
    )
    # Final page: empty array and NO Link header, per the documented contract.
    @gitlab.route(
      "GET", API,
      { "pagination" => "keyset", "order_by" => "id", "sort" => "asc",
        "per_page" => "2", "id_after" => "310" },
      200, {}, []
    )
    [next1, next2]
  end

  def test_keyset_pagination_follows_link_urls_verbatim
    next1, next2 = wire_keyset_pages
    projects = @inventory.fetch_all(per_page: 2)

    assert_equal [101, 207, 310], projects.map { |p| p["id"] }
    assert_equal 3, @gitlab.requests.length, "expected exactly three keyset requests"

    first = @gitlab.requests[0]
    assert_equal "GET", first.method
    assert_equal API, first.path
    expected_first = {
      "pagination" => "keyset", "order_by" => "id",
      "sort" => "asc", "per_page" => "2"
    }
    assert_equal expected_first, first.query,
                 "first keyset request must send exactly the documented controls"

    # The documented rule: request the URL from the Link header, do not
    # rebuild it. Raw targets must match byte-for-byte.
    assert_equal next1, @gitlab.requests[1].target
    assert_equal next2, @gitlab.requests[2].target

    @gitlab.requests.each do |req|
      assert_equal TOKEN, req.headers["private-token"],
                   "PRIVATE-TOKEN must ride on every request (#{req.target})"
    end
  end

  def test_offset_pagination_follows_x_next_page
    @gitlab.route(
      "GET", API,
      { "order_by" => "id", "sort" => "asc", "per_page" => "2", "page" => "1" },
      200,
      { "x-page" => "1", "x-per-page" => "2", "x-total" => "3",
        "x-total-pages" => "2", "x-next-page" => "2", "x-prev-page" => "" },
      [PROJ_101, PROJ_207]
    )
    @gitlab.route(
      "GET", API,
      { "order_by" => "id", "sort" => "asc", "per_page" => "2", "page" => "2" },
      200,
      { "x-page" => "2", "x-per-page" => "2", "x-total" => "3",
        "x-total-pages" => "2", "x-next-page" => "", "x-prev-page" => "1" },
      [PROJ_310]
    )

    projects = @inventory.fetch_all(mode: :offset, per_page: 2)
    assert_equal [101, 207, 310], projects.map { |p| p["id"] }
    assert_equal 2, @gitlab.requests.length,
                 "an empty x-next-page ends offset pagination"
    assert_equal "2", @gitlab.requests[1].query["page"]
    assert_equal "2", @gitlab.requests[1].query["per_page"]
  end

  def test_report_preserves_null_booleans_distinctly
    wire_keyset_pages
    report = @inventory.report(@inventory.fetch_all(per_page: 2))

    rows = report["rows"]
    assert_equal [101, 207, 310], rows.map { |r| r["id"] }, "rows must be sorted by id"

    by_id = rows.to_h { |r| [r["id"], r] }
    assert_equal true, by_id[101]["emails_disabled"]
    assert_equal false, by_id[207]["emails_disabled"]
    assert by_id[310].key?("emails_disabled"), "null boolean column must not be dropped"
    assert_nil by_id[310]["emails_disabled"],
               "a null boolean is neither true nor false and must stay null"
    assert_equal "dev-tools/widget-lab", by_id[310]["path_with_namespace"]
    assert_equal "internal", by_id[207]["visibility"]
    assert_equal true, by_id[310]["archived"]

    # Serialized output keeps JSON null (not false, not missing).
    row_json = JSON.generate(by_id[310])
    assert_includes row_json, "\"emails_disabled\":null"

    summary = report["summary"]
    assert_equal 3, summary["total"]
    assert_equal({ "true" => 1, "false" => 1, "unknown" => 1 },
                 summary["emails_disabled_counts"])
  end

  def test_moved_project_redirect_is_followed
    moved_target = "#{API}/dev-tools%2Fold-widget"
    @gitlab.route(
      "GET", moved_target, {},
      301,
      { "Location" => "#{@gitlab.base_url}#{API}/8842" },
      { "message" => "This resource has been moved permanently to #{@gitlab.base_url}#{API}/8842" }
    )
    @gitlab.route(
      "GET", "#{API}/8842", {},
      200, {},
      project(8842, "dev-tools/widget-lab", emails_disabled: nil)
    )

    fetched = @inventory.fetch_project("dev-tools/old-widget")
    assert_equal 8842, fetched["id"]

    assert_equal 2, @gitlab.requests.length
    assert_equal moved_target, @gitlab.requests[0].target,
                 "namespaced path must travel URL-encoded (%2F)"
    assert_equal "#{API}/8842", @gitlab.requests[1].path,
                 "the Location endpoint must be used after a moved-resource response"
    assert_equal TOKEN, @gitlab.requests[1].headers["private-token"]
  end

  def test_redirect_following_is_bounded
    looping = "#{API}/dev-tools%2Fold-widget"
    @gitlab.route(
      "GET", looping, {},
      301,
      { "Location" => "#{@gitlab.base_url}#{looping}" },
      { "message" => "This resource has been moved permanently to itself" }
    )

    err = assert_raises(GitlabClient::RedirectLoopError) do
      @inventory.fetch_project("dev-tools/old-widget")
    end
    assert_match(/redirect/i, err.message)
    assert_operator @gitlab.requests.length, :<=, 8,
                    "redirect following must be bounded"
  end

  def test_auth_failure_never_echoes_token
    @gitlab.route(
      "GET", API,
      { "pagination" => "keyset", "order_by" => "id", "sort" => "asc", "per_page" => "2" },
      401, {}, { "message" => "401 Unauthorized" }
    )

    err = assert_raises(GitlabClient::AuthError) { @inventory.fetch_all(per_page: 2) }
    assert_includes err.message, "401"
    refute_includes err.message, TOKEN, "token must never appear in errors"
  end

  def test_api_error_carries_status_and_message
    @gitlab.route(
      "GET", API,
      { "pagination" => "keyset", "order_by" => "id", "sort" => "asc", "per_page" => "2" },
      500, {}, { "message" => "500 Internal Server Error" }
    )

    err = assert_raises(GitlabClient::ApiError) { @inventory.fetch_all(per_page: 2) }
    assert_equal 500, err.status
    assert_includes err.message, "500 Internal Server Error"
  end
end
