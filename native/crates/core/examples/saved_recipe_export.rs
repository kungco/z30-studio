//! Read-only developer oracle for a saved native recipe, not a GUI command.
use std::{fs, path::PathBuf};
use z30_core::{
    editing::EditRequest,
    export::Format,
    export_job::{ExportControl, ExportSource},
    session::Session,
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() < 3 || args.len() > 4 {
        return Err(
            "usage: saved_recipe_export SESSION.json INDEX NEW.tif [ABS_LIBRAW_DLL]".into(),
        );
    }
    let path = PathBuf::from(&args[0]);
    if fs::metadata(&path)?.len() > 32 * 1024 * 1024 {
        return Err("session exceeds budget".into());
    }
    let session: Session = serde_json::from_slice(&fs::read(path)?)?;
    session.validate()?;
    let index: usize = args[1].to_str().ok_or("index must be UTF8")?.parse()?;
    let photo = session.photos.get(index).ok_or("invalid photo index")?;
    let destination = PathBuf::from(&args[2]);
    if destination
        .extension()
        .is_none_or(|v| !v.eq_ignore_ascii_case("tif"))
    {
        return Err("choose a new TIFF path".into());
    }
    let dll = args.get(3).map(PathBuf::from);
    let receipt = ExportSource::new(EditRequest {
        path: photo.path.clone(),
        recipe: photo.recipe.clone(),
    })?
    .run(
        &destination,
        Format::Tiff16,
        dll.as_deref(),
        &ExportControl::default(),
    )?;
    println!(
        "{}",
        serde_json::json!({"width":receipt.width,"height":receipt.height,"seconds":receipt.seconds})
    );
    Ok(())
}
