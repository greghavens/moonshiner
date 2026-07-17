# frozen_string_literal: true

# Acceptance harness for the team-access reconciler. Runs a loopback fake
# Terraform Enterprise pinning the wire contract in docs/contract.json.
# No real TFE, no real credentials, no network.
# Protected — do not modify. Run: ruby test_team_access.rb

require "minitest/autorun"
require "json"
require "socket"
require "uri"

require_relative "tfe_client"
require_relative "team_access_reconciler"

TOKEN = "test-token-rb4402" # dummy credential
WS = "ws-DiTm4pR2fjfHyXk7"
LIST_PATH = "/api/v2/team-workspaces"

NOT_FOUND_DOC = {
  "errors" => [
    {
      "status" => "404",
      "title" => "not found",
      "detail" => "Team access not found or user unauthorized to perform action",
    },
  ],
}.freeze

# Tiny scripted loopback HTTP server: routes[[method, path]] -> [[status, body], ...]
class FakeTFE
  attr_reader :requests

  def initialize
    @requests = []
    @routes = Hash.new { |h, k| h[k] = [] }
    @server = TCPServer.new("127.0.0.1", 0)
    @port = @server.addr[1]
    @thread = Thread.new { serve }
  end

  def base_url
    "http://127.0.0.1:#{@port}"
  end

  def route(method, path, status, body = nil)
    @routes[[method, path]] << [status, body]
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

  def take(method, path)
    queue = @routes[[method, path]]
    if queue.empty?
      [599, { "errors" => [{ "status" => "599", "title" => "unscripted",
                             "detail" => "#{method} #{path}" }] }]
    elsif queue.length > 1
      queue.shift
    else
      queue.first
    end
  end

  def handle(sock)
    request_line = sock.gets
    return sock.close if request_line.nil?

    method, target, = request_line.split(" ")
    headers = {}
    while (line = sock.gets)
      line = line.strip
      break if line.empty?

      key, value = line.split(":", 2)
      headers[key.downcase] = value.to_s.strip
    end
    length = headers["content-length"].to_i
    raw = length.positive? ? sock.read(length) : nil
    path, query = target.split("?", 2)
    @requests << {
      "method" => method,
      "path" => path,
      "query" => query,
      "params" => query ? URI.decode_www_form(query).to_h : {},
      "auth" => headers["authorization"],
      "content_type" => headers["content-type"],
      "body" => raw && JSON.parse(raw),
    }
    status, body = take(method, path)
    payload = body ? JSON.generate(body) : ""
    head = +"HTTP/1.1 #{status} Status\r\nContent-Length: #{payload.bytesize}\r\n"
    head << "Content-Type: application/vnd.api+json\r\n" unless payload.empty?
    head << "Connection: close\r\n\r\n"
    sock.write(head + payload)
    sock.close
  rescue StandardError
    begin
      sock.close
    rescue StandardError
      nil
    end
  end
end

def tws(id, team_id, access)
  {
    "id" => id,
    "type" => "team-workspaces",
    "attributes" => { "access" => access },
    "relationships" => {
      "team" => { "data" => { "id" => team_id, "type" => "teams" } },
      "workspace" => { "data" => { "id" => WS, "type" => "workspaces" } },
    },
  }
end

def page_doc(items, current, total_pages, total_count)
  {
    "data" => items,
    "meta" => {
      "pagination" => {
        "current-page" => current,
        "prev-page" => current > 1 ? current - 1 : nil,
        "next-page" => current < total_pages ? current + 1 : nil,
        "total-pages" => total_pages,
        "total-count" => total_count,
      },
    },
  }
end

