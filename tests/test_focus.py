from wizualizer.focus import function_under_test, is_test_file, in_test_code, leading_annotations, subject_of


def test_test_files_python_and_java():
    for rel in ("tests/test_orders.py", "pkg/orders_test.py", "conftest.py", "tests/helpers.py",
                "server/src/test/java/a/FakeClock.java", "src/main/java/a/OrderServiceTest.java",
                "src/main/java/a/OrderServiceIT.java"):
        assert is_test_file(rel), rel
    for rel in ("shop/orders.py", "server/src/main/java/a/TransferService.java", "shop/testing_mode.py",
                "src/main/java/a/Contest.java"):
        assert not is_test_file(rel), rel


FUNCTIONS = {
    "tests/test_x.py:1": {"file": "tests/test_x.py"},
    "tests/test_x.py:9": {"file": "tests/test_x.py"},  # a helper in the test file
    "shop/orders.py:5": {"file": "shop/orders.py"},
    "shop/pricing.py:20": {"file": "shop/pricing.py"},
    "shop/pricing.py:40": {"file": "shop/pricing.py"},
    "run_me.py:1": {"file": "run_me.py"},
}
# test → helper → place_order → (pricing 20, pricing 40); then the test calls pricing 40 again
FRAMES = {
    "1": {"fn": "tests/test_x.py:1", "parent": None},
    "2": {"fn": "tests/test_x.py:9", "parent": 1},
    "3": {"fn": "shop/orders.py:5", "parent": 2},
    "4": {"fn": "shop/pricing.py:20", "parent": 3},
    "5": {"fn": "shop/pricing.py:40", "parent": 3},
}
STEPS = [{"kind": "call", "frame": f} for f in (1, 2, 3, 4, 5)]
LINES = {"shop/orders.py:5": range(5, 15), "shop/pricing.py:20": range(20, 30), "shop/pricing.py:40": range(40, 50)}
TEST_FNS = {"tests/test_x.py:1", "tests/test_x.py:9"}


def test_test_functions_are_test_files_and_the_entry_frame():
    frames = {**FRAMES, "1": {"fn": "run_me.py:1", "parent": None}}  # entry outside a test file
    assert in_test_code(FUNCTIONS, FRAMES) == TEST_FNS
    assert in_test_code(FUNCTIONS, frames) == TEST_FNS | {"run_me.py:1"}


def test_focus_is_first_production_call_without_changes():
    assert function_under_test(STEPS, FRAMES, TEST_FNS, LINES, {}) == "shop/orders.py:5"


def test_focus_moves_to_the_first_called_function_with_changed_lines():
    changed = {"shop/pricing.py": {44, 22}}
    assert function_under_test(STEPS, FRAMES, TEST_FNS, LINES, changed) == "shop/pricing.py:20"
    assert function_under_test(STEPS, FRAMES, TEST_FNS, LINES, {"shop/pricing.py": {44}}) == "shop/pricing.py:40"


def test_changed_lines_in_the_test_or_uncalled_code_do_not_steer_focus():
    changed = {"tests/test_x.py": {2}, "shop/pricing.py": {99}}
    assert function_under_test(STEPS, FRAMES, TEST_FNS, LINES, changed) == "shop/orders.py:5"


def test_no_focus_when_the_test_calls_no_production_code():
    assert function_under_test(STEPS[:2], FRAMES, TEST_FNS, LINES, {}) is None


def test_only_test_source_sets_count_as_tests_not_any_test_package():
    for rel in ("core/src/integrationTest/java/a/Fixtures.java", "app/src/testFixtures/java/a/Builders.java",
                "app/src/it/java/a/Helpers.java", "app/src/test/kotlin/a/Fakes.kt"):
        assert is_test_file(rel), rel
    for rel in ("smoke-test/spring-boot-smoke-test-test/src/main/java/smoketest/test/web/UserVehicleService.java",
                "core/spring-boot-test/src/main/java/org/springframework/boot/test/json/JsonContent.java",
                "src/main/java/com/acme/test/Clock.java", "src/main/resources/test/data.sql"):
        assert not is_test_file(rel), rel


