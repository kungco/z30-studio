use z30_core::{
    editing::EditingSession,
    editor::PreviewEngine,
    recipe::Recipe,
    repair_plan::{Action, Settings},
    repair_view::Point,
    retouch::Mode,
    session::{Photo, Session},
};
fn setup() -> (
    tempfile::TempDir,
    Photo,
    PreviewEngine,
    z30_core::portrait::Binding,
    (u32, u32),
) {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("source.png");
    image::RgbImage::from_fn(256, 192, |x, y| {
        let v = if (120..136).contains(&x) && (90..104).contains(&y) {
            20
        } else {
            80 + (x / 3) as u8
        };
        image::Rgb([v, 60 + (y / 4) as u8, 70 + ((x + y) % 19) as u8])
    })
    .save(&path)
    .unwrap();
    let photo = Photo {
        path: path.canonicalize().unwrap(),
        rating: 0,
        flag: 0,
        recipe: Recipe::default(),
    };
    let mut engine = PreviewEngine::new(None).unwrap();
    let shown = engine.preview(&photo.path, photo.recipe.clone()).unwrap();
    let binding = shown.binding;
    let size = (shown.rendered.width, shown.rendered.height);
    (root, photo, engine, binding, size)
}
fn add(mode: Mode, donor: Option<Point>) -> Action {
    Action::Add {
        target: Point { x: 0.5, y: 0.5 },
        mode,
        settings: Settings {
            radius: 0.06,
            ..Default::default()
        },
        donor,
    }
}
#[test]
fn preview_planning_is_source_bound_requires_explicit_clone_donor_and_keeps_original_pixels() {
    let (_root, photo, engine, binding, size) = setup();
    let original = std::fs::read(&photo.path).unwrap();
    assert!(
        engine
            .plan_repair(
                &photo.path,
                &photo.recipe,
                binding,
                size,
                add(Mode::Clone, None)
            )
            .is_err()
    );
    let clone = engine
        .plan_repair(
            &photo.path,
            &photo.recipe,
            binding,
            size,
            add(Mode::Clone, Some(Point { x: 0.75, y: 0.5 })),
        )
        .unwrap();
    assert_eq!(clone.selected, 0);
    assert_eq!(clone.repairs.binding(), binding);
    assert_eq!(clone.repairs.points()[0].sx, 0.75);
    let healed = engine
        .plan_repair(
            &photo.path,
            &photo.recipe,
            binding,
            size,
            add(Mode::Heal, None),
        )
        .unwrap();
    assert_eq!(healed.repairs.points().len(), 1);
    assert!(healed.repairs.points()[0].sx != 0.5 || healed.repairs.points()[0].sy != 0.5);
    assert!(photo.recipe.repairs.is_none());
    assert_eq!(std::fs::read(&photo.path).unwrap(), original);
}
#[test]
fn donor_replacement_and_suggestion_do_not_move_the_target_or_change_other_settings() {
    let (_root, photo, engine, binding, size) = setup();
    let first = engine
        .plan_repair(
            &photo.path,
            &photo.recipe,
            binding,
            size,
            add(Mode::Heal, None),
        )
        .unwrap();
    let recipe = Recipe {
        repairs: Some(first.repairs),
        ..Default::default()
    };
    let old = recipe.repairs.as_ref().unwrap().points()[0];
    let manual = engine
        .plan_repair(
            &photo.path,
            &recipe,
            binding,
            size,
            Action::Donor {
                index: 0,
                point: Point { x: 0.8, y: 0.2 },
            },
        )
        .unwrap();
    let p = manual.repairs.points()[0];
    assert_eq!(
        (p.x, p.y, p.radius, p.feather, p.opacity),
        (old.x, old.y, old.radius, old.feather, old.opacity)
    );
    assert_eq!((p.sx, p.sy), (0.8, 0.2));
    let next = Recipe {
        repairs: Some(manual.repairs),
        ..Default::default()
    };
    let suggested = engine
        .plan_repair(
            &photo.path,
            &next,
            binding,
            size,
            Action::Suggest { index: 0 },
        )
        .unwrap();
    assert_eq!(suggested.repairs.points()[0], old);
    assert_eq!(recipe.repairs.as_ref().unwrap().points()[0], old);
    assert!(
        engine
            .plan_repair(
                &photo.path,
                &recipe,
                binding,
                size,
                Action::Suggest { index: 1 }
            )
            .is_err()
    );
}
#[test]
fn point_slider_gestures_have_separate_undo_steps_and_reject_bad_values_before_history_changes() {
    let (_root, photo, engine, binding, size) = setup();
    let planned = engine
        .plan_repair(
            &photo.path,
            &photo.recipe,
            binding,
            size,
            add(Mode::Heal, None),
        )
        .unwrap();
    let mut state = EditingSession::new(Session {
        photos: vec![photo],
        selected: Some(0),
        ..Default::default()
    });
    state.apply_repairs(Some(planned.repairs)).unwrap();
    state.adjust_repair(0, "radius", 0.04).unwrap();
    state.adjust_repair(0, "radius", 0.045).unwrap();
    state.adjust_repair(0, "opacity", 0.5).unwrap();
    let before = state.current();
    assert!(state.adjust_repair(0, "radius", f64::NAN).is_err());
    assert!(state.adjust_repair(99, "opacity", 1.).is_err());
    assert_eq!(state.current(), before);
    state.undo().unwrap();
    let p = state.current().unwrap().recipe.repairs.unwrap().points()[0];
    assert_eq!((p.radius, p.opacity), (0.045, 1.));
    state.undo().unwrap();
    assert_eq!(
        state.current().unwrap().recipe.repairs.unwrap().points()[0].radius,
        0.06
    );
    state.undo().unwrap();
    assert!(state.current().unwrap().recipe.repairs.is_none());
    state.redo().unwrap();
    assert!(state.current().unwrap().recipe.repairs.is_some());
}
#[test]
fn stale_display_dimensions_replaced_cache_or_changed_file_cannot_supply_donors() {
    let (root, photo, mut engine, binding, size) = setup();
    assert!(
        engine
            .plan_repair(
                &photo.path,
                &photo.recipe,
                binding,
                (255, 192),
                add(Mode::Heal, None)
            )
            .is_err()
    );
    let other = root.path().join("other.png");
    image::RgbImage::from_pixel(256, 192, image::Rgb([80, 60, 40]))
        .save(&other)
        .unwrap();
    engine.preview(&other, Recipe::default()).unwrap();
    assert!(
        engine
            .plan_repair(
                &photo.path,
                &photo.recipe,
                binding,
                size,
                add(Mode::Heal, None)
            )
            .is_err()
    );
    engine.preview(&photo.path, Recipe::default()).unwrap();
    use std::io::Write;
    std::fs::OpenOptions::new()
        .append(true)
        .open(&photo.path)
        .unwrap()
        .write_all(b"modified synthetic fixture")
        .unwrap();
    assert!(
        engine
            .plan_repair(
                &photo.path,
                &photo.recipe,
                binding,
                size,
                add(Mode::Heal, None)
            )
            .is_err()
    );
}
