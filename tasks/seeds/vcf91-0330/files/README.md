# vcfdiag

A small Go module that diagnoses a failed deployment on the VCF Automation API
in VMware Cloud Foundation 9.1.

    client.go          package vcfdiag — the diagnostic client
    mockapi/           package mockapi — a loopback stand-in pinned to the contract
    docs/              the derived contract and the sources it was derived from
    verify/            the protected acceptance check; do not edit

Run the acceptance check with:

    bash verify/run.sh

Everything is standard library only, and the test suite never contacts a live
VMware endpoint.
