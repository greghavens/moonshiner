import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;

/** Protected offline model of the current JUnit Platform launcher contract. */
public final class PlatformContract {
    private PlatformContract() {}

    @Retention(RetentionPolicy.RUNTIME)
    @Target(ElementType.METHOD)
    public @interface Test {}

    @Retention(RetentionPolicy.RUNTIME)
    @Target(ElementType.METHOD)
    public @interface ParameterizedTest {}

    @Retention(RetentionPolicy.RUNTIME)
    @Target(ElementType.METHOD)
    public @interface ValueSource { int[] ints(); }

    @Retention(RetentionPolicy.RUNTIME)
    @Target(ElementType.METHOD)
    public @interface BeforeEach {}

    @Retention(RetentionPolicy.RUNTIME)
    @Target(ElementType.METHOD)
    public @interface AfterEach {}

    @Retention(RetentionPolicy.RUNTIME)
    @Target(ElementType.METHOD)
    public @interface LegacyTest {
        Class<? extends Throwable> expected() default NoExpectedException.class;
    }

    @Retention(RetentionPolicy.RUNTIME)
    @Target(ElementType.METHOD)
    public @interface LegacyBefore {}

    @Retention(RetentionPolicy.RUNTIME)
    @Target(ElementType.METHOD)
    public @interface LegacyAfter {}

    public static final class NoExpectedException extends Throwable {
        private NoExpectedException() {}
    }

    public enum Outcome { PASSED, FAILED }

    public record CaseResult(String id, Outcome outcome, String detail) {}

    public interface VintageEngine {
        List<CaseResult> execute(Class<?> testClass);
    }

    public static final class Launcher {
        private final List<Class<?>> selectedClasses;
        private final VintageEngine vintage;

        public Launcher(List<Class<?>> selectedClasses, VintageEngine vintage) {
            this.selectedClasses = List.copyOf(selectedClasses);
            this.vintage = vintage;
        }

        public List<CaseResult> execute() {
            List<CaseResult> results = new ArrayList<>();
            for (Class<?> selected : selectedClasses) {
                if (containsCurrentTests(selected)) {
                    results.addAll(executeJupiter(selected));
                }
                if (containsLegacyTests(selected)) {
                    if (vintage == null) {
                        continue;
                    }
                    results.addAll(vintage.execute(selected));
                }
            }
            return List.copyOf(results);
        }
    }

    private static boolean containsCurrentTests(Class<?> type) {
        return Arrays.stream(type.getDeclaredMethods()).anyMatch(method ->
                method.isAnnotationPresent(Test.class)
                        || method.isAnnotationPresent(ParameterizedTest.class));
    }

    private static boolean containsLegacyTests(Class<?> type) {
        return Arrays.stream(type.getDeclaredMethods())
                .anyMatch(method -> method.isAnnotationPresent(LegacyTest.class));
    }

    private static List<CaseResult> executeJupiter(Class<?> type) {
        List<CaseResult> results = new ArrayList<>();
        Method[] before = annotated(type, BeforeEach.class);
        Method[] after = annotated(type, AfterEach.class);
        for (Method method : sortedMethods(type)) {
            if (method.isAnnotationPresent(Test.class)) {
                results.add(invokeCurrent(type, method, before, after,
                        type.getSimpleName() + "." + method.getName(), new Object[0]));
            } else if (method.isAnnotationPresent(ParameterizedTest.class)) {
                ValueSource source = method.getAnnotation(ValueSource.class);
                if (source == null) {
                    results.add(new CaseResult(type.getSimpleName() + "." + method.getName(),
                            Outcome.FAILED, "missing @ValueSource"));
                    continue;
                }
                for (int value : source.ints()) {
                    results.add(invokeCurrent(type, method, before, after,
                            type.getSimpleName() + "." + method.getName() + "[" + value + "]",
                            new Object[] {value}));
                }
            }
        }
        return results;
    }

    private static CaseResult invokeCurrent(Class<?> type, Method test, Method[] before,
                                            Method[] after, String id, Object[] arguments) {
        Throwable failure = null;
        try {
            Object instance = newInstance(type);
            try {
                invokeAll(before, instance);
                invoke(test, instance, arguments);
            } catch (Throwable error) {
                failure = error;
            } finally {
                try {
                    invokeAll(after, instance);
                } catch (Throwable cleanup) {
                    if (failure == null) failure = cleanup;
                }
            }
        } catch (Throwable construction) {
            failure = construction;
        }
        return failure == null
                ? new CaseResult(id, Outcome.PASSED, "")
                : new CaseResult(id, Outcome.FAILED, failure.getClass().getSimpleName());
    }

    public static Method[] annotated(
            Class<?> type, Class<? extends java.lang.annotation.Annotation> annotation) {
        return Arrays.stream(type.getDeclaredMethods())
                .filter(method -> method.isAnnotationPresent(annotation))
                .sorted(Comparator.comparing(Method::getName))
                .toArray(Method[]::new);
    }

    public static Method[] sortedMethods(Class<?> type) {
        return Arrays.stream(type.getDeclaredMethods())
                .sorted(Comparator.comparing(Method::getName))
                .toArray(Method[]::new);
    }

    public static Object newInstance(Class<?> type) throws ReflectiveOperationException {
        var constructor = type.getDeclaredConstructor();
        constructor.setAccessible(true);
        return constructor.newInstance();
    }

    public static void invokeAll(Method[] methods, Object instance) throws Throwable {
        for (Method method : methods) invoke(method, instance, new Object[0]);
    }

    public static void invoke(Method method, Object instance, Object[] arguments) throws Throwable {
        try {
            method.setAccessible(true);
            method.invoke(instance, arguments);
        } catch (InvocationTargetException error) {
            throw error.getCause();
        }
    }
}

