//! Acceptance contract for the rs_rcweaktree outline tree.
//! Protected file: the implementation must satisfy these tests as written.
//!
//! Reference-count assertions are exact on purpose: they are how we prove
//! the ownership design (children strong, parent weak) instead of trusting
//! a comment.

use rs_rcweaktree::{Node, TreeError};
use std::rc::Rc;

fn labels_of(nodes: &[Rc<Node>]) -> Vec<String> {
    nodes.iter().map(|n| n.label().to_string()).collect()
}

#[test]
fn fresh_node_shape_and_counts() {
    let n = Node::new("intro");
    assert_eq!(n.label(), "intro");
    assert!(n.parent().is_none());
    assert!(n.children().is_empty());
    assert_eq!(n.path_to_root(), vec!["intro".to_string()]);
    assert_eq!(Rc::strong_count(&n), 1);
    assert_eq!(Rc::weak_count(&n), 0);
}

#[test]
fn attach_wires_both_directions_with_exact_counts() {
    let root = Node::new("root");
    let a = Node::new("a");
    assert_eq!(Node::attach(&root, &a), Ok(()));

    assert_eq!(Rc::strong_count(&a), 2, "test handle + the parent's child slot");
    assert_eq!(Rc::weak_count(&a), 0);
    assert_eq!(
        Rc::strong_count(&root),
        1,
        "a parent link must never be strong — that is the leak we are killing"
    );
    assert_eq!(Rc::weak_count(&root), 1, "exactly the child's parent link");

    assert!(Rc::ptr_eq(&a.parent().unwrap(), &root));
    assert_eq!(labels_of(&root.children()), vec!["a"]);
}

#[test]
fn attach_rejects_a_child_that_already_has_a_parent() {
    let root = Node::new("root");
    let other = Node::new("other");
    let a = Node::new("a");
    Node::attach(&root, &a).unwrap();

    assert_eq!(Node::attach(&root, &a), Err(TreeError::AlreadyAttached));
    assert_eq!(Node::attach(&other, &a), Err(TreeError::AlreadyAttached));

    assert_eq!(root.children().len(), 1, "failed attach must not duplicate the child");
    assert!(other.children().is_empty());
    assert_eq!(Rc::strong_count(&a), 2);
    assert!(Rc::ptr_eq(&a.parent().unwrap(), &root));
}

#[test]
fn children_keep_insertion_order() {
    let root = Node::new("root");
    for l in ["x", "y", "z"] {
        Node::attach(&root, &Node::new(l)).unwrap();
    }
    assert_eq!(labels_of(&root.children()), vec!["x", "y", "z"]);
}

#[test]
fn parent_upgrades_to_the_real_node() {
    let a = Node::new("a");
    let b = Node::new("b");
    Node::attach(&a, &b).unwrap();
    let p = b.parent().expect("attached child must reach its parent");
    assert!(Rc::ptr_eq(&p, &a));
}

#[test]
fn path_to_root_walks_the_weak_chain() {
    let root = Node::new("root");
    let a = Node::new("a");
    let b = Node::new("b");
    Node::attach(&root, &a).unwrap();
    Node::attach(&a, &b).unwrap();
    assert_eq!(
        b.path_to_root(),
        vec!["b".to_string(), "a".to_string(), "root".to_string()]
    );
    assert_eq!(a.path_to_root(), vec!["a".to_string(), "root".to_string()]);
}

#[test]
fn detach_unlinks_both_directions_with_exact_counts() {
    let root = Node::new("root");
    let a = Node::new("a");
    let b = Node::new("b");
    Node::attach(&root, &a).unwrap();
    Node::attach(&a, &b).unwrap();
    assert_eq!(Rc::strong_count(&b), 2);
    assert_eq!(Rc::weak_count(&a), 1);

    assert!(b.detach(), "detaching an attached node reports true");

    assert_eq!(Rc::strong_count(&b), 1, "the parent's strong slot must be gone");
    assert_eq!(Rc::weak_count(&a), 0, "the child's weak parent link must be gone");
    assert!(a.children().is_empty());
    assert!(b.parent().is_none());
    assert_eq!(b.path_to_root(), vec!["b".to_string()]);
    assert!(!b.detach(), "second detach is a no-op false");
    assert!(a.parent().is_some(), "the rest of the tree is untouched");
}

