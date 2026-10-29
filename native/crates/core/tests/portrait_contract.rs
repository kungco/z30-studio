use std::fs;
use z30_core::{
    batch::{Queue, RecipeMode},
    editing::{EditRequest, EditingSession},
    editor::PreviewEngine,
    export::Format,
    export_job::{ExportControl, ExportSource},
    portrait::{self, Binding, Face, Point, Portrait},
    recipe::Recipe,
    session::{Photo, Session, SessionStore},
    source::SourceState,
};
fn face() -> Face {
    Face {
        rect: [0.2, 0.1, 0.6, 0.8],
        confidence: 1.,
        landmarks: [
            Point { x: 0.36, y: 0.36 },
            Point { x: 0.64, y: 0.36 },
            Point { x: 0.5, y: 0.5 },
            Point { x: 0.42, y: 0.68 },
            Point { x: 0.58, y: 0.68 },
        ],
    }
}
fn photo(root: &std::path::Path, name: &str) -> Photo {
    let path = root.join(name);
    let image = image::RgbImage::from_fn(100, 100, |x, y| {
        image::Rgb([
            80 + (x % 40) as u8,
            70 + (y % 45) as u8,
            55 + ((x + y) % 40) as u8,
        ])
    });
    image.save(&path).unwrap();
    Photo {
        path,
        rating: 0,
        flag: 0,
        recipe: Recipe::default(),
    }
}
fn portrait(photo: &Photo) -> Portrait {
    Portrait {
        face: Some(face()),
        binding: Some(Binding::capture(&photo.path, 100, 100).unwrap()),
        smooth: 60.,
    }
}

#[test]
fn equal_size_and_timestamp_on_another_path_cannot_reuse_face_coordinates() {
    let root = tempfile::tempdir().unwrap();
    let first = root.path().join("one");
    let second = root.path().join("two");
    fs::write(&first, b"same").unwrap();
    fs::write(&second, b"same").unwrap();
    let time = first.metadata().unwrap().modified().unwrap();
    fs::File::options()
        .write(true)
        .open(&second)
        .unwrap()
        .set_times(fs::FileTimes::new().set_modified(time))
        .unwrap();
    assert_eq!(
        SourceState::read(&first).unwrap(),
        SourceState::read(&second).unwrap()
    );
    assert!(
        !Binding::capture(&first, 100, 100)
            .unwrap()
            .matches_source(&second)
            .unwrap()
    );
}

#[test]
fn fine_checker_texture_retains_at_least_ninety_percent_local_contrast() {
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("photo");
    fs::write(&source, b"x").unwrap();
    let face = face();
    let recipe = Portrait {
        face: Some(face),
        binding: Some(Binding::capture(&source, 160, 160).unwrap()),
        smooth: 100.,
    };
    let mut pixels = Vec::new();
    for y in 0..160 {
        for x in 0..160 {
            let value = 0.45
                + 0.08 * (x as f32 * std::f32::consts::TAU / 12.).sin()
                + if (x + y) % 2 == 0 { 0.01 } else { -0.01 };
            pixels.push([value; 3]);
        }
    }
    let before = pixels.clone();
    let mut frame = z30_core::develop::SrgbFrame {
        width: 160,
        height: 160,
        pixels,
    };
    portrait::apply(&mut frame, &recipe).unwrap();
    let contrast = |pixels: &[[f32; 3]], x: usize, y: usize| {
        (pixels[y * 160 + x][0] - pixels[y * 160 + x + 1][0] - pixels[(y + 1) * 160 + x][0]
            + pixels[(y + 1) * 160 + x + 1][0])
            .abs()
    };
    let (mut source_contrast, mut result_contrast, mut pairs) = (0., 0., 0);
    for y in 2..157 {
        for x in 2..157 {
            if [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)]
                .iter()
                .all(|(xx, yy)| {
                    portrait::mask(&face, *xx as f32 + 0.5, *yy as f32 + 0.5, 160, 160) > 0.99
                })
            {
                source_contrast += contrast(&before, x, y);
                result_contrast += contrast(&frame.pixels, x, y);
                pairs += 1;
            }
        }
    }
    assert!(pairs > 100);
    assert!(result_contrast / source_contrast > 0.9);
}

