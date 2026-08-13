# SDDC Manager 9.0 bundle-download client

A small Java program that drives a VMware Cloud Foundation 9.0 SDDC Manager appliance through one
bundle download, start to finish.

## Layout

```
src/com/example/vcf/SddcManagerClient.java   the client — the only file you implement
harness/com/example/vcf/harness/
  MockSddcManager.java                       loopback mock of the appliance
  TestMain.java                              boots the mock, runs the client, records the run
  Verifier.java                              checks the recorded run and docs/
  MiniJson.java                              dependency-free JSON reader/writer
docs/README.md                               shape of the two files you write into docs/
verify.sh                                    compile, run, verify
```

`harness/` and `verify.sh` are protected: they are restored before verification, so changing them
changes nothing.

## Running

```
./verify.sh
```

It compiles everything with `javac`, runs `TestMain` (which starts the mock on a free loopback port
and hands its base URL to your client), and then runs `Verifier`. Failures are printed one per line.
Build and run need no network and no third-party libraries — JDK 17 or newer is enough.

After a run, `target/request-log.jsonl` holds one JSON object per HTTP request the mock saw —
method, path, raw query string, headers, body and the status it answered with. It is the fastest way
to see what your client actually put on the wire.
