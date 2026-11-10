# Mocked calls and library calls

Every call a function under test makes is tappable in the viewer, not only the
ones whose code ran.

| chip | what the call was | tap it |
|---|---|---|
| `↘ quote` (solid) | project code that ran in this flow | jump into that frame, at that call |
| `↘ updateBalance · mock` (dashed) | project code a mock or stub stood in for | open the real code, not run |
| `subtract()` (grey) | the JDK, a framework or a library | see what it got and gave back |

The same goes for the sequence diagram: a mock shows as its own actor
(`mock UserAccountService`), and tapping its call arrow opens the real code.

## The real code, not run

When a test mocks `UserAccountService`, the call
`userAccountService.updateBalance("111", 400)` never reaches project code.
Tap its chip and the flowchart shows the implementation
(`UserAccountServiceImpl.updateBalance`), dashed, under a banner:

> **Not run in this flow:** replaced by a mock in the test.
> Called with `accountNumber = "111", newBalance = 400` · the mock returned `void`

The variables panel shows the same arguments and the mock's answer; the
source panel jumps to the method. The call path reads
`TransactionServiceImpl.transfer › UserAccountServiceImpl.updateBalance not run`:
tap the caller, **↗ out** or ✕ to go back. **↘ into** on a line that calls a
mock opens that real code first, then goes on to the next step.

Which code: the mocked type's implementations in the project (a class that
implements the interface or extends the class, directly or through others),
else the type's own method (a mocked class). With several implementations,
the banner lists them to pick from. Calls are matched by name and number of
arguments. A mock's arguments are named after the implementation's parameters
(Mockito's generated classes carry no names).

Recognised stand-ins:

- Java: Mockito mocks of interfaces and classes (`@Mock`, `mock(...)`,
  including the inline mock maker, Mockito 5's default).
- Python: `unittest.mock` mocks (`Mock`, `MagicMock`, `patch`, `create_autospec`;
  with a `spec` the class is known, without one the call is matched by name)
  and a test's own function or lambda swapped in by `monkeypatch.setattr`
  (shown as a stub).

## Library calls

A grey chip is a call into code outside the project: the JDK, Spring, Lombok's
generated getters, Python's standard library. Hover or tap it for each call's
arguments and result. Only calls the source spells out get a chip (not
autoboxing or string concatenation). On a phone they collapse into one
`+3 calls` chip per node.

## Limits

- Python built-in functions and methods written in C (`len`, `dict.get`,
  `str.upper`) show the first argument at most and no result: `sys.monitoring`
  does not report more. Library code written in Python shows everything.
- The real code is matched statically: a call through a variable whose mock
  has no spec, or a name declared in many places (more than 8), is not
  matched and shows as a library call.
- Calls made from inside a library callback (a lambda passed to a stream, say)
  are not listed.
- Up to 3,000 such calls per recording.
