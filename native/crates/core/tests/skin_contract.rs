use std::{fs, path::Path};
use z30_core::{
    batch::{Queue, RecipeMode, Store},
    editing::{EditRequest, EditingSession},
    editor::PreviewEngine,
    export::Format,
    export_job::{ExportControl, ExportSource},
    portrait::{Binding, Face, Point, Portrait},
    recipe::Recipe,
    session::{Photo, Session, SessionStore},
    skin::{SIDE, SkinMask},
};
fn face() -> Face {
    Face {
        rect: [0.1, 0.1, 0.8, 0.8],
        confidence: 1.,
        landmarks: [
            Point { x: 0.35, y: 0.35 },
            Point { x: 0.65, y: 0.35 },
            Point { x: 0.5, y: 0.5 },
            Point { x: 0.4, y: 0.7 },
            Point { x: 0.6, y: 0.7 },
        ],
    }
}
fn photo(root: &Path, index: usize, raster: bool) -> Photo {
    let path = root.join(format!("photo-{index}.png"));
    if raster {
        image::RgbImage::from_fn(256, 193, |x, y| {
            let v = (90. + 30. * (x as f32 * 0.25).sin()) as u8;
            image::Rgb([v, v.saturating_sub(15), 70 + ((x + y) % 19) as u8])
        })
        .save(&path)
        .unwrap();
    } else {
        fs::write(&path, b"x").unwrap();
    } // Capacity records only, not decode fixtures.
    let binding = Binding::capture(&path, 256, 193).unwrap();
    let mut recipe = Recipe {
        portrait: Portrait {
            face: Some(face()),
            binding: Some(binding),
            smooth: 70.,
        },
        ..Default::default()
    };
    recipe.skin = Some(
        SkinMask::new(
            binding,
            (0..SIDE * SIDE)
                .map(|i| {
                    if i % SIDE < 120 {
                        200 + ((i + index) % 56) as u8
                    } else {
                        0
                    }
                })
                .collect(),
        )
        .unwrap(),
    );
    Photo {
        path: path.canonicalize().unwrap(),
        rating: 0,
        flag: 0,
        recipe,
    }
}

#[test]
fn portable_saved_mask_renders_without_model_and_matches_real_tiff16() {
    let root = tempfile::tempdir().unwrap();
    let photo = photo(root.path(), 0, true);
    let original = fs::read(&photo.path).unwrap();
    let store = SessionStore::new(root.path().join("data")).unwrap();
    let session = Session {
        photos: vec![photo.clone()],
        selected: Some(0),
        ..Default::default()
    };
    store.save(&session).unwrap();
    let loaded = store.load().unwrap();
    assert_eq!(loaded.photos[0], photo);
    let cloned = loaded.photos[0].recipe.clone();
    assert_eq!(
        cloned.skin.as_ref().unwrap().weights().as_ptr(),
        loaded.photos[0]
            .recipe
            .skin
            .as_ref()
            .unwrap()
            .weights()
            .as_ptr()
    );
    let mut preview = PreviewEngine::new(None).unwrap();
    let shown = preview.preview(&photo.path, cloned.clone()).unwrap();
    let linear = z30_core::raster::decode(&photo.path).unwrap();
    let fallback = z30_core::rendering::render_with_skin(
        &linear.frame,
        &cloned,
        &photo.path,
        (256, 193),
        None,
    )
    .unwrap();
    assert_eq!(fallback.pixels, shown.rendered.pixels);
    let wrong = SkinMask::new(cloned.portrait.binding.unwrap(), vec![255; SIDE * SIDE]).unwrap();
    assert!(
        z30_core::rendering::render_with_skin(
            &linear.frame,
            &cloned,
            &photo.path,
            (256, 193),
            Some(&wrong)
        )
        .is_err()
    );
    let destination = root.path().join("semantic.tif");
    ExportSource::new(EditRequest {
        path: photo.path.clone(),
        recipe: cloned,
    })
    .unwrap()
    .run(
        &destination,
        Format::Tiff16,
        None,
        &ExportControl::default(),
    )
    .unwrap();
    let output = image::open(&destination).unwrap().to_rgb16();
    for (a, b) in output.pixels().zip(&shown.rendered.pixels) {
        for c in 0..3 {
            assert_eq!(a[c], (b[c] * 65535.).round_ties_even() as u16);
        }
    }
    let mut geometric = photo.recipe.clone();
    geometric.skin = None;
    assert_ne!(
        shown.rendered.pixels,
        preview
            .preview(&photo.path, geometric)
            .unwrap()
            .rendered
            .pixels
    );
    assert_eq!(fs::read(&photo.path).unwrap(), original);
    // Moving the complete JSON to another profile does not lose a side cache.
    let moved = SessionStore::new(root.path().join("moved-profile")).unwrap();
    moved.save(&loaded).unwrap();
    assert_eq!(moved.load().unwrap(), loaded);
}