#[test]
fn attach_to_self_is_would_cycle() {
    let n = Node::new("loner");
    assert_eq!(Node::attach(&n, &n), Err(TreeError::WouldCycle));
    assert!(n.children().is_empty());
    assert!(n.parent().is_none());
    assert_eq!(Rc::strong_count(&n), 1);
    assert_eq!(Rc::weak_count(&n), 0);
}

#[test]
fn attach_own_ancestor_is_would_cycle_and_wins_over_already_attached() {
    let root = Node::new("root");
    let a = Node::new("a");
    let b = Node::new("b");
    Node::attach(&root, &a).unwrap();
    Node::attach(&a, &b).unwrap();

    // `a` is b's ancestor AND already has a parent: the cycle answer wins.
    assert_eq!(Node::attach(&b, &a), Err(TreeError::WouldCycle));
    // Same for the root, which has no parent:
    assert_eq!(Node::attach(&b, &root), Err(TreeError::WouldCycle));

    assert!(Rc::ptr_eq(&a.parent().unwrap(), &root));
    assert!(b.children().is_empty());
    assert_eq!(Rc::weak_count(&a), 1);
}

#[test]
fn reparent_moves_a_subtree_intact() {
    let root = Node::new("root");
    let a = Node::new("a");
    let b = Node::new("b");
    let c = Node::new("c");
    let g = Node::new("g");
    Node::attach(&root, &a).unwrap();
    Node::attach(&root, &b).unwrap();
    Node::attach(&a, &c).unwrap();
    Node::attach(&c, &g).unwrap();

    assert_eq!(Node::reparent(&c, &b), Ok(()));

    assert!(a.children().is_empty());
    assert_eq!(labels_of(&b.children()), vec!["c"]);
    assert!(Rc::ptr_eq(&c.parent().unwrap(), &b));
    assert_eq!(
        g.path_to_root(),
        vec!["g".to_string(), "c".to_string(), "b".to_string(), "root".to_string()],
        "grandchildren must ride along"
    );
    assert_eq!(Rc::strong_count(&c), 2, "exactly one parent slot holds c");
}

#[test]
fn reparent_under_own_descendant_is_rejected_and_changes_nothing() {
    let root = Node::new("root");
    let a = Node::new("a");
    let c = Node::new("c");
    Node::attach(&root, &a).unwrap();
    Node::attach(&a, &c).unwrap();

    assert_eq!(Node::reparent(&a, &c), Err(TreeError::WouldCycle));

    assert!(Rc::ptr_eq(&a.parent().unwrap(), &root));
    assert!(Rc::ptr_eq(&c.parent().unwrap(), &a));
    assert!(c.children().is_empty());
    assert_eq!(labels_of(&root.children()), vec!["a"]);
    assert_eq!(labels_of(&a.children()), vec!["c"]);
}

#[test]
fn reparent_to_itself_is_rejected() {
    let root = Node::new("root");
    let a = Node::new("a");
    Node::attach(&root, &a).unwrap();
    assert_eq!(Node::reparent(&a, &a), Err(TreeError::WouldCycle));
    assert!(Rc::ptr_eq(&a.parent().unwrap(), &root));
}

#[test]
fn reparent_to_the_same_parent_moves_it_to_the_back() {
    let root = Node::new("root");
    let a = Node::new("a");
    let b = Node::new("b");
    let c = Node::new("c");
    Node::attach(&root, &a).unwrap();
    Node::attach(&root, &b).unwrap();
    Node::attach(&root, &c).unwrap();

    assert_eq!(Node::reparent(&a, &root), Ok(()));
    assert_eq!(labels_of(&root.children()), vec!["b", "c", "a"]);
    assert_eq!(Rc::strong_count(&a), 2, "no duplicate slot after the move");
}

#[test]
fn reparent_a_detached_root_acts_as_attach() {
    let root = Node::new("root");
    let x = Node::new("x");
    let y = Node::new("y");
    Node::attach(&x, &y).unwrap();

    assert_eq!(Node::reparent(&x, &root), Ok(()));
    assert_eq!(labels_of(&root.children()), vec!["x"]);
    assert_eq!(
        y.path_to_root(),
        vec!["y".to_string(), "x".to_string(), "root".to_string()]
    );
}