def test_annotations_on_a_declaration():
    src = "class A {\n  @Bean\n  @Primary // why\n  Foo foo() {}\n  @InitBinder public void b() {}\n}\n"
    assert leading_annotations(src, 2) == {"Bean", "Primary"}
    assert leading_annotations(src, 5) == {"InitBinder"}
    handler = '  @PostMapping("/visits/new")\n  public String add(@ModelAttribute Owner owner, @Valid Visit v) {}\n'
    assert leading_annotations(handler, 1) == {"PostMapping"}
    assert leading_annotations("@pytest.fixture(scope='module')\ndef db(): ...\n", 1) == {"pytest.fixture"}


def test_subject_of_a_test():
    assert subject_of("OwnerControllerTests.processFindFormSuccess") == ("OwnerController", ["process", "find", "form", "success"])
    assert subject_of("TestOrders.test_place_order") == ("Orders", ["place", "order"])
    assert subject_of("test_place_order")[0] == ""


def _spring_like(calls, lines, test="OwnerControllerTests.processFindFormSuccess"):
    """A test frame (1) calling each (qualname, line steps) in order, as frames 2..n."""
    functions = {"T.java:1": {"qualname": test, "file": "src/test/java/T.java"}}
    frames = {"1": {"fn": "T.java:1", "parent": None}}
    steps = [{"kind": "call", "frame": 1}]
    for i, (qualname, n) in enumerate(calls, start=2):
        fid = f"A.java:{i}"
        functions[fid] = {"qualname": qualname, "file": "src/main/java/A.java"}
        frames[str(i)] = {"fn": fid, "parent": 1}
        steps += [{"kind": "call", "frame": i}] + [{"kind": "line", "frame": i}] * n
    return steps, frames, functions


def _pick(calls, test="OwnerControllerTests.processFindFormSuccess", annotations=None, lines=None):
    steps, frames, functions = _spring_like(calls, lines, test)
    fid = function_under_test(steps, frames, {"T.java:1"}, {}, {}, functions, annotations or {})
    return functions[fid]["qualname"] if fid else None


def test_focus_skips_arrange_setters_and_constructors_for_the_named_method():
    calls = [("new Owner", 2), ("BaseEntity.setId", 1), ("Person.setFirstName", 1),
             ("OwnerController.setAllowedFields", 1), ("OwnerController.processFindForm", 9)]
    assert _pick(calls) == "OwnerController.processFindForm"


def test_focus_skips_a_mock_that_ran_no_line():
    calls = [("UserService.signUp", 0), ("new Email", 2), ("User.of", 1), ("UserRestController.postUser", 2)]
    assert _pick(calls, "UserRestControllerTest.when_post_user_expect_valid_userModel") == "UserRestController.postUser"


def test_focus_prefers_the_named_class_then_any_real_code_over_hooks():
    calls = [("PetTypeFormatter.parse", 3), ("PetController.populatePetTypes", 1), ("PetController.processUpdate", 6)]
    ann = {"A.java:3": {"ModelAttribute"}}
    assert _pick(calls, "PetControllerTests.updatesThePetOnPost", ann) == "PetController.processUpdate"
    assert _pick([("Clock.tick", 2), ("new Ledger", 4)], "LedgerTests.balances") == "Clock.tick"


def test_focus_falls_back_to_a_constructor_when_only_constructors_ran():
    assert _pick([("new HmacSHA256", 3)], "HmacSHA256Test.when_invalid_secret_expect_throw_exception") == "new HmacSHA256"


def test_focus_matches_the_method_named_in_the_test_over_earlier_calls():
    calls = [("new Email", 2), ("Email.equals", 4), ("Email.hashCode", 1)]
    assert _pick(calls, "EmailTest.when_same_address_expect_equal_and_hashCode") == "Email.equals"
    calls = [("new DefaultApplicationArguments", 3), ("DefaultApplicationArguments.getOptionValues", 2)]
    assert _pick(calls, "DefaultApplicationArgumentsTests.getOptionValues") == "DefaultApplicationArguments.getOptionValues"


