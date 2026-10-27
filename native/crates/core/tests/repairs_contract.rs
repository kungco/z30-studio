use std::{fs, io::Write};
use z30_core::{
    batch::{Queue, RecipeMode, Store},
    editing::{EditRequest, EditingSession},
    editor::PreviewEngine,
    export::Format,
    export_job::{ExportControl, ExportSource},
    portrait::Binding,
    recipe::Recipe,
    repairs::Repairs,
    retouch::Repair,
    session::{Photo, Session, SessionStore},
};
fn photo(root: &std::path::Path, name: &str) -> Photo {
    let path = root.join(format!("{name}.png"));
    image::RgbImage::from_fn(256, 192, |x, y| {
        if (120..136).contains(&x) && (90..104).contains(&y) {
            image::Rgb([12, 7, 4])
        } else {
            image::Rgb([
                80 + (x / 3) as u8,
                60 + (y / 4) as u8,
                70 + ((x + y) % 19) as u8,
            ])
        }
    })
    .save(&path)
    .unwrap();
    Photo {
        path: path.canonicalize().unwrap(),
        rating: 0,
        flag: 0,
        recipe: Recipe::default(),
    }
}
fn repairs(photo: &Photo, sx: f64) -> Repairs {
    Repairs::new(
        Binding::capture(&photo.path, 256, 192).unwrap(),
        vec![Repair {
            sx,
            sy: 0.5,
            radius: 0.06,
            ..Default::default()
        }],
    )
    .unwrap()
}
#[test]
fn repaired_linear_pixels_flow_through_preview_real_tiff_and_recovery_without_a_model() {
    let root = tempfile::tempdir().unwrap();
    let mut photo = photo(root.path(), "source");
    let bytes = fs::read(&photo.path).unwrap();
    let set = repairs(&photo, 0.3);
    photo.recipe.repairs = Some(set.clone());
    let source = z30_core::raster::decode(&photo.path).unwrap().frame;
    let repaired = z30_core::retouch::render(&source, set.points())
        .unwrap()
        .unwrap();
    let expected = z30_core::develop::render(&repaired, &photo.recipe.parameters).unwrap();
    let mut engine = PreviewEngine::new(None).unwrap();
    let actual = engine.preview(&photo.path, photo.recipe.clone()).unwrap();
    assert_eq!(actual.rendered.pixels, expected.pixels);
    assert_ne!(
        actual.rendered.pixels,
        z30_core::develop::render(&source, &photo.recipe.parameters)
            .unwrap()
            .pixels
    );
    let store = SessionStore::new(root.path().join("data")).unwrap();
    let session = Session {
        photos: vec![photo.clone()],
        selected: Some(0),
        ..Default::default()
    };
    store.save(&session).unwrap();
    let loaded = store.load().unwrap();
    assert_eq!(loaded.photos[0].recipe, photo.recipe);
    let destination = root.path().join("repaired.tif");
    let job = ExportSource::new(EditRequest {
        path: photo.path.clone(),
        recipe: loaded.photos[0].recipe.clone(),
    })
    .unwrap();
    job.run(
        &destination,
        Format::Tiff16,
        None,
        &ExportControl::default(),
    )
    .unwrap();
    let mut decoder = tiff::decoder::Decoder::new(fs::File::open(&destination).unwrap()).unwrap();
    let tiff::decoder::DecodingResult::U16(pixels) = decoder.read_image().unwrap() else {
        panic!("not RGB16")
    };
    let expected: Vec<u16> = expected
        .pixels
        .iter()
        .flatten()
        .map(|v| (v.clamp(0., 1.) * 65535.).round_ties_even() as u16)
        .collect();
    assert_eq!(pixels, expected);
    assert!(pixels.iter().any(|v| v % 257 != 0));
    assert!(
        job.run(
            &destination,
            Format::Tiff16,
            None,
            &ExportControl::default()
        )
        .is_err()
    );
    assert_eq!(fs::read(&photo.path).unwrap(), bytes);
}
#[test]
fn undo_ai_shared_batch_and_frozen_jobs_keep_individual_immutable_repair_lists() {
    let root = tempfile::tempdir().unwrap();
    let first = photo(root.path(), "first");
    let mut second = photo(root.path(), "second");
    let a = repairs(&first, 0.3);
    let b = repairs(&second, 0.7);
    second.recipe.repairs = Some(b.clone());
    let mut state = EditingSession::new(Session {
        photos: vec![first.clone(), second.clone()],
        selected: Some(0),
        ..Default::default()
    });
    state.apply_repairs(Some(a.clone())).unwrap();
    assert_eq!(
        state
            .current()
            .unwrap()
            .recipe
            .repairs
            .as_ref()
            .unwrap()
            .points()
            .as_ptr(),
        a.points().as_ptr()
    );
    state.undo().unwrap();
    assert!(state.current().unwrap().recipe.repairs.is_none());
    state.redo().unwrap();
    let mut p = state.current().unwrap().recipe.parameters;
    p.exposure = 0.3;
    state.apply_ai(p).unwrap();
    assert_eq!(state.current().unwrap().recipe.repairs, Some(a.clone()));
    let mut queue = Queue::default();
    queue
        .enqueue(
            &state.session.photos,
            root.path(),
            Format::Tiff16,
            RecipeMode::Shared(state.current().unwrap().recipe),
        )
        .unwrap();
    assert_eq!(queue.jobs[0].recipe.repairs, Some(a.clone()));
    assert_eq!(queue.jobs[1].recipe.repairs, Some(b));
    state.reset().unwrap();
    assert!(state.current().unwrap().recipe.repairs.is_none());
    state.undo().unwrap();
    assert_eq!(state.current().unwrap().recipe.repairs, Some(a.clone()));
    state.apply_repairs(None).unwrap();
    assert_eq!(queue.jobs[0].recipe.repairs, Some(a));
    state.select(1).unwrap();
    let old = state.current();
    assert!(
        state
            .apply_repairs(queue.jobs[0].recipe.repairs.clone())
            .is_err()
    );
    assert_eq!(state.current(), old);
    let store = Store::new(root.path().join("queue")).unwrap();
    store.save(&queue).unwrap();
    assert_eq!(store.load().unwrap().jobs[0].recipe, queue.jobs[0].recipe);
}
#[test]
fn source_dimension_cancel_and_malformed_sets_fail_without_applying_or_dropping_the_list() {
    let root = tempfile::tempdir().unwrap();
    let mut photo = photo(root.path(), "source");
    let set = repairs(&photo, 0.3);
    let cloned = set.clone();
    assert_eq!(cloned.points().as_ptr(), set.points().as_ptr());
    let encoded = serde_json::to_value(&set).unwrap();
    for bad in [
        serde_json::json!([]),
        serde_json::json!([{}]),
        serde_json::json!(vec![serde_json::to_value(Repair::default()).unwrap(); 257]),
    ] {
        let mut value = encoded.clone();
        value["points"] = bad;
        assert!(serde_json::from_value::<Repairs>(value).is_err());
    }
    let mut bad = encoded.clone();
    bad["version"] = 2.into();
    assert!(serde_json::from_value::<Repairs>(bad).is_err());
    photo.recipe.repairs = Some(set);
    let decoded = z30_core::raster::decode(&photo.path).unwrap();
    let original = decoded.frame.pixels.clone();
    assert!(
        z30_core::rendering::render(&decoded.frame, &photo.recipe, &photo.path, (255, 192))
            .is_err()
    );
    let mut calls = 0;
    assert!(
        z30_core::rendering::render_with_cancel(
            &decoded.frame,
            &photo.recipe,
            &photo.path,
            (256, 192),
            || {
                calls += 1;
                calls >= 3
            }
        )
        .is_err()
    );
    assert_eq!(decoded.frame.pixels, original);
    fs::OpenOptions::new()
        .append(true)
        .open(&photo.path)
        .unwrap()
        .write_all(b"changed fixture")
        .unwrap();
    assert!(
        z30_core::rendering::render(&decoded.frame, &photo.recipe, &photo.path, (256, 192))
            .is_err()
    );
}
#[test]
fn v5_and_v3_exact_backups_and_old_format_new_data_rejection_are_real() {
    let root = tempfile::tempdir().unwrap();
    let mut photo = photo(root.path(), "source");
    let old = Session {
        version: 5,
        photos: vec![photo.clone()],
        selected: Some(0),
    };
    let data = root.path().join("data");
    fs::create_dir(&data).unwrap();
    let bytes = serde_json::to_vec_pretty(&old).unwrap();
    fs::write(data.join("session.json"), &bytes).unwrap();
    let store = SessionStore::new(data.clone()).unwrap();
    let loaded = store.load().unwrap();
    assert_eq!(loaded.version, 6);
    store.save(&loaded).unwrap();
    assert!(fs::read_dir(&data).unwrap().flatten().any(|e| {
        e.file_name()
            .to_string_lossy()
            .starts_with("session-v5-backup-")
            && fs::read(e.path()).unwrap() == bytes
    }));
    let mut queue = Queue::default();
    queue
        .enqueue(
            &[photo.clone()],
            root.path(),
            Format::Tiff16,
            RecipeMode::PerPhoto,
        )
        .unwrap();
    let qdir = root.path().join("q");
    fs::create_dir(&qdir).unwrap();
    queue.version = 3;
    let bytes = serde_json::to_vec_pretty(&queue).unwrap();
    fs::write(qdir.join("batch-jobs.json"), &bytes).unwrap();
    let store = Store::new(qdir.clone()).unwrap();
    let migrated = store.load().unwrap();
    assert_eq!(migrated.version, 4);
    store.save(&migrated).unwrap();
    assert!(fs::read_dir(&qdir).unwrap().flatten().any(|e| {
        e.file_name()
            .to_string_lossy()
            .starts_with("batch-v3-backup-")
            && fs::read(e.path()).unwrap() == bytes
    }));
    photo.recipe.repairs = Some(repairs(&photo, 0.3));
    assert!(
        Session {
            version: 5,
            photos: vec![photo.clone()],
            selected: Some(0)
        }
        .validate()
        .is_err()
    );
    queue.jobs[0].recipe = photo.recipe;
    assert!(queue.validate().is_err());
    assert!(
        serde_json::to_value(Recipe::default())
            .unwrap()
            .get("repairs")
            .is_none()
    );
}
