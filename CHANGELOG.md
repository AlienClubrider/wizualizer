# Changelog

## 0.4.0 (2026-11-20)

Spring Boot "service slice" tests (the real Spring context, external systems
mocked, an in-memory database) now record quickly and show what the framework
did.

- **No more hangs.** The Java recorder reads values without running any code
  in the test JVM, ends the recording when an exception leaves the test, and
  stops a test still running after 5 minutes (`wiz run --timeout SECONDS`,
  `$WIZ_RECORD_TIMEOUT`, `0` for no limit). The helper, the test JVM and the
  build are stopped together, even if `wiz` is killed.
- **Fast Spring tests.** Breakpoints are set only while the test method runs,
  so starting the Spring context no longer runs under the debugger. A
  `@SpringBootTest` with MockMvc records in about 10 s instead of 2–4 minutes.
- **Framework steps.** Transactions (began, joined, committed, rolled back),
  MVC dispatch and its status, `@Valid` rejecting a request, aspects, `@Async`
  and method security each show as one step in the sequence.
- **The right function under test.** Recordings open on the function the test
  is about (`OwnerControllerTests#processFindFormSuccess` opens
  `OwnerController.processFindForm`), not on setters, constructors,
  `@InitBinder`/`@ModelAttribute` hooks, servlet filters or mocks. Only real
  test source sets (`src/test/**`, `src/integrationTest/**`…) count as tests: a
  `test` package under `src/main/java` is app code.
- **Mocks drawn as mocks.** `@MockitoBean` and inline mocks of concrete
  classes show as mocked calls with their arguments and stubbed value (or the
  error they threw), not as a real method that never ran.

Known gaps:

- Calls into framework code (MockMvc, Spring proxies, repositories) show no
  return value, and library calls made by the test itself show none either.
- `@Cacheable` and Spring events are not shown as steps.
- When an exception escapes MockMvc, the MVC step says "answered 200" (the
  status was never set).
- Code run on another thread (`RANDOM_PORT` servers, `@Async` bodies) is
  still not recorded.