def test_focus_prefers_what_the_test_calls_over_what_that_calls():
    steps, frames, functions = _spring_like([("UserRestController.loginUser", 3)], None, "IntegrationTest.auth_login")
    functions["A.java:9"] = {"qualname": "UserService.login", "file": "src/main/java/A.java"}
    frames["9"] = {"fn": "A.java:9", "parent": 2}
    steps += [{"kind": "call", "frame": 9}, {"kind": "line", "frame": 9}]
    fid = function_under_test(steps, frames, {"T.java:1"}, {}, {}, functions, {})
    assert functions[fid]["qualname"] == "UserRestController.loginUser"


def test_a_getter_that_does_work_is_not_an_accessor():
    calls = [("Clock.tick", 1), ("UserVehicleController.getVehicleDetailsText", 4)]
    assert _pick(calls, "UserVehicleControllerTests.getVehicleWhenRequestingText") == "UserVehicleController.getVehicleDetailsText"
    steps, frames, functions = _spring_like([("Listener.supportsEventType", 2), ("UserVehicleController.getVehicleDetailsText", 1)],
                                            None, "UserVehicleControllerTests.getVehicleWhenRequestingText")
    functions["A.java:9"] = {"qualname": "VehicleDetails.getMake", "file": "src/main/java/A.java"}
    frames["9"] = {"fn": "A.java:9", "parent": 3}  # the one-line getter calls into project code
    steps += [{"kind": "call", "frame": 9}, {"kind": "line", "frame": 9}]
    fid = function_under_test(steps, frames, {"T.java:1"}, {}, {}, functions, {})
    assert functions[fid]["qualname"] == "UserVehicleController.getVehicleDetailsText"


def test_the_exactly_named_handler_wins_even_behind_a_filter():
    steps, frames, functions = _spring_like([("JwtFilter.doFilterInternal", 5)], None,
                                            "CommentRestControllerTest.when_get_comments_expect_ok")
    functions["A.java:9"] = {"qualname": "CommentRestController.getComments", "file": "src/main/java/A.java"}
    frames["9"] = {"fn": "A.java:9", "parent": 2}
    steps += [{"kind": "call", "frame": 9}, {"kind": "line", "frame": 9}]
    fid = function_under_test(steps, frames, {"T.java:1"}, {}, {}, functions, {})
    assert functions[fid]["qualname"] == "CommentRestController.getComments"


def _behind_filter(test, handler, arrange=(), lines=3):
    """test -> [arrange calls] -> JwtFilter.doFilterInternal -> handler (as MockMvc records it
    when the filter is project code)."""
    steps, frames, functions = _spring_like([*arrange, ("JwtFilter.doFilterInternal", 4)], None, test)
    functions["A.java:9"] = {"qualname": handler, "file": "src/main/java/A.java"}
    frames["9"] = {"fn": "A.java:9", "parent": len(arrange) + 2}
    steps += [{"kind": "call", "frame": 9}] + [{"kind": "line", "frame": 9}] * lines
    fid = function_under_test(steps, frames, {"T.java:1"}, {}, {}, functions, {})
    return functions[fid]["qualname"]


def test_a_handler_behind_a_servlet_filter_counts_as_called_by_the_test():
    assert _behind_filter("UserSliceTest.create_article_with_tags", "ArticleRestController.postArticle",
                          arrange=[("UserRestController.postUser", 2)]) \
        == "ArticleRestController.postArticle"
    assert _behind_filter("UserSliceTest.profile_of_unknown_user_is_not_found",
                          "ProfileRestController.getProfileByUsername") == "ProfileRestController.getProfileByUsername"


def test_a_partial_name_never_picks_a_setter():
    calls = [("NamedEntity.setName", 1), ("PetValidator.validate", 6)]
    assert _pick(calls, "PetValidatorTests.validateWithInvalidPetName") == "PetValidator.validate"


def test_a_hook_that_raised_beats_hooks_that_did_not():
    steps, frames, functions = _spring_like([("OwnerController.setAllowedFields", 1), ("OwnerController.findOwner", 3)],
                                            None, "PetClinicOutageSliceTests.showOwnerWhenStoreIsDownFails")
    steps.append({"kind": "return", "frame": 3, "raised": True})
    ann = {"A.java:2": {"InitBinder"}, "A.java:3": {"ModelAttribute"}}
    fid = function_under_test(steps, frames, {"T.java:1"}, {}, {}, functions, ann)
    assert functions[fid]["qualname"] == "OwnerController.findOwner"
