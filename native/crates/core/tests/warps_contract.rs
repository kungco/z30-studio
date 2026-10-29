use std::fs;
use z30_core::{
    batch::{Queue, RecipeMode, Store},
    develop::Parameters,
    editing::{EditRequest, EditingSession},
    editor::PreviewEngine,
    export::Format,
    export_job::{ExportControl, ExportSource},
    portrait::{Binding, Face, Point, Portrait},
    recipe::Recipe,
    rendering,
    session::{Photo, Session, SessionStore},
    warp::{self, Pin},
    warps::Warps,
};
fn setup() -> (tempfile::TempDir, Photo, Warps) {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("source.png");
    image::RgbImage::from_fn(96, 64, |x, y| {
        image::Rgb([
            40 + (x * 2) as u8,
            30 + (y * 3) as u8,
            70 + ((x + y) % 31) as u8,
        ])
    })
    .save(&path)
    .unwrap();
    let path = path.canonicalize().unwrap();
    let warp = Warps::new(
        Binding::capture(&path, 96, 64).unwrap(),
        vec![Pin {
            from: [0.5 - 1. / 95., 0.5],
            to: [0.5, 0.5],
            radius: 0.2,
        }],
    )
    .unwrap();
    (
        root,
        Photo {
            path,
            rating: 0,
            flag: 0,
            recipe: Recipe::default(),
        },
        warp,
    )
}
fn face() -> Face {
    Face {
        rect: [0.1, 0.1, 0.8, 0.8],
        landmarks: [
            Point { x: 0.35, y: 0.35 },
            Point { x: 0.65, y: 0.35 },
            Point { x: 0.5, y: 0.5 },
            Point { x: 0.4, y: 0.7 },
            Point { x: 0.6, y: 0.7 },
        ],
        confidence: 1.,
    }
}
#[test]
fn bound_immutable_warps_roundtrip_strictly_and_share_history_data() {
    let (_root, _photo, warp) = setup();
    let cloned = warp.clone();
    assert_eq!(warp.pins().as_ptr(), cloned.pins().as_ptr());
    let value = serde_json::to_value(&warp).unwrap();
    let restored: Warps = serde_json::from_value(value.clone()).unwrap();
    assert_eq!(restored, warp);
    let mut bad = value.clone();
    bad["version"] = 2.into();
    assert!(serde_json::from_value::<Warps>(bad).is_err());
    let mut bad = value.clone();
    bad["unexpected"] = true.into();
    assert!(serde_json::from_value::<Warps>(bad).is_err());
    let mut bad = value;
    bad["pins"] = serde_json::to_value(vec![warp.pins()[0]; 5]).unwrap();
    assert!(serde_json::from_value::<Warps>(bad).is_err());
    assert!(Warps::new(warp.binding(), vec![]).is_err());
    assert!(
        serde_json::to_value(Recipe::default())
            .unwrap()
            .get("warp")
            .is_none()
    );
}
#[test]
fn complete_source_space_portrait_result_is_warped_in_linear_light_last() {
    let (_root, mut photo, warp) = setup();
    let source = z30_core::raster::decode(&photo.path).unwrap().frame;
    photo.recipe.parameters.exposure = 0.3;
    photo.recipe.parameters.contrast = 25.;
    photo.recipe.portrait = Portrait {
        face: Some(face()),
        binding: Some(warp.binding()),
        smooth: 60.,
    };
    let original = rendering::render(&source, &photo.recipe, &photo.path, (96, 64)).unwrap();
    let expected = warp::render_srgb(&original, warp.pins()).unwrap().unwrap();
    photo.recipe.warp = Some(warp);
    let got = rendering::render(&source, &photo.recipe, &photo.path, (96, 64)).unwrap();
    assert_eq!(got.pixels, expected.pixels);
    assert_ne!(got.pixels, original.pixels);
    let mut engine = PreviewEngine::new(None).unwrap();
    let preview = engine.preview(&photo.path, photo.recipe.clone()).unwrap();
    assert_eq!(preview.rendered.pixels, got.pixels);
    assert!(
        rendering::render_with_cancel(&source, &photo.recipe, &photo.path, (96, 64), || true)
            .is_err()
    );
}
#[test]
fn warp_edits_are_undoable_ai_preserves_geometry_and_reset_can_be_undone() {
    let (_root, photo, warp) = setup();
    let mut edits = EditingSession::new(Session {
        photos: vec![photo],
        selected: Some(0),
        ..Default::default()
    });
    assert!(edits.apply_warp(Some(warp.clone())).unwrap());
    assert!(!edits.apply_warp(Some(warp.clone())).unwrap());
    edits.adjust("exposure", 0.3).unwrap();
    edits.commit().unwrap();
    assert_eq!(edits.current().unwrap().recipe.warp, Some(warp.clone()));
    edits
        .apply_ai(Parameters {
            exposure: 0.7,
            ..Default::default()
        })
        .unwrap();
    assert_eq!(edits.current().unwrap().recipe.warp, Some(warp.clone()));
    edits.reset().unwrap();
    assert!(edits.current().unwrap().recipe.warp.is_none());
    edits.undo().unwrap();
    assert_eq!(edits.current().unwrap().recipe.warp, Some(warp));
    assert_eq!(edits.current().unwrap().recipe.parameters.exposure, 0.7);
}
#[test]
fn shared_batch_keeps_each_photo_geometry_and_freezes_the_snapshot() {
    let (root, mut photo, warp) = setup();
    let path = root.path().join("other.png");
    fs::copy(&photo.path, &path).unwrap();
    let other = Photo {
        path: path.canonicalize().unwrap(),
        rating: 0,
        flag: 0,
        recipe: Recipe::default(),
    };
    photo.recipe.warp = Some(warp.clone());
    let mut shared = photo.recipe.clone();
    shared.parameters.exposure = 0.5;
    let mut queue = Queue::default();
    queue
        .enqueue(
            &[photo.clone(), other],
            root.path(),
            Format::Tiff16,
            RecipeMode::Shared(shared),
        )
        .unwrap();
    assert_eq!(queue.jobs[0].recipe.warp, Some(warp));
    assert!(queue.jobs[1].recipe.warp.is_none());
    photo.recipe.warp = None;
    assert!(queue.jobs[0].recipe.warp.is_some());
    assert_eq!(queue.jobs[0].recipe.parameters.exposure, 0.5);
    assert_eq!(queue.jobs[1].recipe.parameters.exposure, 0.5);
}
#[test]
fn old_v6_and_v4_migrate_with_exact_backups_and_reject_new_geometry() {
    let (root, photo, warp) = setup();
    let data = root.path().join("data");
    fs::create_dir(&data).unwrap();
    let legacy = Session {
        version: 6,
        photos: vec![photo.clone()],
        selected: Some(0),
    };
    let bytes = serde_json::to_vec_pretty(&legacy).unwrap();
    fs::write(data.join("session.json"), &bytes).unwrap();
    let store = SessionStore::new(data.clone()).unwrap();
    let loaded = store.load().unwrap();
    assert_eq!(loaded.version, 7);
    assert_eq!(fs::read(data.join("session.json")).unwrap(), bytes);
    store.save(&loaded).unwrap();
    assert!(fs::read_dir(&data).unwrap().flatten().any(|e| {
        e.file_name()
            .to_string_lossy()
            .starts_with("session-v6-backup-")
            && fs::read(e.path()).unwrap() == bytes
    }));
    let mut bad = legacy;
    bad.photos[0].recipe.warp = Some(warp.clone());
    assert!(bad.validate().is_err());
    let qdir = root.path().join("queue");
    fs::create_dir(&qdir).unwrap();
    let mut queue = Queue::default();
    queue
        .enqueue(&[photo], root.path(), Format::Tiff16, RecipeMode::PerPhoto)
        .unwrap();
    queue.version = 4;
    let bytes = serde_json::to_vec_pretty(&queue).unwrap();
    fs::write(qdir.join("batch-jobs.json"), &bytes).unwrap();
    let store = Store::new(qdir.clone()).unwrap();
    let loaded = store.load().unwrap();
    assert_eq!(loaded.version, 5);
    store.save(&loaded).unwrap();
    assert!(fs::read_dir(&qdir).unwrap().flatten().any(|e| {
        e.file_name()
            .to_string_lossy()
            .starts_with("batch-v4-backup-")
            && fs::read(e.path()).unwrap() == bytes
    }));
    queue.jobs[0].recipe.warp = Some(warp);
    assert!(queue.validate().is_err());
}
#[test]
fn source_replacement_and_dimension_mismatch_cannot_apply_or_export() {
    let (root, photo, warp) = setup();
    let mut recipe = photo.recipe.clone();
    recipe.warp = Some(warp.clone());
    let source = z30_core::raster::decode(&photo.path).unwrap().frame;
    assert!(rendering::render(&source, &recipe, &photo.path, (95, 64)).is_err());
    let path = root.path().join("other.png");
    fs::copy(&photo.path, &path).unwrap();
    assert!(rendering::render(&source, &recipe, &path, (96, 64)).is_err());
    let mut edits = EditingSession::new(Session {
        photos: vec![photo.clone()],
        selected: Some(0),
        ..Default::default()
    });
    let before = edits.current();
    image::RgbImage::from_pixel(96, 64, image::Rgb([9, 8, 7]))
        .save(&photo.path)
        .unwrap();
    assert!(edits.apply_warp(Some(warp)).is_err());
    assert_eq!(edits.current(), before);
    let target = root.path().join("rejected.tif");
    let result = ExportSource::new(EditRequest {
        path: photo.path,
        recipe,
    })
    .and_then(|s| s.run(&target, Format::Tiff16, None, &ExportControl::default()));
    assert!(result.is_err());
    assert!(!target.exists());
}

