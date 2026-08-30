package com.broadcom.vcf.sddclcm.harness;

import com.broadcom.vcf.sddclcm.SddcLcmClient;

import java.util.concurrent.atomic.AtomicInteger;

/**
 * Protected session authority for the loopback fixture.
 *
 * <p>The pinned SDDC LCM specification declares a bearer security scheme and no
 * token route, so the authority stands in for the identity provider that issued
 * the session. It hands the client an access token that stops being accepted
 * part way through the lifecycle run and mints exactly one replacement on
 * request.
 *
 * <p>This file is part of the protected harness. Do not modify it.
 */
public final class TokenAuthority {

    /** The token the session starts with. */
    public static final String INITIAL_ACCESS_TOKEN =
            "eyJhbGciOiJSUzI1NiJ9.sddc-lcm-session-alpha.c2lnbmF0dXJlLWFscGhh";

    /** The token minted by the single permitted renewal. */
    public static final String REPLACEMENT_ACCESS_TOKEN =
            "eyJhbGciOiJSUzI1NiJ9.sddc-lcm-session-bravo.c2lnbmF0dXJlLWJyYXZv";

    private final AtomicInteger refreshCount = new AtomicInteger();
    private volatile boolean initialAccepted = true;
    private volatile boolean replacementAccepted = true;

    /** Reports whether the service still accepts {@code token}. */
    public boolean accepts(String token) {
        if (INITIAL_ACCESS_TOKEN.equals(token)) {
            return initialAccepted;
        }
        if (REPLACEMENT_ACCESS_TOKEN.equals(token)) {
            return replacementAccepted;
        }
        return false;
    }

    /** Stops accepting the initial access token. */
    public void expireInitialAccessToken() {
        initialAccepted = false;
    }

    /** Stops accepting the replacement access token. */
    public void expireReplacementAccessToken() {
        replacementAccepted = false;
    }

    /** Reports whether the initial access token has already expired. */
    public boolean initialAccessTokenExpired() {
        return !initialAccepted;
    }

    /** Number of renewals the client has asked for. */
    public int refreshCount() {
        return refreshCount.get();
    }

    /**
     * Builds the token source handed to the client. The source keeps returning
     * whatever token it currently holds, including an expired one, until the
     * client explicitly renews.
     */
    public SddcLcmClient.AccessTokenSource newAccessTokenSource() {
        return new SddcLcmClient.AccessTokenSource() {
            private String held = INITIAL_ACCESS_TOKEN;

            @Override
            public synchronized String currentAccessToken() {
                return held;
            }

            @Override
            public synchronized String refreshAccessToken() {
                refreshCount.incrementAndGet();
                held = REPLACEMENT_ACCESS_TOKEN;
                return held;
            }
        };
    }
}
