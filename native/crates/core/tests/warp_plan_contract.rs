use z30_core::{
    portrait::Binding,
    repair_view::Point,
    warp::{Map, Pin},
    warp_plan,
    warps::Warps,
};
fn binding(w: u32, h: u32) -> (tempfile::TempDir, Binding) {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("source");
    std::fs::write(&path, b"original").unwrap();
    let b = Binding::capture(&path, w, h).unwrap();
    (root, b)
}
fn p(x: f64, y: f64) -> Point {
    Point { x, y }
}
#[test]
fn wide_portrait_and_rounded_preview_drags_obey_both_pixel_metrics() {
    for (full, small) in [
        ((5568, 3712), (1600, 1067)),
        ((3711, 5567), (1066, 1600)),
        ((33, 17), (12, 6)),
    ] {
        let (_r, b) = binding(full.0, full.1);
        let plan = warp_plan::plan(None, b, small, p(0.5, 0.5), p(0.8, 0.7), 0.12)
            .unwrap()
            .unwrap();
        assert!(plan.limited);
        let pins = plan.warps.pins();
        for size in [full, small] {
            let map = Map::new(size.0, size.1, pins).unwrap();
            let pin = pins[0];
            let q = map
                .source_at(
                    pin.to[0] * (size.0 - 1) as f64,
                    pin.to[1] * (size.1 - 1) as f64,
                )
                .unwrap();
            assert!((q[0] - 0.5 * (size.0 - 1) as f64).abs() < 1e-9);
            assert!((q[1] - 0.5 * (size.1 - 1) as f64).abs() < 1e-9);
        }
    }
}
#[test]
fn append_is_in_current_display_space_and_preserves_the_original_recipe() {
    let (_r, b) = binding(300, 200);
    let old = Warps::new(
        b,
        vec![Pin {
            from: [0.495, 0.5],
            to: [0.5, 0.5],
            radius: 0.2,
        }],
    )
    .unwrap();
    let saved = serde_json::to_vec(&old).unwrap();
    let plan = warp_plan::plan(Some(&old), b, (300, 200), p(0.5, 0.5), p(0.502, 0.5), 0.2)
        .unwrap()
        .unwrap();
    assert!(!plan.limited);
    let combined = Map::new(300, 200, plan.warps.pins())
        .unwrap()
        .source_at(0.502 * 299., 0.5 * 199.)
        .unwrap();
    let wanted = Map::new(300, 200, old.pins())
        .unwrap()
        .source_at(0.5 * 299., 0.5 * 199.)
        .unwrap();
    assert!((combined[0] - wanted[0]).abs() < 1e-10);
    assert!((combined[1] - wanted[1]).abs() < 1e-10);
    assert_eq!(serde_json::to_vec(&old).unwrap(), saved);
    let half = old.with_amount(0.5).unwrap();
    assert_eq!(
        warp_plan::plan(Some(&half), b, (300, 200), p(0.5, 0.5), p(0.502, 0.5), 0.2)
            .unwrap()
            .unwrap()
            .warps
            .amount(),
        0.5
    );
}
#[test]
fn clicks_are_noops_and_unsafe_input_never_becomes_a_recipe() {
    let (_r, b) = binding(300, 200);
    assert!(
        warp_plan::plan(None, b, (300, 200), p(0.5, 0.5), p(0.5, 0.5), 0.1)
            .unwrap()
            .is_none()
    );
    for (from, to, radius, size) in [
        (p(f64::NAN, 0.5), p(0.6, 0.5), 0.1, (300, 200)),
        (p(0.5, 0.5), p(1.1, 0.5), 0.1, (300, 200)),
        (p(0.01, 0.01), p(0.02, 0.02), 0.2, (300, 200)),
        (p(0.5, 0.5), p(0.6, 0.5), 0.3, (300, 200)),
        (p(0.5, 0.5), p(0.6, 0.5), 0.1, (1, 200)),
    ] {
        assert!(warp_plan::plan(None, b, size, from, to, radius).is_err());
    }
}
#[test]
fn count_zero_strength_and_cross_source_bindings_are_guarded() {
    let (_r, b) = binding(300, 200);
    let pin = Pin {
        from: [0.495, 0.5],
        to: [0.5, 0.5],
        radius: 0.2,
    };
    let four = Warps::new(b, vec![pin; 4]).unwrap();
    assert!(warp_plan::plan(Some(&four), b, (300, 200), p(0.5, 0.5), p(0.51, 0.5), 0.2).is_err());
    let old = Warps::new(b, vec![pin]).unwrap();
    let off = old.with_amount(0.).unwrap();
    assert!(warp_plan::plan(Some(&off), b, (300, 200), p(0.5, 0.5), p(0.51, 0.5), 0.2).is_err());
    let (_other, c) = binding(300, 200);
    assert!(warp_plan::plan(Some(&old), c, (300, 200), p(0.5, 0.5), p(0.51, 0.5), 0.2).is_err());
}
