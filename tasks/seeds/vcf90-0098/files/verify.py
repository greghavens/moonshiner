#!/usr/bin/env python3
"""Deterministic verifier for the single-file Java client."""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
START_TIMESTAMP = 1_700_000_000_100

PROBE_SOURCE = r'''
import java.io.IOException;
import java.net.Authenticator;
import java.net.CookieHandler;
import java.net.ProxySelector;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.Executor;
import java.util.concurrent.Flow;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSession;

public final class ClientProbe {
    private static final long START = 1_700_000_000_100L;
    private static final String TOKEN = "vcf90-0098-session";

    private ClientProbe() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            throw new IllegalArgumentException(
                    "usage: ClientProbe <scenario> <start> <page-size> <success|io>");
        }
        String scenario = args[0];
        long start = Long.parseLong(args[1]);
        int pageSize = Integer.parseInt(args[2]);
        boolean expectIOException = args[3].equals("io");
        if (!expectIOException && !args[3].equals("success")) {
            throw new IllegalArgumentException("invalid expectation");
        }

        ScriptedHttpClient transport = new ScriptedHttpClient(scenario);
        VcfLogsClient client = new VcfLogsClient(URI.create("http://127.0.0.1:9"), TOKEN);
        var field = VcfLogsClient.class.getDeclaredField("httpClient");
        field.setAccessible(true);
        field.set(client, transport);

        List<VcfLogsClient.Event> events = List.of();
        IOException failure = null;
        try {
            events = client.fetchAllEvents(start, pageSize);
        } catch (IOException exception) {
            failure = exception;
        }

        if (expectIOException != (failure != null)) {
            throw new AssertionError(expectIOException
                    ? "expected IOException"
                    : "unexpected IOException: " + failure);
        }
        transport.verifyRequests(TOKEN, expectedPaths(scenario), expectedLimit(scenario));

        if (failure != null) {
            System.out.println("IO");
            return;
        }
        for (VcfLogsClient.Event event : events) {
            String encodedText = Base64.getEncoder().encodeToString(
                    event.text().getBytes(StandardCharsets.UTF_8));
            System.out.println(event.timestamp() + "\t" + encodedText);
        }
    }

    private static List<String> expectedPaths(String scenario) {
        String first = "/api/v2/events/timestamp/GE%201700000000100";
        return switch (scenario) {
            case "happy" -> List.of(
                    first,
                    "/api/v2/events/timestamp/GT%201700000000100",
                    "/api/v2/events/timestamp/GT%201700000000300");
            case "subset" -> List.of(
                    "/api/v2/events/timestamp/GE%201700000000250",
                    "/api/v2/events/timestamp/GT%201700000000300",
                    "/api/v2/events/timestamp/GT%201700000000400");
            case "escapes" -> List.of(
                    "/api/v2/events/timestamp/GE%201700000000100");
            case "http-error", "complete-false", "complete-missing" -> List.of(first);
            case "no-progress" -> List.of(
                    first,
                    "/api/v2/events/timestamp/GT%201700000000200");
            default -> throw new AssertionError("unknown scenario: " + scenario);
        };
    }

    private static String expectedLimit(String scenario) {
        return switch (scenario) {
            case "subset" -> "1";
            case "escapes" -> "10";
            default -> "2";
        };
    }

    private record ResponseSpec(int status, String body) {}

    private static final class ScriptedHttpClient extends HttpClient {
        private final String scenario;
        private final List<HttpRequest> requests = new ArrayList<>();
        private final SSLContext sslContext;

        private ScriptedHttpClient(String scenario) throws NoSuchAlgorithmException {
            this.scenario = scenario;
            this.sslContext = SSLContext.getDefault();
        }

        @Override
        public <T> HttpResponse<T> send(
                HttpRequest request, HttpResponse.BodyHandler<T> handler) throws IOException {
            requests.add(request);
            ResponseSpec spec = scriptedResponse(requests.size() - 1);
            HttpHeaders headers = HttpHeaders.of(
                    Map.of("Content-Type", List.of("application/json")),
                    (_name, _value) -> true);
            HttpResponse.ResponseInfo info = new HttpResponse.ResponseInfo() {
                @Override
                public int statusCode() {
                    return spec.status();
                }

                @Override
                public HttpHeaders headers() {
                    return headers;
                }

                @Override
                public Version version() {
                    return Version.HTTP_1_1;
                }
            };
            HttpResponse.BodySubscriber<T> subscriber = handler.apply(info);
            subscriber.onSubscribe(new Flow.Subscription() {
                @Override
                public void request(long count) {}

                @Override
                public void cancel() {}
            });
            subscriber.onNext(List.of(ByteBuffer.wrap(spec.body().getBytes(StandardCharsets.UTF_8))));
            subscriber.onComplete();
            final T body;
            try {
                body = subscriber.getBody().toCompletableFuture().join();
            } catch (CompletionException exception) {
                throw new IOException("response body handler failed", exception.getCause());
            }
            return new ScriptedResponse<>(request, spec.status(), headers, body);
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request, HttpResponse.BodyHandler<T> handler) {
            return completedSend(request, handler);
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request,
                HttpResponse.BodyHandler<T> handler,
                HttpResponse.PushPromiseHandler<T> pushPromiseHandler) {
            return completedSend(request, handler);
        }

        private <T> CompletableFuture<HttpResponse<T>> completedSend(
                HttpRequest request, HttpResponse.BodyHandler<T> handler) {
            try {
                return CompletableFuture.completedFuture(send(request, handler));
            } catch (IOException exception) {
                CompletableFuture<HttpResponse<T>> failed = new CompletableFuture<>();
                failed.completeExceptionally(exception);
                return failed;
            }
        }

        private ResponseSpec scriptedResponse(int index) {
            return switch (scenario) {
                case "happy" -> switch (index) {
                    case 0 -> ok("{\"complete\":true,\"events\":["
                            + "{\"timestamp\":1700000000100,\"text\":\"zeta\"},"
                            + "{\"timestamp\":1700000000100,\"text\":\"alpha\"}]}");
                    case 1 -> ok("{\"complete\":true,\"events\":["
                            + "{\"timestamp\":1700000000200,\"text\":\"bravo\"},"
                            + "{\"timestamp\":1700000000300,\"text\":\"caf\\u00e9 \\u2615\"}]}");
                    case 2 -> ok("{\"complete\":true,\"events\":["
                            + "{\"timestamp\":1700000000400,"
                            + "\"text\":\"delta \\\"quoted\\\" \\\\ path\"}]}");
                    default -> unexpected(index);
                };
                case "subset" -> switch (index) {
                    case 0 -> ok("{\"complete\":true,\"events\":["
                            + "{\"timestamp\":1700000000300,\"text\":\"caf\\u00e9 \\u2615\"}]}");
                    case 1 -> ok("{\"complete\":true,\"events\":["
                            + "{\"timestamp\":1700000000400,"
                            + "\"text\":\"delta \\\"quoted\\\" \\\\ path\"}]}");
                    case 2 -> ok("{\"complete\":true,\"events\":[]}");
                    default -> unexpected(index);
                };
                case "escapes" -> index == 0
                        ? ok("{\"complete\":true,\"events\":[{"
                                + "\"timestamp\":1700000000100,"
                                + "\"text\":\"quote \\\" slash \\/ backslash \\\\ controls "
                                + "\\b\\f\\n\\r\\t snowman \\u2603 emoji \\ud83d\\ude00\"}]}")
                        : unexpected(index);
                case "http-error" -> index == 0
                        ? new ResponseSpec(503, "{\"errorMessage\":\"unavailable\"}")
                        : unexpected(index);
                case "complete-false" -> index == 0
                        ? ok("{\"complete\":false,\"events\":[]}")
                        : unexpected(index);
                case "complete-missing" -> index == 0
                        ? ok("{\"events\":[]}")
                        : unexpected(index);
                case "no-progress" -> index < 2
                        ? ok("{\"complete\":true,\"events\":["
                                + "{\"timestamp\":1700000000200,\"text\":\"zeta\"},"
                                + "{\"timestamp\":1700000000200,\"text\":\"alpha\"}]}")
                        : unexpected(index);
                default -> throw new AssertionError("unknown scenario: " + scenario);
            };
        }

        private static ResponseSpec ok(String body) {
            return new ResponseSpec(200, body);
        }

        private static ResponseSpec unexpected(int index) {
            return new ResponseSpec(500, "{\"errorMessage\":\"unexpected request " + index + "\"}");
        }

        private void verifyRequests(
                String token, List<String> expectedPaths, String expectedLimit) {
            if (requests.size() != expectedPaths.size()) {
                throw new AssertionError(
                        "expected " + expectedPaths.size() + " requests, got " + requests.size());
            }
            for (int index = 0; index < requests.size(); index++) {
                HttpRequest request = requests.get(index);
                String path = request.uri().getRawPath();
                if (!request.method().equals("GET") || !path.equals(expectedPaths.get(index))) {
                    throw new AssertionError("request " + (index + 1) + " path mismatch: " + path);
                }
                Map<String, String> query = new java.util.LinkedHashMap<>();
                String rawQuery = request.uri().getRawQuery();
                if (rawQuery != null) {
                    for (String pair : rawQuery.split("&", -1)) {
                        String[] parts = pair.split("=", 2);
                        if (parts.length != 2 || query.put(parts[0], parts[1]) != null) {
                            throw new AssertionError("invalid or duplicate query parameter: " + pair);
                        }
                    }
                }
                if (!query.equals(Map.of(
                        "limit", expectedLimit,
                        "order-by-direction", "ASC"))) {
                    throw new AssertionError("request " + (index + 1) + " query mismatch: " + query);
                }
                if (!request.headers().firstValue("Authorization").orElse("")
                        .equals("Bearer " + token)) {
                    throw new AssertionError("request " + (index + 1) + " Authorization mismatch");
                }
                if (!request.headers().firstValue("Accept").orElse("").equals("application/json")) {
                    throw new AssertionError("request " + (index + 1) + " Accept mismatch");
                }
                if (request.headers().firstValue("Content-Type").isPresent()) {
                    throw new AssertionError("bodyless GET must omit Content-Type");
                }
                if (request.bodyPublisher().isPresent()
                        && request.bodyPublisher().orElseThrow().contentLength() != 0) {
                    throw new AssertionError("GET request unexpectedly has a body");
                }
                for (String optional : List.of("timeout", "view", "content-pack-fields")) {
                    if (query.containsKey(optional)) {
                        throw new AssertionError("unset optional parameter was sent: " + optional);
                    }
                }
            }
        }

        @Override
        public Optional<CookieHandler> cookieHandler() {
            return Optional.empty();
        }

        @Override
        public Optional<Duration> connectTimeout() {
            return Optional.empty();
        }

        @Override
        public Redirect followRedirects() {
            return Redirect.NEVER;
        }

        @Override
        public Optional<ProxySelector> proxy() {
            return Optional.empty();
        }

        @Override
        public SSLContext sslContext() {
            return sslContext;
        }

        @Override
        public SSLParameters sslParameters() {
            return new SSLParameters();
        }

        @Override
        public Optional<Authenticator> authenticator() {
            return Optional.empty();
        }

        @Override
        public Version version() {
            return Version.HTTP_1_1;
        }

        @Override
        public Optional<Executor> executor() {
            return Optional.empty();
        }
    }

    private record ScriptedResponse<T>(
            HttpRequest request,
            int statusCode,
            HttpHeaders headers,
            T body) implements HttpResponse<T> {
        @Override
        public Optional<HttpResponse<T>> previousResponse() {
            return Optional.empty();
        }

        @Override
        public Optional<SSLSession> sslSession() {
            return Optional.empty();
        }

        @Override
        public URI uri() {
            return request.uri();
        }

        @Override
        public HttpClient.Version version() {
            return HttpClient.Version.HTTP_1_1;
        }
    }
}
'''