class TeamAccessTest < Minitest::Test
  def setup
    @fake = FakeTFE.new
    @client = TFEClient.new(@fake.base_url, TOKEN)
  end

  def teardown
    @fake.shutdown
  end

  def current_three
    [
      { "id" => "tws-1", "team_id" => "team-alpha", "access" => "read" },
      { "id" => "tws-2", "team_id" => "team-bravo", "access" => "write" },
      { "id" => "tws-3", "team_id" => "team-echo", "access" => "admin" },
    ]
  end

  # -------------------------------------------------------------- transport

  def test_existing_transport_behavior_is_unchanged
    @fake.route("GET", "/api/v2/ping", 200, { "data" => [] })
    status, body = @client.request("GET", "/api/v2/ping")
    assert_equal 200, status
    assert_equal({ "data" => [] }, body)

    @fake.route("GET", "/api/v2/gone", 404, NOT_FOUND_DOC)
    status, body = @client.request("GET", "/api/v2/gone")
    assert_equal 404, status, "transport hands back non-2xx, it does not raise"
    assert_equal NOT_FOUND_DOC, body

    @fake.route("POST", "/api/v2/ping", 200, { "data" => {} })
    @client.request("POST", "/api/v2/ping", body: { "data" => {} })
    post = @fake.requests.last
    assert_equal "Bearer #{TOKEN}", post["auth"]
    assert_equal "application/vnd.api+json", post["content_type"]
  end

  # ------------------------------------------------------------------ fetch

  def test_fetch_pages_with_documented_params
    @fake.route("GET", LIST_PATH, 200,
                page_doc([tws("tws-1", "team-alpha", "read"),
                          tws("tws-2", "team-bravo", "write")], 1, 2, 3))
    @fake.route("GET", LIST_PATH, 200,
                page_doc([tws("tws-3", "team-echo", "admin")], 2, 2, 3))

    current = TeamAccess.fetch(@client, WS, page_size: 2)

    lists = @fake.requests.select { |r| r["path"] == LIST_PATH }
    assert_equal 2, lists.length, "follow meta.pagination next-page exactly once here"
    assert_equal({ "filter[workspace][id]" => WS, "page[number]" => "1", "page[size]" => "2" },
                 lists[0]["params"])
    assert_equal({ "filter[workspace][id]" => WS, "page[number]" => "2", "page[size]" => "2" },
                 lists[1]["params"], "every page repeats the workspace filter")
    assert_equal "Bearer #{TOKEN}", lists[0]["auth"]

    assert_equal current_three, current
  end

  def test_fetch_stops_on_null_next_page
    @fake.route("GET", LIST_PATH, 200,
                page_doc([tws("tws-1", "team-alpha", "read")], 1, 1, 1))
    current = TeamAccess.fetch(@client, WS, page_size: 2)
    assert_equal 1, @fake.requests.length
    assert_equal [{ "id" => "tws-1", "team_id" => "team-alpha", "access" => "read" }], current
  end

  def test_fetch_404_is_not_found_or_unauthorized
    @fake.route("GET", LIST_PATH, 404, NOT_FOUND_DOC)
    err = assert_raises(TeamAccess::NotFoundOrUnauthorized) do
      TeamAccess.fetch(@client, WS, page_size: 2)
    end
    assert_kind_of TeamAccess::APIError, err
    assert_equal 404, err.status
    assert_match(/unauthorized/, err.message,
                 "a TFE 404 masks authorization; the message must say so")
    assert_match(/not found/i, err.message)
  end

  # ------------------------------------------------------------------- plan

  def test_plan_minimum_change_set_sorted_by_team
    desired = {
      "team-alpha" => "read",
      "team-zulu" => "plan",
      "team-bravo" => "admin",
      "team-delta" => "plan",
    }
    plan = TeamAccess.plan(current_three, desired)

    assert_equal [{ "team_id" => "team-delta", "access" => "plan" },
                  { "team_id" => "team-zulu", "access" => "plan" }],
                 plan["add"], "adds sorted by team id"
    assert_equal [{ "id" => "tws-2", "team_id" => "team-bravo",
                    "from" => "write", "to" => "admin" }],
                 plan["change"]
    assert_equal [{ "id" => "tws-3", "team_id" => "team-echo", "access" => "admin" }],
                 plan["remove"]
  end

  def test_plan_rejects_undocumented_access_levels
    assert_raises(ArgumentError) do
      TeamAccess.plan(current_three, { "team-alpha" => "owner" })
    end
  end

  def test_format_plan_is_deterministic
    plan = TeamAccess.plan(current_three,
                           { "team-alpha" => "read",
                             "team-bravo" => "admin",
                             "team-delta" => "plan" })
    assert_equal [
      "+ team-delta plan",
      "~ team-bravo write -> admin (tws-2)",
      "- team-echo admin (tws-3)",
    ], TeamAccess.format_plan(plan)
  end

  # ------------------------------------------------------------------ apply

  def make_plan
    TeamAccess.plan(current_three,
                    { "team-alpha" => "read",
                      "team-bravo" => "admin",
                      "team-delta" => "plan" })
  end

  def test_apply_request_shapes_and_order
    @fake.route("POST", LIST_PATH, 200, { "data" => tws("tws-9", "team-delta", "plan") })
    @fake.route("PATCH", "#{LIST_PATH}/tws-2", 200,
                { "data" => tws("tws-2", "team-bravo", "admin") })
    @fake.route("DELETE", "#{LIST_PATH}/tws-3", 204)

    report = TeamAccess.apply(@client, WS, make_plan)
    assert_equal ["add team-delta plan", "change team-bravo write->admin", "remove team-echo"],
                 report

    ops = @fake.requests.map { |r| [r["method"], r["path"]] }
    assert_equal [["POST", LIST_PATH],
                  ["PATCH", "#{LIST_PATH}/tws-2"],
                  ["DELETE", "#{LIST_PATH}/tws-3"]],
                 ops, "adds, then changes, then removes"

    post = @fake.requests[0]
    assert_equal "application/vnd.api+json", post["content_type"]
    assert_equal({ "data" => {
                   "type" => "team-workspaces",
                   "attributes" => { "access" => "plan" },
                   "relationships" => {
                     "team" => { "data" => { "type" => "teams", "id" => "team-delta" } },
                     "workspace" => { "data" => { "type" => "workspaces", "id" => WS } },
                   },
                 } }, post["body"])

    patch = @fake.requests[1]
    assert_equal "application/vnd.api+json", patch["content_type"]
    assert_equal "team-workspaces", patch["body"]["data"]["type"]
    assert_equal "tws-2", patch["body"]["data"]["id"],
                 "PATCH bodies repeat the record id in data.id"
    assert_equal({ "access" => "admin" }, patch["body"]["data"]["attributes"])

    delete = @fake.requests[2]
    assert_nil delete["body"], "DELETE sends no body"
  end

  def test_apply_skips_masked_404_on_write_and_continues
    @fake.route("PATCH", "#{LIST_PATH}/tws-2", 404, NOT_FOUND_DOC)
    @fake.route("DELETE", "#{LIST_PATH}/tws-3", 204)

    plan = TeamAccess.plan(current_three,
                           { "team-alpha" => "read", "team-bravo" => "admin" })
    report = TeamAccess.apply(@client, WS, plan)
    assert_equal ["skip team-bravo (missing or unauthorized)", "remove team-echo"],
                 report
    ops = @fake.requests.map { |r| [r["method"], r["path"]] }
    assert_equal [["PATCH", "#{LIST_PATH}/tws-2"], ["DELETE", "#{LIST_PATH}/tws-3"]], ops,
                 "a masked 404 mid-reconcile must not stop the remaining operations"
  end

  def test_apply_aborts_when_create_is_masked
    @fake.route("POST", LIST_PATH, 404, NOT_FOUND_DOC)
    err = assert_raises(TeamAccess::NotFoundOrUnauthorized) do
      TeamAccess.apply(@client, WS, make_plan)
    end
    assert_equal 404, err.status
    assert_equal 1, @fake.requests.length,
                 "nothing can be created against an invisible team/workspace: abort"
  end

  def test_apply_raises_structured_error_on_other_failures
    @fake.route("POST", LIST_PATH, 422,
                { "errors" => [{ "status" => "422", "title" => "invalid attribute",
                                 "detail" => "Access is not included in the list" }] })
    err = assert_raises(TeamAccess::APIError) do
      TeamAccess.apply(@client, WS, make_plan)
    end
    assert_equal 422, err.status
    assert_equal "invalid attribute", err.errors[0]["title"]
    assert_match(/Access is not included in the list/, err.message)
  end

  # -------------------------------------------------------------- reconcile

  def test_reconcile_dry_run_reads_only
    @fake.route("GET", LIST_PATH, 200, page_doc(
                  [tws("tws-1", "team-alpha", "read"),
                   tws("tws-2", "team-bravo", "write"),
                   tws("tws-3", "team-echo", "admin")], 1, 1, 3
                ))
    out = TeamAccess.reconcile(@client, WS,
                               { "team-alpha" => "read", "team-bravo" => "admin" },
                               dry_run: true)
    assert_equal ["~ team-bravo write -> admin (tws-2)", "- team-echo admin (tws-3)"],
                 out["lines"]
    assert_equal [], out["report"]
    assert_equal ["GET"], @fake.requests.map { |r| r["method"] }.uniq,
                 "a dry run must not issue a single write request"
  end

  def test_reconcile_end_to_end
    @fake.route("GET", LIST_PATH, 200, page_doc(
                  [tws("tws-1", "team-alpha", "read"),
                   tws("tws-2", "team-bravo", "write"),
                   tws("tws-3", "team-echo", "admin")], 1, 1, 3
                ))
    @fake.route("POST", LIST_PATH, 200, { "data" => tws("tws-9", "team-delta", "plan") })
    @fake.route("PATCH", "#{LIST_PATH}/tws-2", 200,
                { "data" => tws("tws-2", "team-bravo", "admin") })
    @fake.route("DELETE", "#{LIST_PATH}/tws-3", 204)

    out = TeamAccess.reconcile(@client, WS,
                               { "team-alpha" => "read",
                                 "team-bravo" => "admin",
                                 "team-delta" => "plan" })
    assert_equal ["add team-delta plan", "change team-bravo write->admin", "remove team-echo"],
                 out["report"]
    assert_equal [["GET", LIST_PATH],
                  ["POST", LIST_PATH],
                  ["PATCH", "#{LIST_PATH}/tws-2"],
                  ["DELETE", "#{LIST_PATH}/tws-3"]],
                 @fake.requests.map { |r| [r["method"], r["path"]] }
  end
end
