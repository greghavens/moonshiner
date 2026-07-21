package com.acme.app;

import com.acme.greeter.GreetingService;
import java.util.Iterator;
import java.util.ServiceLoader;

public final class Main {
    private Main() {
    }

    public static void main(String[] args) {
        Iterator<GreetingService> providers =
                ServiceLoader.load(GreetingService.class).iterator();
        if (!providers.hasNext()) {
            throw new IllegalStateException("no GreetingService provider found");
        }
        GreetingService provider = providers.next();
        if (providers.hasNext()) {
            throw new IllegalStateException("multiple GreetingService providers found");
        }
        System.out.println(provider.message());
    }
}
