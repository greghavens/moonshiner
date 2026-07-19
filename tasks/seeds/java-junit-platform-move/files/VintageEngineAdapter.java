import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

/** Adapter from legacy JUnit-4-shaped annotations to the current engine seam. */
public final class VintageEngineAdapter implements PlatformContract.VintageEngine {
    @Override
    public List<PlatformContract.CaseResult> execute(Class<?> testClass) {
        List<PlatformContract.CaseResult> results = new ArrayList<>();
        try {
            Object instance = PlatformContract.newInstance(testClass);
            for (Method method : PlatformContract.sortedMethods(testClass)) {
                if (!method.isAnnotationPresent(PlatformContract.LegacyTest.class)) continue;
                String id = testClass.getSimpleName() + "." + method.getName();
                try {
                    PlatformContract.invoke(method, instance, new Object[0]);
                    results.add(new PlatformContract.CaseResult(
                            id, PlatformContract.Outcome.PASSED, ""));
                } catch (Throwable error) {
                    results.add(new PlatformContract.CaseResult(
                            id, PlatformContract.Outcome.FAILED,
                            error.getClass().getSimpleName()));
                }
            }
        } catch (ReflectiveOperationException error) {
            throw new IllegalStateException("legacy test construction failed", error);
        }
        return List.copyOf(results);
    }
}

