"""Static check: garuda_mono.py must not need the stereo modules unless a stereo flag is set
(GARUDA_MONO_OAK_PAIR=1, or GARUDA_MONO_OAK_BOX=1 for host box stereo).

garuda_mono.py cannot be imported off the Pi (Hailo runtime), so this inspects its syntax tree.
It guards the deployment rule that the default path keeps working on a target where
oak_pair.py, oak_distance.py and oak_stereo_state.py were never copied (review astra-0010).
"""
import ast
from pathlib import Path

STEREO_ONLY = {"oak_stereo_state", "oak_pair", "oak_distance", "oak_depth_view", "oak_box"}
FLAGS = {"OAK_PAIR", "OAK_BOX"}       # both default to off; each enables one stereo mode
STEREO_ONLY_NAMES = {"OakPairBackend"}
SOURCE = Path(__file__).with_name("garuda_mono.py").read_text()


def is_positive_flag_test(test):
    """True only for a stereo flag itself, an `and` whose terms include one positively, or
    an `or` whose terms are ALL such guards (`OAK_PAIR or OAK_BOX`). `not OAK_PAIR`,
    `OAK_PAIR or x` and anything else do not count as a guard."""
    if isinstance(test, ast.Name):
        return test.id in FLAGS
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return any(is_positive_flag_test(v) for v in test.values)
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return all(is_positive_flag_test(v) for v in test.values)
    return False


def guarded_imports(source=None):
    """Yield (module, names, lineno, guarded) for every import in the file."""
    tree = ast.parse(SOURCE if source is None else source)

    def walk(node, guarded):
        for child in ast.iter_child_nodes(node):
            g = guarded
            if isinstance(node, ast.If) and child in node.body:
                g = guarded or is_positive_flag_test(node.test)
            if isinstance(child, ast.ImportFrom):
                yield child.module, {a.name for a in child.names}, child.lineno, g
            elif isinstance(child, ast.Import):
                for a in child.names:
                    yield a.name, set(), child.lineno, g
            yield from walk(child, g)
    yield from walk(tree, False)


def test_stereo_modules_are_only_imported_under_the_oak_pair_flag():
    found = 0
    for module, names, lineno, guarded in guarded_imports():
        if module in STEREO_ONLY or names & STEREO_ONLY_NAMES:
            found += 1
            assert guarded, f"line {lineno}: {module} {sorted(names)} imported outside `if OAK_PAIR`"
    assert found >= 4            # the check is looking at something


def test_the_flag_defaults_to_off():
    assert 'os.environ.get("GARUDA_MONO_OAK_PAIR", "0") == "1"' in SOURCE
    assert 'os.environ.get("GARUDA_MONO_OAK_BOX", "0") == "1"' in SOURCE


def test_guard_recognition_is_strict():
    def guard(expr):
        return is_positive_flag_test(ast.parse(expr, mode="eval").body)
    assert guard("OAK_PAIR") and guard("OAK_BOX") and guard("OAK_PAIR or OAK_BOX")
    assert guard("(OAK_PAIR or OAK_BOX) and store is not None") and guard("OAK_BOX and x")
    assert not guard("OAK_PAIR or x") and not guard("not OAK_BOX") and not guard("store is not None")
    assert not guard("OAK_BOX or (OAK_PAIR and x) or y")


def test_mono_state_imports_nothing_from_the_stereo_modules():
    tree = ast.parse(Path(__file__).with_name("mono_state.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
           {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {"math"}


def _guard(condition):
    src = f"if {condition}:\n    from oak_stereo_state import EpochGate\n"
    return [g for m, _, _, g in guarded_imports(src) if m == "oak_stereo_state"]


def test_guard_recognition_is_structural_not_textual():
    assert _guard("OAK_PAIR") == [True]
    assert _guard("OAK_PAIR and x") == [True]
    assert _guard("x and OAK_PAIR and y") == [True]
    for not_a_guard in ("not OAK_PAIR", "OAK_PAIR or True", "x or OAK_PAIR", "True",
                        "OAK_PAIR_OTHER", "f(OAK_PAIR)", "OAK_PAIR == 0", "x and not OAK_PAIR"):
        assert _guard(not_a_guard) == [False], not_a_guard


def test_else_branch_of_the_flag_is_not_guarded():
    src = "if OAK_PAIR:\n    pass\nelse:\n    from oak_stereo_state import EpochGate\n"
    assert [g for m, _, _, g in guarded_imports(src) if m == "oak_stereo_state"] == [False]