#[test]
fn strength_shares_base_controls_disables_exactly_and_has_its_own_undo_gesture() {
    let (_root, photo, warp) = setup();
    let half = warp.with_amount(0.5).unwrap();
    assert_eq!(half.pins().as_ptr(), warp.pins().as_ptr());
    assert_eq!(half.amount(), 0.5);
    assert!(warp.with_amount(f64::NAN).is_err());
    assert!(warp.with_amount(1.01).is_err());
    let zero = warp.with_amount(0.).unwrap();
    assert!(!zero.active());
    let source = z30_core::raster::decode(&photo.path).unwrap().frame;
    let baseline = rendering::render(&source, &photo.recipe, &photo.path, (96, 64)).unwrap();
    let mut recipe = photo.recipe.clone();
    recipe.warp = Some(zero.clone());
    assert_eq!(
        rendering::render(&source, &recipe, &photo.path, (96, 64))
            .unwrap()
            .pixels,
        baseline.pixels
    );
    let mut old = serde_json::to_value(&warp).unwrap();
    old.as_object_mut().unwrap().remove("amount");
    assert_eq!(serde_json::from_value::<Warps>(old).unwrap(), warp);
    let mut edits = EditingSession::new(Session {
        photos: vec![photo.clone()],
        selected: Some(0),
        ..Default::default()
    });
    edits.apply_warp(Some(warp.clone())).unwrap();
    edits.adjust_warp_amount(0.4).unwrap();
    edits.adjust_warp_amount(0.5).unwrap();
    edits.adjust("exposure", 0.3).unwrap();
    edits.commit().unwrap();
    edits.undo().unwrap();
    assert_eq!(edits.current().unwrap().recipe.warp, Some(half));
    edits.undo().unwrap();
    assert_eq!(edits.current().unwrap().recipe.warp, Some(warp));
    let old = Session {
        version: 6,
        photos: vec![Photo {
            recipe: Recipe {
                warp: Some(zero),
                ..Default::default()
            },
            ..photo
        }],
        selected: Some(0),
    };
    assert!(old.validate().is_err());
}
