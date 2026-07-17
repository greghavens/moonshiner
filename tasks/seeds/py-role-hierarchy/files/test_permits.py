"""Acceptance checks for permits.py. Run: python3 test_permits.py"""
from permits import (PermissionDenied, has_permission, grants_for,
                     known_roles, require, roles_granting)


# ---------------------------------------------------------------- existing

def test_flat_grants():
    assert has_permission(["viewer"], "article.read")
    assert not has_permission(["viewer"], "article.write")
    assert has_permission(["viewer", "moderator"], "comment.hide")
    assert not has_permission([], "article.read")
    try:
        has_permission(["viewer", "wizard"], "article.read")
        assert False, "unknown role tolerated"
    except KeyError:
        pass


def test_grants_for_and_roles_granting():
    assert "user.ban" in grants_for("admin")
    try:
        grants_for("wizard")
        assert False, "unknown role tolerated"
    except KeyError:
        pass
    assert roles_granting("comment.hide") == ["admin", "moderator"]
    assert roles_granting("nosuch.perm") == []


def test_known_roles_sorted():
    assert known_roles() == ["admin", "editor", "moderator", "viewer"]


def test_require_gate():
    require(["editor"], "article.write")  # must not raise
    try:
        require(["viewer"], "article.delete")
        assert False, "require let a viewer delete"
    except PermissionDenied as e:
        assert e.permission == "article.delete"
        assert isinstance(e, PermissionError)


# ---------------- feature: hierarchical roles with deny-overrides

def make_graph():
    from permits import RoleGraph
    g = RoleGraph()
    g.add_role("viewer", grants=["article.read", "comment.read"])
    g.add_role("editor", inherits=["viewer"],
               grants=["article.write", "comment.write"])
    g.add_role("senior_editor", inherits=["editor"],
               grants=["article.delete"])
    g.add_role("moderator", inherits=["viewer"],
               grants=["comment.hide", "user.warn"])
    g.add_role("contractor", inherits=["editor"],
               denies=["article.write", "article.delete"])
    g.add_role("lead", inherits=["senior_editor", "moderator"])
    return g


def test_grants_flow_down_the_hierarchy():
    g = make_graph()
    assert g.allowed(["editor"], "article.read")       # from viewer
    assert g.allowed(["senior_editor"], "comment.write")
    assert not g.allowed(["viewer"], "article.write")
    assert g.effective_grants("senior_editor") == frozenset({
        "article.read", "comment.read", "article.write", "comment.write",
        "article.delete",
    })


def test_deny_overrides_inherited_grant():
    g = make_graph()
    assert not g.allowed(["contractor"], "article.write")
    assert g.allowed(["contractor"], "article.read")
    assert g.allowed(["contractor"], "comment.write")
    assert g.effective_grants("contractor") == frozenset({
        "article.read", "comment.read", "comment.write",
    })


def test_deny_beats_regrant_further_down():
    g = make_graph()
    g.add_role("contractor_plus", inherits=["contractor"],
               grants=["article.write"])
    assert not g.allowed(["contractor_plus"], "article.write")


def test_deny_wins_across_combined_roles():
    g = make_graph()
    assert g.allowed(["editor"], "article.write")
    assert not g.allowed(["editor", "contractor"], "article.write")
    assert g.allowed(["editor", "contractor"], "comment.read")


def test_diamond_inheritance():
    g = make_graph()  # lead reaches viewer twice: via editor and moderator
    for perm in ["article.delete", "comment.hide", "user.warn",
                 "article.read"]:
        assert g.allowed(["lead"], perm), perm
    assert not g.allowed(["lead"], "user.ban")


def test_graph_validation():
    from permits import RoleGraph
    g = make_graph()
    try:
        g.add_role("viewer")
        assert False, "duplicate role accepted"
    except ValueError:
        pass
    try:
        g.add_role("temp", inherits=["wizard"])
        assert False, "unknown parent accepted"
    except KeyError:
        pass
    try:
        RoleGraph().add_role("  ")
        assert False, "blank role name accepted"
    except ValueError:
        pass
    for call in [lambda: g.allowed(["wizard"], "article.read"),
                 lambda: g.effective_grants("wizard")]:
        try:
            call()
            assert False, "unknown role tolerated"
        except KeyError:
            pass
    assert not g.allowed([], "article.read")


def test_graph_require_raises_permission_denied():
    g = make_graph()
    g.require(["editor"], "article.write")  # must not raise
    try:
        g.require(["contractor"], "article.write")
        assert False, "require let a denied contractor write"
    except PermissionDenied as e:
        assert e.permission == "article.write"


EXISTING = [
    test_flat_grants,
    test_grants_for_and_roles_granting,
    test_known_roles_sorted,
    test_require_gate,
]

FEATURE = [
    test_grants_flow_down_the_hierarchy,
    test_deny_overrides_inherited_grant,
    test_deny_beats_regrant_further_down,
    test_deny_wins_across_combined_roles,
    test_diamond_inheritance,
    test_graph_validation,
    test_graph_require_raises_permission_denied,
]


def main():
    failures = 0
    for t in EXISTING + FEATURE:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(EXISTING + FEATURE))


if __name__ == "__main__":
    main()
