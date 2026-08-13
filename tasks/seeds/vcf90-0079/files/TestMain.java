public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: TestMain <loopback-base-uri> <case-token>");
        }

        String authorization = "OpsToken integration-" + args[1];
        VcfOperationsClient client = new VcfOperationsClient(args[0], authorization);

        String firstResponse = client.updateHardThresholdSymptom(
                "SymptomDefinition-retry-" + args[1],
                "CPU \"saturation\" \\ retry\nthreshold \u2603",
                "VMWARE",
                "VirtualMachine",
                "CRITICAL",
                "cpu|demandmhz",
                "GT_EQ",
                "95");

        if (!firstResponse.contains("\"id\":\"SymptomDefinition-retry-" + args[1] + "\"")
                || !firstResponse.contains("\"waitCycles\":2")) {
            throw new AssertionError("the first successful response body was not returned: " + firstResponse);
        }

        String secondResponse = client.updateHardThresholdSymptom(
                "SymptomDefinition-second-" + args[1],
                "Memory\bpressure\fwith\rcontrols\tand \u0001 emoji \ud83d\ude80",
                "CUSTOM-" + args[1],
                "HostSystem",
                "WARNING",
                "mem|host_usagePct/" + args[1],
                "LT",
                "12.5");

        if (!secondResponse.contains("\"id\":\"SymptomDefinition-second-" + args[1] + "\"")
                || !secondResponse.contains("\"waitCycles\":3")) {
            throw new AssertionError("the second successful response body was not returned: " + secondResponse);
        }
        System.out.println("TestMain passed");
    }
}