def fail(message: str) -> None:
    raise AssertionError(message)


def verify_sources() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    expected_sha = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
    expected_path = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
    if sources.get("tag") != "9.0.0.0" or sources.get("commitSha") != expected_sha:
        fail("official source must remain pinned to the VCF 9.0.0.0 tag commit")
    if sources.get("specPath") != expected_path:
        fail("official source spec path changed")
    expected_url = f"https://github.com/vmware/vcf-api-specs/blob/{expected_sha}/{expected_path}"
    if sources.get("repository") != "https://github.com/vmware/vcf-api-specs":
        fail("official source repository changed")
    if sources.get("license") != "Apache-2.0" or sources.get("specUrl") != expected_url:
        fail("official specification URL or license changed")
    operation_ids = [
        operation.get("operationId")
        for path_item in contract.get("paths", {}).values()
        for method, operation in path_item.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    ]
    if operation_ids != ["GET_events-+path"]:
        fail("contract operationId changed")
    if sources.get("operationIds") != operation_ids:
        fail("official source operationIds do not match the contract")
    if contract.get("servers") != [{"url": "/api/v2"}]:
        fail("contract server path changed")
    operation = contract["paths"]["/events/{+path}"]["get"]
    parameter_names = [parameter["name"] for parameter in operation["parameters"]]
    if parameter_names != [
        "+path",
        "limit",
        "timeout",
        "view",
        "content-pack-fields",
        "order-by-direction",
    ]:
        fail("contract parameters changed")
    if operation.get("security") != [{"Bearer": []}]:
        fail("contract Bearer security changed")
    schemas = contract.get("components", {}).get("schemas", {})
    if schemas.get("events.get.response", {}).get("required") != ["complete"]:
        fail("contract response requirements changed")


