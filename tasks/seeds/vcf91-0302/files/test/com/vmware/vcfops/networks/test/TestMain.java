package com.vmware.vcfops.networks.test;

import com.vmware.vcfops.networks.NetworkInsightInventoryClient;
import com.vmware.vcfops.networks.NetworkInsightInventoryClient.ApplicationSummary;

import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Drives {@link NetworkInsightInventoryClient} against a loopback {@link MockNiServer} and checks
 * the resulting request log with {@link ContractVerifier}.
 *
 * <p>Exit code 0 means the contract was honoured; 1 means at least one assertion failed.
 * No network endpoint outside 127.0.0.1 is contacted.
 *
 * <p>DO NOT MODIFY.
 */
public final class TestMain {

    private static final int CLIENT_TIMEOUT_SECONDS = 60;
    private static final Path LOG_PATH = Path.of("build", "request-log.jsonl");

    public static void main(String[] args) throws Exception {
        int exitCode;
        try (MockNiServer server = new MockNiServer().start()) {
            System.out.println("Mock VCF Operations for Networks appliance on " + server.baseUrl());

            List<ApplicationSummary> snapshot = null;
            String clientError = null;
            try {
                snapshot = runClient(server.baseUrl());
            } catch (TimeoutException e) {
                clientError = "collectApplicationInventory() did not finish within "
                        + CLIENT_TIMEOUT_SECONDS + "s. A client that retries a 401 forever will "
                        + "hang here.";
            } catch (Exception e) {
                clientError = "collectApplicationInventory() threw " + rootCause(e);
            }

            server.writeLog(LOG_PATH);
            System.out.println("Recorded " + server.requests().size() + " request(s) to "
                    + LOG_PATH.toAbsolutePath());

            List<String> failures = new ContractVerifier(
                    server.requests(), server.issuedTokens(), server.applications(), snapshot)
                    .verify();

            exitCode = report(clientError, failures, server);
        }
        System.exit(exitCode);
    }

    private static List<ApplicationSummary> runClient(String baseUrl) throws Exception {
        ExecutorService pool = Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "inventory-client");
            t.setDaemon(true);
            return t;
        });
        try {
            Callable<List<ApplicationSummary>> job = () -> new NetworkInsightInventoryClient(
                    baseUrl, MockNiServer.USERNAME, MockNiServer.PASSWORD)
                    .collectApplicationInventory();
            Future<List<ApplicationSummary>> future = pool.submit(job);
            return future.get(CLIENT_TIMEOUT_SECONDS, TimeUnit.SECONDS);
        } finally {
            pool.shutdownNow();
        }
    }

    private static int report(String clientError, List<String> failures, MockNiServer server) {
        System.out.println();
        System.out.println("---- request log ----");
        for (RecordedRequest r : server.requests()) {
            System.out.printf("  %-20s %s%n", r.operationId(), r.describe());
        }
        System.out.println();

        if (clientError == null && failures.isEmpty()) {
            System.out.println("PASS: all contract assertions satisfied ("
                    + server.requests().size() + " requests, "
                    + server.issuedTokens().size() + " tokens issued).");
            return 0;
        }

        if (clientError != null) {
            System.out.println("FAIL: " + clientError);
        }
        for (String failure : failures) {
            System.out.println("FAIL: " + failure);
        }
        System.out.println();
        System.out.println(failures.size() + " contract assertion(s) failed"
                + (clientError == null ? "." : ", plus a client error."));
        return 1;
    }

    private static String rootCause(Throwable t) {
        Throwable cause = t;
        while (cause.getCause() != null && cause.getCause() != cause) {
            cause = cause.getCause();
        }
        return cause + (cause.getStackTrace().length > 0 ? " at " + cause.getStackTrace()[0] : "");
    }
}
