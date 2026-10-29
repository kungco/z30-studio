//! Developer-only preview cache acceptance. Does not save or modify photos.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::{path::PathBuf, sync::Arc, time::Instant};
    use z30_core::{develop::Parameters, editor::PreviewEngine, recipe::Recipe};
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 3 {
        return Err("usage: edit_preview_probe DLL_OR_DASH INPUT PARAMETERS.json".into());
    }
    let source = PathBuf::from(&args[1]);
    let parameters = PathBuf::from(&args[2]);
    if parameters.metadata()?.len() > 8192 {
        return Err("parameters exceed 8 KiB".into());
    }
    let parameters: Parameters = serde_json::from_slice(&std::fs::read(parameters)?)?;
    parameters.validate()?;
    let mut engine = PreviewEngine::new(if args[0] == "-" {
        None
    } else {
        Some(PathBuf::from(&args[0]))
    })?;
    let started = Instant::now();
    let neutral = engine.preview(&source, Recipe::default())?;
    let first_seconds = started.elapsed().as_secs_f64();
    let recipe = Recipe {
        parameters,
        ..Default::default()
    };
    let warm = engine.preview(&source, recipe.clone())?;
    let repeat = engine.preview(&source, recipe)?;
    let shared_neutral = Arc::ptr_eq(&neutral.neutral, &warm.neutral);
    let deterministic_repeat = warm.rendered.pixels == repeat.rendered.pixels;
    let edits_change_pixels = warm.rendered.pixels != neutral.rendered.pixels;
    let success = !neutral.reused_decode
        && warm.reused_decode
        && repeat.reused_decode
        && shared_neutral
        && deterministic_repeat
        && edits_change_pixels;
    println!(
        "{}",
        serde_json::json!({"stage":"2E-edit-preview-core","gui_editing_enabled":false,"success":success,
        "source_width":warm.source_width,"source_height":warm.source_height,"preview_width":warm.rendered.width,"preview_height":warm.rendered.height,
        "first_preview_seconds":first_seconds,"warm_render_seconds":warm.render_seconds,"repeat_render_seconds":repeat.render_seconds,
        "warm_decode_resize_seconds":warm.decode_resize_seconds,"reused_decode":warm.reused_decode,"shared_neutral":shared_neutral,
        "deterministic_repeat":deterministic_repeat,"edits_change_pixels":edits_change_pixels,"source_bits":warm.source_bits})
    );
    if !success {
        return Err("Preview cache acceptance failed".into());
    }
    Ok(())
}