#[test]
fn shared_global_settings_preserve_each_target_portrait() {
    let root = tempfile::tempdir().unwrap();
    let output = root.path().join("out");
    fs::create_dir(&output).unwrap();
    let mut first = photo(root.path(), "first.png");
    let second = photo(root.path(), "second.png");
    first.recipe.portrait = portrait(&first);
    let mut shared = first.recipe.clone();
    shared.parameters.exposure = 1.;
    let mut queue = Queue::default();
    queue
        .enqueue(
            &[first.clone(), second],
            &output,
            Format::Tiff16,
            RecipeMode::Shared(shared),
        )
        .unwrap();
    assert_eq!(queue.jobs[0].recipe.portrait, first.recipe.portrait);
    assert_eq!(queue.jobs[1].recipe.portrait, Default::default());
    assert!(
        queue
            .jobs
            .iter()
            .all(|job| job.recipe.parameters.exposure == 1.)
    );
}

#[test]
fn portrait_is_one_undo_step_ai_preserves_it_and_wrong_photo_is_rejected() {
    let root = tempfile::tempdir().unwrap();
    let first = photo(root.path(), "one.png");
    let second = photo(root.path(), "two.png");
    let mut session = Session::default();
    session.photos = vec![first.clone()];
    session.selected = Some(0);
    let mut edits = EditingSession::new(session);
    let accepted = portrait(&first);
    edits.apply_portrait(accepted).unwrap();
    assert!(edits.apply_portrait(portrait(&second)).is_err());
    let mut parameters = edits.current().unwrap().recipe.parameters;
    parameters.exposure = 0.5;
    edits.apply_ai(parameters).unwrap();
    assert_eq!(edits.current().unwrap().recipe.portrait, accepted);
    edits.undo().unwrap();
    assert_eq!(edits.current().unwrap().recipe.parameters.exposure, 0.);
    edits.undo().unwrap();
    assert_eq!(
        edits.current().unwrap().recipe.portrait,
        Portrait::default()
    );
    edits.redo().unwrap();
    edits.reset().unwrap();
    assert_eq!(
        edits.current().unwrap().recipe.portrait,
        Portrait::default()
    );
}

#[test]
fn old_schema_cannot_hide_portrait_data_and_overwrite_the_original_record() {
    let root = tempfile::tempdir().unwrap();
    let mut first = photo(root.path(), "one.png");
    first.recipe.portrait = portrait(&first);
    let mut session = Session::default();
    session.version = 2;
    session.photos = vec![first];
    session.selected = Some(0);
    let directory = root.path().join("data");
    let store = SessionStore::new(directory.clone()).unwrap();
    let previous = serde_json::to_vec(&session).unwrap();
    fs::write(directory.join("session.json"), &previous).unwrap();
    assert!(store.load().is_err());
    assert!(store.save(&Session::default()).is_err());
    assert_eq!(fs::read(directory.join("session.json")).unwrap(), previous);
}

#[test]
fn actual_preview_and_tiff16_export_use_the_same_portrait_recipe() {
    let root = tempfile::tempdir().unwrap();
    let mut photo = photo(root.path(), "one.png");
    let original = fs::read(&photo.path).unwrap();
    photo.recipe.portrait = portrait(&photo);
    let mut preview = PreviewEngine::new(None).unwrap();
    let rendered = preview.preview(&photo.path, photo.recipe.clone()).unwrap();
    let destination = root.path().join("output.tif");
    ExportSource::new(EditRequest {
        path: photo.path.clone(),
        recipe: photo.recipe,
    })
    .unwrap()
    .run(
        &destination,
        Format::Tiff16,
        None,
        &ExportControl::default(),
    )
    .unwrap();
    let output = image::open(destination).unwrap().to_rgb16();
    for (actual, wanted) in output.pixels().zip(&rendered.rendered.pixels) {
        for channel in 0..3 {
            assert_eq!(
                actual[channel],
                (wanted[channel] * 65535.).round_ties_even() as u16
            );
        }
    }
    assert_eq!(fs::read(photo.path).unwrap(), original);
}
