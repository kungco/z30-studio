use z30_core::{
    editor::PreviewEngine,
    portrait::{Binding, Face, Point, Portrait},
    recipe::Recipe,
    skin::SkinMask,
};
fn setup() -> (tempfile::TempDir, std::path::PathBuf, Recipe, Binding) {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("face-fixture.png");
    image::RgbImage::from_fn(256, 192, |x, y| {
        let mut v = 145. + x as f64 * 0.04 + y as f64 * 0.015;
        for (cx, cy) in [
            (82., 110.),
            (173., 112.),
            (77., 58.),
            (128., 101.),
            (12., 40.),
        ] {
            v *= 1.
                - 0.55
                    * (-((x as f64 - cx).powi(2) + (y as f64 - cy).powi(2)) / (2. * 1.5 * 1.5))
                        .exp();
        }
        image::Rgb([v.round() as u8; 3])
    })
    .save(&path)
    .unwrap();
    let binding = Binding::capture(&path, 256, 192).unwrap();
    let recipe = Recipe {
        portrait: Portrait {
            binding: Some(binding),
            face: Some(Face {
                rect: [0., 0., 1., 1.],
                landmarks: [
                    Point { x: 0.3, y: 0.3 },
                    Point { x: 0.7, y: 0.3 },
                    Point { x: 0.5, y: 0.5 },
                    Point { x: 0.38, y: 0.75 },
                    Point { x: 0.62, y: 0.75 },
                ],
                confidence: 1.,
            }),
            smooth: 0.,
        },
        skin: Some(SkinMask::new(binding, vec![255; 256 * 256]).unwrap()),
        ..Default::default()
    };
    (root, path, recipe, binding)
}
#[test]
fn cached_semantic_analysis_protects_eyes_nose_and_background() {
    let (_r, path, recipe, binding) = setup();
    let before = std::fs::read(&path).unwrap();
    let mut engine = PreviewEngine::new(None).unwrap();
    engine.preview(&path, recipe.clone()).unwrap();
    let points = engine
        .propose_blemishes(&path, &recipe, binding, (256, 192), || false)
        .unwrap();
    assert_eq!(points.len(), 2);
    for p in points {
        assert!(
            [(82., 110.), (173., 112.)]
                .iter()
                .any(|&(x, y)| (p.x * 255. - x).hypot(p.y * 191. - y) < 2.)
        );
    }
    assert_eq!(std::fs::read(path).unwrap(), before);
}
#[test]
fn missing_gate_wrong_cache_replacement_and_cancellation_never_return_a_plan() {
    let (_r, path, recipe, binding) = setup();
    let mut engine = PreviewEngine::new(None).unwrap();
    assert!(
        engine
            .propose_blemishes(&path, &recipe, binding, (256, 192), || false)
            .is_err()
    );
    engine.preview(&path, recipe.clone()).unwrap();
    let mut absent = recipe.clone();
    absent.skin = None;
    assert!(
        engine
            .propose_blemishes(&path, &absent, binding, (256, 192), || false)
            .is_err()
    );
    assert!(
        engine
            .propose_blemishes(&path, &recipe, binding, (128, 96), || false)
            .is_err()
    );
    assert!(
        engine
            .propose_blemishes(&path, &recipe, binding, (256, 192), || true)
            .is_err()
    );
    std::fs::write(&path, b"separate fixture replaced externally").unwrap();
    assert!(
        engine
            .propose_blemishes(&path, &recipe, binding, (256, 192), || false)
            .is_err()
    );
}
#[test]
fn black_semantic_mask_and_already_repaired_regions_are_quiet() {
    let (_r, path, mut recipe, binding) = setup();
    let mut engine = PreviewEngine::new(None).unwrap();
    engine.preview(&path, recipe.clone()).unwrap();
    let points = engine
        .propose_blemishes(&path, &recipe, binding, (256, 192), || false)
        .unwrap();
    assert!(!points.is_empty());
    recipe.repairs = Some(z30_core::repairs::Repairs::new(binding, points).unwrap());
    assert!(
        engine
            .propose_blemishes(&path, &recipe, binding, (256, 192), || false)
            .unwrap()
            .is_empty()
    );
    recipe.repairs = None;
    recipe.skin = Some(SkinMask::new(binding, vec![0; 256 * 256]).unwrap());
    assert!(
        engine
            .propose_blemishes(&path, &recipe, binding, (256, 192), || false)
            .unwrap()
            .is_empty()
    );
}
