package com.vmware.vcf.lab.harness;

import com.vmware.vcf.lab.VcenterSessionClient;
import com.vmware.vcf.lab.VcenterSessionClient.CpuUpdateSpec;
import com.vmware.vcf.lab.VcenterSessionClient.VcenterApiException;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Protected harness. Drives the credential rotation scenario against the loopback mock and
 * writes the observable outcomes to a result file. All wire level assertions live in the
 * verifier, which reads the mock's request log.
 */
public final class TestMain {

    private static final String PRINCIPAL = "administrator@vsphere.local";
    private static final String OLD_PASSWORD = "OldSecret!23";
    private static final String NEW_PASSWORD = "NewSecret!45";
    private static final String UNVERIFIABLE_PASSWORD = "Quarantined!99";
    private static final String REJECTED_VM = "vm-rejected";
    private static final String SLOW_VM = "vm-slow";
    private static final String VALIDATION_WINDOW_VM = "vm-validation-window";

    public static void main(String[] arguments) throws Exception {
        Map<String, String> options = parseArguments(arguments);
        URI baseUri = URI.create(options.get("base-uri"));
        Path logPath = Path.of(options.get("log"));
        Path outputPath = Path.of(options.get("out"));

        Map<String, Object> results = new LinkedHashMap<>();
        VcenterSessionClient client = new VcenterSessionClient(baseUri);

        String initialToken = client.connect(PRINCIPAL, OLD_PASSWORD);
        results.put("connected", initialToken != null && !initialToken.isEmpty());
        results.put("connectReturnsCurrentToken", initialToken != null
                && initialToken.equals(client.currentSessionToken()));

        client.updateVirtualMachineCpu("vm-101", new CpuUpdateSpec().count(8));

        // A failed reconfigure must surface the contract operation and response status, and the
        // final optional UpdateSpec property must still be serialized when it was explicitly set.
        int cpuFailureStatus = -1;
        String cpuFailureOperationId = "<none>";
        try {
            client.updateVirtualMachineCpu(
                    REJECTED_VM, new CpuUpdateSpec().hotRemoveEnabled(true));
        } catch (VcenterApiException failure) {
            cpuFailureStatus = failure.statusCode();
            cpuFailureOperationId = failure.operationId();
        }
        results.put("cpuFailureStatus", cpuFailureStatus);
        results.put("cpuFailureOperationId", cpuFailureOperationId);

        // A rotation whose replacement session cannot be validated must leave the client
        // running on the credential it already had.
        int failureStatus = -1;
        String failureOperationId = "<none>";
        try {
            client.rotateCredential(PRINCIPAL, UNVERIFIABLE_PASSWORD);
        } catch (VcenterApiException failure) {
            failureStatus = failure.statusCode();
            failureOperationId = failure.operationId();
        }
        results.put("failedRotationStatus", failureStatus);
        results.put("failedRotationOperationId", failureOperationId);
        results.put("tokenKeptAfterFailedRotation", initialToken.equals(client.currentSessionToken()));

        client.updateVirtualMachineCpu("vm-103", new CpuUpdateSpec().coresPerSocket(1));

        // A reconfigure that the mock holds open, so it is genuinely in flight on the old
        // session while the rotation runs.
        AtomicReference<Throwable> inFlightFailure = new AtomicReference<>();
        Thread inFlight = new Thread(() -> {
            try {
                client.updateVirtualMachineCpu(SLOW_VM, new CpuUpdateSpec().count(16).coresPerSocket(2));
            } catch (Throwable throwable) {
                inFlightFailure.set(throwable);
            }
        }, "in-flight-reconfigure");
        inFlight.start();
        awaitRequestLogged(logPath, "\"path\": \"/api/vcenter/vm/" + SLOW_VM + "/hardware/cpu\"");

        // Run rotation concurrently so work submitted while Cis.Session_get is still validating
        // the replacement can prove that the replacement is not adopted prematurely.
        AtomicReference<Throwable> rotationFailure = new AtomicReference<>();
        Thread rotation = new Thread(() -> {
            try {
                client.rotateCredential(PRINCIPAL, NEW_PASSWORD);
            } catch (Throwable throwable) {
                rotationFailure.set(throwable);
            }
        }, "credential-rotation");
        rotation.start();
        awaitLogOccurrences(logPath, "\"operationId\": \"Cis.Session_get\"", 2);

        client.updateVirtualMachineCpu(
                VALIDATION_WINDOW_VM, new CpuUpdateSpec().hotRemoveEnabled(false));

        rotation.join();
        inFlight.join();

        Throwable strandedFailure = inFlightFailure.get();
        Throwable rotateFailure = rotationFailure.get();
        results.put("inFlightRequestSucceeded", strandedFailure == null);
        results.put("inFlightFailure", strandedFailure == null ? "<none>" : describe(strandedFailure));
        results.put("rotationSucceeded", rotateFailure == null);
        results.put("rotationFailure", rotateFailure == null ? "<none>" : describe(rotateFailure));
        results.put("tokenReplacedAfterRotation", !initialToken.equals(client.currentSessionToken()));

        client.updateVirtualMachineCpu("vm-102", new CpuUpdateSpec().hotAddEnabled(false));
        client.close();
        results.put("tokenClearedByClose", client.currentSessionToken() == null);
        client.close();

        Files.writeString(outputPath, toJson(results), StandardCharsets.UTF_8);
    }

    private static String describe(Throwable throwable) {
        if (throwable instanceof VcenterApiException apiFailure) {
            return apiFailure.operationId() + "/" + apiFailure.statusCode();
        }
        return throwable.getClass().getName();
    }

    private static void awaitRequestLogged(Path logPath, String needle) throws Exception {
        awaitLogOccurrences(logPath, needle, 1);
    }

    private static void awaitLogOccurrences(Path logPath, String needle, int expectedCount)
            throws Exception {
        long deadline = System.nanoTime() + 10_000_000_000L;
        while (System.nanoTime() < deadline) {
            if (Files.exists(logPath)) {
                String contents = Files.readString(logPath, StandardCharsets.UTF_8);
                int count = 0;
                int from = 0;
                while ((from = contents.indexOf(needle, from)) >= 0) {
                    count++;
                    from += needle.length();
                }
                if (count >= expectedCount) {
                    return;
                }
            }
            Thread.sleep(5);
        }
        throw new IllegalStateException(
                "request log never showed " + expectedCount + " occurrence(s) of " + needle);
    }

    private static Map<String, String> parseArguments(String[] arguments) {
        Map<String, String> options = new LinkedHashMap<>();
        for (int index = 0; index + 1 < arguments.length; index += 2) {
            options.put(arguments[index].replaceFirst("^--", ""), arguments[index + 1]);
        }
        for (String required : new String[] {"base-uri", "log", "out"}) {
            if (!options.containsKey(required)) {
                throw new IllegalArgumentException("missing --" + required);
            }
        }
        return options;
    }

    private static String toJson(Map<String, Object> values) {
        StringBuilder json = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, Object> entry : values.entrySet()) {
            if (!first) {
                json.append(',');
            }
            first = false;
            json.append('"').append(entry.getKey()).append("\":");
            Object value = entry.getValue();
            if (value instanceof String text) {
                json.append('"').append(text.replace("\\", "\\\\").replace("\"", "\\\"")).append('"');
            } else {
                json.append(value);
            }
        }
        return json.append('}').toString();
    }

    private TestMain() {
    }
}
