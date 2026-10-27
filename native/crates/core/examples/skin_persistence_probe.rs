//! Capacity/I/O benchmark only: placeholder source records are not image
//! decoding or portrait-quality fixtures. Writes exclusively in a new dir.
use std::{io::Read, path::PathBuf, time::Instant};
use z30_core::{
    batch::{Queue, RecipeMode, Store},
    portrait::Binding,
    session::{Session, SessionStore},
    skin::{SIDE, SkinMask},
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 3 {
        return Err(
            "usage: skin_persistence_probe ABS_SEED_SESSION natural|worst NEW_ABS_DIR".into(),
        );
    }
    let input = PathBuf::from(&args[0]);
    let mode = args[1].to_str().ok_or("mode")?;
    let output = PathBuf::from(&args[2]);
    if !input.is_absolute() || !output.is_absolute() || !["natural", "worst"].contains(&mode) {
        return Err("invalid arguments".into());
    }
    let mut bytes = Vec::new();
    std::fs::File::open(&input)?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > 1024 * 1024 {
        return Err("single-photo seed too large".into());
    }
    let seed: Session = serde_json::from_slice(&bytes)?;
    seed.validate()?;
    let first = seed.photos.first().ok_or("seed photo")?;
    let natural = first.recipe.skin.as_ref().ok_or("seed mask")?;
    std::fs::create_dir(&output)?;
    let mut photos = Vec::new();
    for i in 0..256 {
        let path = output.join(format!("capacity-only-{i}.png"));
        std::fs::write(&path, b"capacity record, not an image")?;
        let mut photo = first.clone();
        photo.path = path.canonicalize()?;
        let prior = photo.recipe.portrait.binding.unwrap();
        let binding = Binding::capture(&photo.path, prior.width, prior.height)?;
        photo.recipe.portrait.binding = Some(binding);
        let weights = if mode == "natural" {
            natural.weights().to_vec()
        } else {
            (0..SIDE * SIDE)
                .map(|n| ((n * 31 + i) % 256) as u8)
                .collect()
        };
        photo.recipe.skin = Some(SkinMask::new(binding, weights)?);
        photos.push(photo);
    }
    let session = Session {
        photos: photos.clone(),
        selected: Some(255),
        ..Default::default()
    };
    let store = SessionStore::new(output.join("draft"))?;
    let start = Instant::now();
    store.save(&session)?;
    let save = start.elapsed().as_secs_f64();
    let start = Instant::now();
    let loaded = store.load()?;
    let load = start.elapsed().as_secs_f64();
    if loaded != session {
        return Err("draft roundtrip mismatch".into());
    }
    drop(loaded);
    let mut queue = Queue::default();
    let mut twice = photos.clone();
    twice.extend(photos);
    queue.enqueue(
        &twice,
        &output,
        z30_core::export::Format::Tiff16,
        RecipeMode::PerPhoto,
    )?;
    let batch = Store::new(output.join("queue"))?;
    let start = Instant::now();
    batch.save(&queue)?;
    let batch_save = start.elapsed().as_secs_f64();
    let start = Instant::now();
    let restored = batch.load()?;
    let batch_load = start.elapsed().as_secs_f64();
    if restored.jobs.len() != 512 || restored.jobs[0].recipe != queue.jobs[0].recipe {
        return Err("queue mismatch".into());
    }
    println!(
        "{}",
        serde_json::json!({"mode":mode,"photo_count":256,"job_count":512,
        "draft_bytes":std::fs::metadata(output.join("draft/session.json"))?.len(),
        "queue_bytes":std::fs::metadata(output.join("queue/batch-jobs.json"))?.len(),
        "draft_save_seconds":save,"draft_load_seconds":load,"queue_save_seconds":batch_save,"queue_load_seconds":batch_load,
        "roundtrip_identical":true,"synthetic_capacity_records":true,"portrait_quality_test":false})
    );
    Ok(())
}