#[test]
fn skin_is_one_undo_step_and_global_batch_sync_keeps_each_target_mask() {
    let root = tempfile::tempdir().unwrap();
    let first = photo(root.path(), 0, true);
    let second = photo(root.path(), 1, true);
    let mut geometric = first.clone();
    let mask = geometric.recipe.skin.take().unwrap();
    let mut edits = EditingSession::new(Session {
        photos: vec![geometric, second.clone()],
        selected: Some(0),
        ..Default::default()
    });
    assert!(edits.apply_skin(mask.clone()).unwrap());
    assert!(edits.undo().unwrap());
    assert!(edits.current().unwrap().recipe.skin.is_none());
    edits.redo().unwrap();
    assert_eq!(edits.current().unwrap().recipe.skin, Some(mask.clone()));
    edits.adjust("exposure", 0.5).unwrap();
    edits.commit().unwrap();
    edits.undo().unwrap();
    assert_eq!(edits.current().unwrap().recipe.skin, Some(mask));
    let mut advice = edits.current().unwrap().recipe.parameters;
    advice.exposure = 0.25;
    edits.apply_ai(advice).unwrap();
    assert_eq!(edits.current().unwrap().recipe.skin, first.recipe.skin);
    edits.undo().unwrap();
    edits.select(1).unwrap();
    assert_eq!(edits.current().unwrap().recipe.skin, second.recipe.skin);
    assert!(
        edits
            .apply_skin(first.recipe.skin.clone().unwrap())
            .is_err()
    );
    let mut shared = first.recipe.clone();
    shared.parameters.exposure = 0.75;
    let mut queue = Queue::default();
    queue
        .enqueue(
            &[first.clone(), second.clone()],
            root.path(),
            Format::Tiff16,
            RecipeMode::Shared(shared),
        )
        .unwrap();
    assert_eq!(queue.jobs[0].recipe.skin, first.recipe.skin);
    assert_eq!(queue.jobs[1].recipe.skin, second.recipe.skin);
    assert_eq!(queue.jobs[1].recipe.parameters.exposure, 0.75);
    let store = Store::new(root.path().join("batch")).unwrap();
    store.save(&queue).unwrap();
    let restored = store.load().unwrap();
    assert_eq!(restored.jobs[1].recipe.skin, second.recipe.skin);
    edits.reset().unwrap();
    assert!(edits.current().unwrap().recipe.skin.is_none());
    edits.undo().unwrap();
    assert_eq!(edits.current().unwrap().recipe.skin, second.recipe.skin);
}

#[test]
fn corrupt_mask_and_legacy_disguise_never_overwrite_valid_records() {
    let root = tempfile::tempdir().unwrap();
    let photo = photo(root.path(), 0, false);
    let session = Session {
        photos: vec![photo],
        selected: Some(0),
        ..Default::default()
    };
    let original = serde_json::to_vec(&session).unwrap();
    for mutation in 0..4 {
        let data = root.path().join(format!("data-{mutation}"));
        let store = SessionStore::new(data.clone()).unwrap();
        let mut value: serde_json::Value = serde_json::from_slice(&original).unwrap();
        match mutation {
            0 => value["photos"][0]["recipe"]["skin"]["sha256"] = "0".repeat(64).into(),
            1 => value["photos"][0]["recipe"]["skin"]["weights"] = "short".into(),
            2 => value["photos"][0]["recipe"]["skin"]["version"] = 2.into(),
            _ => value["version"] = 3.into(),
        }
        let bad = serde_json::to_vec(&value).unwrap();
        fs::write(data.join("session.json"), &bad).unwrap();
        assert!(store.load().is_err());
        assert!(store.save(&Session::default()).is_err());
        assert_eq!(fs::read(data.join("session.json")).unwrap(), bad);
    }
    let mut value = serde_json::to_value(&session).unwrap();
    value["photos"][0]["recipe"]["skin"]["binding"]["width"] = 300.into();
    assert!(serde_json::from_value::<Session>(value).is_err()); // checksum binds dimensions too.
}

