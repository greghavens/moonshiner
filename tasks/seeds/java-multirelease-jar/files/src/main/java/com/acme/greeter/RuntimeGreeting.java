package com.acme.greeter;

public final class RuntimeGreeting implements GreetingService {
    @Override
    public String message() {
        return "base-java8";
    }
}