def encoded_output(events: list[tuple[int, str]]) -> str:
    return "".join(
        f"{timestamp}\t{base64.b64encode(text.encode('utf-8')).decode('ascii')}\n"
        for timestamp, text in events
    )


def run_scenario(
    classes: Path,
    scenario: str,
    start_timestamp: int,
    page_size: int,
    expectation: str,
    expected_stdout: str,
) -> None:
    result = subprocess.run(
        [
            "java",
            "-XX:+PerfDisableSharedMem",
            "-Dfile.encoding=UTF-8",
            "-cp",
            str(classes),
            "ClientProbe",
            scenario,
            str(start_timestamp),
            str(page_size),
            expectation,
        ],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        fail(f"scenario {scenario} failed\nstdout: {result.stdout}\nstderr: {result.stderr}")
    if result.stdout != expected_stdout:
        fail(
            f"scenario {scenario} output mismatch\n"
            f"expected: {expected_stdout!r}\nactual: {result.stdout!r}"
        )


def main() -> None:
    verify_sources()
    java_sources = sorted(path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*.java"))
    if java_sources != ["TestMain.java", "VcfLogsClient.java"]:
        fail(f"client must remain a single Java source file; found {java_sources}")

    with tempfile.TemporaryDirectory(prefix="vcf90-0098-") as temp_name:
        temp = Path(temp_name)
        classes = temp / "classes"
        classes.mkdir()
        probe_source = temp / "ClientProbe.java"
        probe_source.write_text(PROBE_SOURCE, encoding="utf-8")
        compile_result = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-d",
                str(classes),
                "VcfLogsClient.java",
                "TestMain.java",
                str(probe_source),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        if compile_result.returncode != 0:
            fail(f"javac failed\n{compile_result.stdout}{compile_result.stderr}")

        run_scenario(
            classes,
            "happy",
            START_TIMESTAMP,
            2,
            "success",
            encoded_output(
                [
                    (1_700_000_000_100, "alpha"),
                    (1_700_000_000_100, "zeta"),
                    (1_700_000_000_200, "bravo"),
                    (1_700_000_000_300, "café ☕"),
                    (1_700_000_000_400, 'delta "quoted" \\ path'),
                ]
            ),
        )
        run_scenario(
            classes,
            "subset",
            1_700_000_000_250,
            1,
            "success",
            encoded_output(
                [
                    (1_700_000_000_300, "café ☕"),
                    (1_700_000_000_400, 'delta "quoted" \\ path'),
                ]
            ),
        )
        escaped_text = 'quote " slash / backslash \\ controls \b\f\n\r\t snowman ☃ emoji 😀'
        run_scenario(
            classes,
            "escapes",
            START_TIMESTAMP,
            10,
            "success",
            encoded_output([(START_TIMESTAMP, escaped_text)]),
        )
        for scenario in ("http-error", "complete-false", "complete-missing", "no-progress"):
            run_scenario(classes, scenario, START_TIMESTAMP, 2, "io", "IO\n")

    print("PASS: VCF Operations for Logs 9.0 pagination and wire contract verified")


if __name__ == "__main__":
    main()