#[test]
fn v3_geometric_session_and_v1_queue_keep_exact_backup_before_upgrade() {
    let root = tempfile::tempdir().unwrap();
    let mut photo = photo(root.path(), 0, true);
    photo.recipe.skin = None;
    let session = Session {
        version: 3,
        photos: vec![photo.clone()],
        selected: Some(0),
    };
    let directory = root.path().join("data");
    let store = SessionStore::new(directory.clone()).unwrap();
    let old = format!("  {}\n", serde_json::to_string_pretty(&session).unwrap()).into_bytes();
    fs::write(directory.join("session.json"), &old).unwrap();
    let loaded = store.load().unwrap();
    assert_eq!(loaded.version, 7);
    assert_eq!(loaded.photos[0].recipe, photo.recipe);
    assert_eq!(fs::read(directory.join("session.json")).unwrap(), old);
    store.save(&loaded).unwrap();
    let backup = fs::read_dir(&directory)
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| {
            p.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("session-v3-backup-")
        })
        .unwrap();
    assert_eq!(fs::read(backup).unwrap(), old);
    let batch_dir = root.path().join("batch");
    let batch_store = Store::new(batch_dir.clone()).unwrap();
    let mut queue = Queue::default();
    queue
        .enqueue(&[photo], root.path(), Format::Tiff16, RecipeMode::PerPhoto)
        .unwrap();
    queue.version = 1;
    let old_queue = serde_json::to_vec_pretty(&queue).unwrap();
    fs::write(batch_dir.join("batch-jobs.json"), &old_queue).unwrap();
    let loaded = batch_store.load().unwrap();
    assert_eq!(loaded.version, 5);
    batch_store.save(&loaded).unwrap();
    let backup = fs::read_dir(&batch_dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| {
            p.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("batch-v1-backup-")
        })
        .unwrap();
    assert_eq!(fs::read(backup).unwrap(), old_queue);
}

#[test]
fn full_256_photo_project_and_512_job_queue_fit_the_persistence_budgets() {
    let root = tempfile::tempdir().unwrap();
    let photos: Vec<_> = (0..256).map(|i| photo(root.path(), i, false)).collect();
    let session = Session {
        photos: photos.clone(),
        selected: Some(255),
        ..Default::default()
    };
    let directory = root.path().join("data");
    let store = SessionStore::new(directory.clone()).unwrap();
    store.save(&session).unwrap();
    let bytes = fs::metadata(directory.join("session.json")).unwrap().len();
    assert!(bytes > 2 * 1024 * 1024 && bytes < 32 * 1024 * 1024);
    assert_eq!(store.load().unwrap(), session);
    let mut doubled = photos.clone();
    doubled.extend(photos);
    let mut queue = Queue::default();
    queue
        .enqueue(&doubled, root.path(), Format::Tiff16, RecipeMode::PerPhoto)
        .unwrap();
    let directory = root.path().join("batch");
    let store = Store::new(directory.clone()).unwrap();
    store.save(&queue).unwrap();
    assert_eq!(store.load().unwrap().jobs.len(), 512);
    assert!(
        fs::metadata(directory.join("batch-jobs.json"))
            .unwrap()
            .len()
            < 64 * 1024 * 1024
    );
}

#[test]
fn array_counts_are_rejected_during_parsing_even_if_extra_record_is_invalid() {
    let template = serde_json::json!({"path":std::env::temp_dir().join("photo.png"),"recipe":{}});
    let mut photos = vec![template; 256];
    photos.push(serde_json::Value::Null);
    let value = serde_json::json!({"version":4,"photos":photos,"selected":0});
    let error = serde_json::from_value::<Session>(value)
        .unwrap_err()
        .to_string();
    assert!(error.contains("记录数量"), "{error}");
}

#[test]
fn lossless_runs_are_small_for_flat_masks_and_reject_expansion_or_truncation() {
    use base64::{Engine, engine::general_purpose::STANDARD};
    let root = tempfile::tempdir().unwrap();
    let photo = photo(root.path(), 0, false);
    let binding = photo.recipe.portrait.binding.unwrap();
    let mask = SkinMask::new(binding, vec![255; SIDE * SIDE]).unwrap();
    let wire = serde_json::to_value(&mask).unwrap();
    assert_eq!(wire["encoding"], "rle8");
    assert!(wire["weights"].as_str().unwrap().len() < 16);
    assert_eq!(
        serde_json::from_value::<SkinMask>(wire.clone()).unwrap(),
        mask
    );
    for bad in [
        vec![0, 0, 0],
        vec![1, 0],
        vec![255, 255, 1, 255, 255, 1],
        vec![1, 0, 1],
    ] {
        let mut value = wire.clone();
        value["weights"] = STANDARD.encode(bad).into();
        assert!(serde_json::from_value::<SkinMask>(value).is_err());
    }
    let mut value = wire.clone();
    value["encoding"] = "unknown".into();
    assert!(serde_json::from_value::<SkinMask>(value).is_err());
    let mut raw = wire;
    raw.as_object_mut().unwrap().remove("encoding");
    raw["weights"] = STANDARD.encode(vec![255; SIDE * SIDE]).into();
    assert_eq!(serde_json::from_value::<SkinMask>(raw).unwrap(), mask);
}

