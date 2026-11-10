from wizualizer.call_targets import called_type, resolve, subtypes
from wizualizer.ports import Declaration as D
from wizualizer.trace import link_calls

DECLARED = {
    "src/main/java/bank/AccountServiceImpl.java": [
        D("updateBalance", "AccountServiceImpl.updateBalance", 46, "AccountServiceImpl", ("AccountService",),
          ("accountNumber", "newBalance")),
        D("getName", "AccountServiceImpl.getName", 30, "AccountServiceImpl", ("AccountService",), ("accountNumber",)),
    ],
    "src/main/java/bank/AuditedAccounts.java": [
        D("getName", "AuditedAccounts.getName", 12, "AuditedAccounts", ("BaseAccounts",), ("accountNumber",)),
    ],
    "src/main/java/bank/BaseAccounts.java": [
        D("audit", "BaseAccounts.audit", 5, "BaseAccounts", ("AccountService",), ()),
    ],
    "app/gateway.py": [
        D("charge", "CardGateway.charge", 11, "CardGateway", ("PaymentGateway",), ("account", "cents")),
        D("charge", "WireGateway.charge", 20, "WireGateway", ("PaymentGateway",), ("account", "cents")),
        D("send_receipt", "send_receipt", 30, "", (), ("email", "text")),
    ],
}


def names(hits):
    return [d.qualname for _, d in hits]


def test_called_type_is_the_simple_name():
    assert called_type({"type": "com.x.AccountService"}) == "AccountService"
    assert called_type({"type": "com.x.Outer$Inner"}) == "Inner"
    assert called_type({"type": None}) is None


def test_interface_resolves_to_its_implementation():
    call = {"name": "updateBalance", "type": "bank.AccountService", "arity": 2, "mock": True}
    assert names(resolve(call, DECLARED)) == ["AccountServiceImpl.updateBalance"]


def test_implementations_through_an_abstract_class_are_found_too():
    assert subtypes(DECLARED)["AccountService"] == {"AccountServiceImpl", "BaseAccounts", "AuditedAccounts"}
    call = {"name": "getName", "type": "bank.AccountService", "arity": 1}
    assert names(resolve(call, DECLARED)) == ["AccountServiceImpl.getName", "AuditedAccounts.getName"]


def test_several_implementations_are_all_offered():
    call = {"name": "charge", "type": "app.gateway.PaymentGateway", "mock": True}
    assert names(resolve(call, DECLARED)) == ["CardGateway.charge", "WireGateway.charge"]


def test_a_stub_without_a_type_matches_by_name():
    assert names(resolve({"name": "send_receipt", "type": None, "stub": True}, DECLARED)) == ["send_receipt"]


def test_library_calls_and_constructors_resolve_to_nothing():
    assert resolve({"name": "subtract", "type": "java.math.BigDecimal", "arity": 1}, DECLARED) == []
    assert resolve({"name": "get", "type": "builtins.dict"}, DECLARED) == []
    assert resolve({"name": "new AccountServiceImpl", "type": "bank.AccountServiceImpl"}, DECLARED) == []


class Source:
    """A SourceModel stand-in over DECLARED."""

    def handles(self, path):
        return path.endswith((".java", ".py"))

    def declarations(self, text):
        return DECLARED[text.split("\n")[0]]

    def flowchart(self, text, line):
        return {"nodes": [{"id": 0, "kind": "start", "label": f"{text.split()[0]}:{line}", "lines": [line, line],
                           "depth": 0}], "edges": []}


def read(rel):
    """A file's "source": its path, then the calls it declares."""
    return rel + "\n" + " ".join(f"{d.name}(" for d in DECLARED.get(rel, []))


def test_link_calls_adds_the_target_not_run_and_names_the_mock_arguments():
    functions = {"src/main/java/bank/Transfers.java:10": {"name": "transfer", "file": "src/main/java/bank/Transfers.java",
                                                          "line": 10, "test": False}}
    frames = {"2": {"fn": "src/main/java/bank/Transfers.java:10", "parent": 1}}
    calls = [
        {"frame": 2, "line": 12, "after": 3, "name": "updateBalance", "type": "bank.AccountService", "arity": 2,
         "args": {"arg0": '"111"', "arg1": "400"}, "value": "void", "mock": True},
        {"frame": 2, "line": 13, "after": 4, "name": "subtract", "type": "java.math.BigDecimal", "arity": 1,
         "args": {"subtrahend": "100"}, "value": "400"},
    ]
    files = {}
    link_calls(calls, functions, frames, files, Source(), read_file=read,
               list_files=lambda: sorted(DECLARED) + ["src/main/java/bank/AccountService.java"])
    target = "src/main/java/bank/AccountServiceImpl.java:46"
    assert calls[0]["targets"] == [target]
    assert calls[0]["args"] == {"accountNumber": '"111"', "newBalance": "400"}
    assert "targets" not in calls[1]
    assert functions[target]["ran"] is False and functions[target]["qualname"] == "AccountServiceImpl.updateBalance"
    assert functions[target]["flowchart"]["nodes"][0]["label"].endswith(":46")
    assert "src/main/java/bank/AccountServiceImpl.java" in files