#[test]
fn dropping_the_root_frees_every_descendant() {
    let root = Node::new("root");
    let a = Node::new("a");
    let b = Node::new("b");
    let c = Node::new("c");
    let d = Node::new("d");
    Node::attach(&root, &a).unwrap();
    Node::attach(&a, &b).unwrap();
    Node::attach(&b, &c).unwrap();
    Node::attach(&a, &d).unwrap();

    let weaks = [
        Rc::downgrade(&root),
        Rc::downgrade(&a),
        Rc::downgrade(&b),
        Rc::downgrade(&c),
        Rc::downgrade(&d),
    ];

    drop(a);
    drop(b);
    drop(c);
    drop(d);
    assert!(
        weaks[1].upgrade().is_some(),
        "interior nodes must stay alive while the root holds them"
    );

    drop(root);
    for (i, w) in weaks.iter().enumerate() {
        assert!(
            w.upgrade().is_none(),
            "node {i} still alive after the root dropped — a reference cycle is keeping it"
        );
    }
}

#[test]
fn detached_subtree_survives_the_root_dropping() {
    let root = Node::new("root");
    let a = Node::new("a");
    let b = Node::new("b");
    Node::attach(&root, &a).unwrap();
    Node::attach(&a, &b).unwrap();
    let w_root = Rc::downgrade(&root);

    assert!(a.detach());
    drop(root);

    assert!(w_root.upgrade().is_none(), "nothing may keep the old root alive");
    assert_eq!(b.path_to_root(), vec!["b".to_string(), "a".to_string()]);
    assert_eq!(labels_of(&a.children()), vec!["b"]);
    assert!(a.parent().is_none());
}

#[test]
fn node_whose_parent_was_dropped_behaves_as_a_root() {
    let a = Node::new("a");
    {
        let root = Node::new("root");
        Node::attach(&root, &a).unwrap();
        assert!(a.parent().is_some());
    } // root goes out of scope; only the test held it strongly

    assert_eq!(Rc::strong_count(&a), 1);
    assert!(a.parent().is_none(), "dangling parent link must read as no parent");
    assert_eq!(a.path_to_root(), vec!["a".to_string()]);
    assert!(!a.detach(), "detach on an orphan is a no-op false");
}

#[test]
fn preorder_walks_depth_first_in_child_order() {
    let root = Node::new("root");
    let a = Node::new("a");
    let b = Node::new("b");
    let c = Node::new("c");
    let d = Node::new("d");
    Node::attach(&root, &a).unwrap();
    Node::attach(&a, &b).unwrap();
    Node::attach(&a, &c).unwrap();
    Node::attach(&root, &d).unwrap();

    assert_eq!(
        labels_of(&Node::preorder(&root)),
        vec!["root", "a", "b", "c", "d"]
    );
    assert_eq!(labels_of(&Node::preorder(&a)), vec!["a", "b", "c"]);
}

#[test]
fn find_returns_the_first_preorder_match() {
    let root = Node::new("root");
    let a = Node::new("a");
    let c1 = Node::new("c");
    let d = Node::new("d");
    let c2 = Node::new("c");
    Node::attach(&root, &a).unwrap();
    Node::attach(&a, &c1).unwrap();
    Node::attach(&root, &d).unwrap();
    Node::attach(&d, &c2).unwrap();

    let hit = Node::find(&root, "c").expect("label exists");
    assert!(
        Rc::ptr_eq(&hit, &c1),
        "with duplicate labels, preorder decides which one wins"
    );
    assert!(Rc::ptr_eq(&Node::find(&root, "d").unwrap(), &d));
    assert!(Node::find(&root, "missing").is_none());
    assert!(Node::find(&d, "a").is_none(), "find searches only the subtree");
}

#[test]
fn no_leak_after_a_reparent_shuffle() {
    let root = Node::new("root");
    let a = Node::new("a");
    let b = Node::new("b");
    let c = Node::new("c");
    Node::attach(&root, &a).unwrap();
    Node::attach(&root, &b).unwrap();
    Node::attach(&a, &c).unwrap();

    Node::reparent(&c, &b).unwrap(); // root -> {a, b -> c}
    Node::reparent(&a, &c).unwrap(); // root -> b -> c -> a

    assert_eq!(
        a.path_to_root(),
        vec![
            "a".to_string(),
            "c".to_string(),
            "b".to_string(),
            "root".to_string()
        ]
    );

    let weaks = [
        Rc::downgrade(&root),
        Rc::downgrade(&a),
        Rc::downgrade(&b),
        Rc::downgrade(&c),
    ];
    drop(a);
    drop(b);
    drop(c);
    drop(root);
    for (i, w) in weaks.iter().enumerate() {
        assert!(
            w.upgrade().is_none(),
            "node {i} leaked after the reparent shuffle"
        );
    }
}