#[test]
fn tone_controls_require_skin_and_keep_source_guard_history_and_tiff16() {
    let root = tempfile::tempdir().unwrap();
    let original = photo(root.path(), 0, true);
    let mut edits = EditingSession::new(Session {
        photos: vec![original.clone()],
        selected: Some(0),
        ..Default::default()
    });
    edits.adjust("skin_uniformity", 80.).unwrap();
    edits.adjust("skin_lighten", 60.).unwrap();
    edits.commit().unwrap();
    let recipe = edits.current().unwrap().recipe;
    assert_eq!(recipe.skin_tone.uniformity, 80.);
    assert_eq!(recipe.skin, original.recipe.skin);
    edits.undo().unwrap();
    assert_eq!(edits.current().unwrap().recipe.skin_tone.uniformity, 80.);
    assert_eq!(edits.current().unwrap().recipe.skin_tone.lighten, 0.);
    edits.undo().unwrap();
    assert!(!edits.current().unwrap().recipe.skin_tone.active());
    edits.redo().unwrap();
    edits.redo().unwrap();
    let mut preview = PreviewEngine::new(None).unwrap();
    let shown = preview.preview(&original.path, recipe.clone()).unwrap();
    let destination = root.path().join("tone.tif");
    ExportSource::new(EditRequest {
        path: original.path.clone(),
        recipe: recipe.clone(),
    })
    .unwrap()
    .run(
        &destination,
        Format::Tiff16,
        None,
        &ExportControl::default(),
    )
    .unwrap();
    for (a, b) in image::open(&destination)
        .unwrap()
        .to_rgb16()
        .pixels()
        .zip(&shown.rendered.pixels)
    {
        for c in 0..3 {
            assert_eq!(a[c], (b[c] * 65535.).round_ties_even() as u16);
        }
    }
    let store = SessionStore::new(root.path().join("data")).unwrap();
    store.save(&edits.session).unwrap();
    assert_eq!(store.load().unwrap().photos[0].recipe, recipe);
    let mut invalid = recipe.clone();
    invalid.skin = None;
    assert!(invalid.validate().is_err());
    edits.reset().unwrap();
    assert!(edits.adjust("skin_uniformity", 50.).is_err());
    edits.undo().unwrap();
    let mut only_tone = recipe;
    only_tone.portrait.smooth = 0.;
    fs::write(&original.path, b"changed source").unwrap();
    assert!(preview.preview(&original.path, only_tone).is_err());
}

#[test]
fn v4_masks_migrate_without_new_tone_edits_and_preserve_original_backups() {
    let root = tempfile::tempdir().unwrap();
    let original = photo(root.path(), 0, true);
    let old = Session {
        version: 4,
        photos: vec![original.clone()],
        selected: Some(0),
    };
    let directory = root.path().join("data");
    let store = SessionStore::new(directory.clone()).unwrap();
    let bytes = serde_json::to_vec_pretty(&old).unwrap();
    fs::write(directory.join("session.json"), &bytes).unwrap();
    let restored = store.load().unwrap();
    assert_eq!(restored.version, 7);
    assert_eq!(restored.photos[0].recipe, original.recipe);
    store.save(&restored).unwrap();
    let backup = fs::read_dir(&directory)
        .unwrap()
        .map(|p| p.unwrap().path())
        .find(|p| {
            p.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("session-v4-backup-")
        })
        .unwrap();
    assert_eq!(fs::read(backup).unwrap(), bytes);
    let mut forged = old;
    forged.photos[0].recipe.skin_tone.lighten = 1.;
    assert!(forged.validate().is_err());
    let mut queue = Queue::default();
    queue
        .enqueue(
            &[original.clone()],
            root.path(),
            Format::Tiff16,
            RecipeMode::PerPhoto,
        )
        .unwrap();
    queue.version = 2;
    let directory = root.path().join("batch");
    let store = Store::new(directory.clone()).unwrap();
    let bytes = serde_json::to_vec_pretty(&queue).unwrap();
    fs::write(directory.join("batch-jobs.json"), &bytes).unwrap();
    let loaded = store.load().unwrap();
    assert_eq!(loaded.version, 5);
    store.save(&loaded).unwrap();
    let backup = fs::read_dir(&directory)
        .unwrap()
        .map(|p| p.unwrap().path())
        .find(|p| {
            p.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("batch-v2-backup-")
        })
        .unwrap();
    assert_eq!(fs::read(backup).unwrap(), bytes);
    let mut forged = queue;
    forged.jobs[0].recipe.skin_tone.lighten = 1.;
    assert!(forged.validate().is_err());
}
