//! Prepare a NEW synthetic QA profile with a real source-bound recipe.
use std::{fs, path::PathBuf};
use z30_core::{
    editing::EditRequest,
    export::Format,
    export_job::{ExportControl, ExportSource},
    portrait::Binding,
    recipe::Recipe,
    session::{Photo, Session, SessionStore},
    warp::Pin,
    warps::Warps,
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() < 2 || args.len() > 3 {
        return Err(
            "usage: warp_workflow_probe INPUT NEW_PROFILE_DIR [heavy|ABS_LIBRAW_DLL]".into(),
        );
    }
    let path = PathBuf::from(&args[0]).canonicalize()?;
    let data = PathBuf::from(&args[1]);
    fs::create_dir(&data)?;
    let heavy = args.get(2).is_some_and(|v| v == "heavy");
    let raw_dll = if !heavy {
        args.get(2).map(PathBuf::from)
    } else {
        None
    };
    let (w, h) = if path
        .extension()
        .is_some_and(|v| v.eq_ignore_ascii_case("nef"))
    {
        #[cfg(windows)]
        {
            let dll = raw_dll.as_deref().ok_or("RAW needs an explicit DLL")?;
            let d = z30_core::raw::LibRaw::load(dll)?.decode(
                &path,
                false,
                z30_core::recipe::WhiteBalance::Camera,
            )?;
            (d.frame.width, d.frame.height)
        }
        #[cfg(not(windows))]
        {
            return Err("RAW probe needs Windows".into());
        }
    } else {
        let d = z30_core::raster::decode(&path)?;
        (d.frame.width, d.frame.height)
    };
    let radius = if heavy { 0.2 } else { 0.12 };
    let delta = 0.08 * radius * w.min(h) as f64;
    let pins = vec![
        Pin {
            from: [0.5 - delta / (w - 1) as f64, 0.5],
            to: [0.5, 0.5],
            radius
        };
        if heavy { 4 } else { 1 }
    ];
    let recipe = Recipe {
        warp: Some(Warps::new(Binding::capture(&path, w, h)?, pins)?),
        ..Default::default()
    };
    let store = SessionStore::new(data.clone())?;
    store.save(&Session {
        photos: vec![Photo {
            path: path.clone(),
            recipe: recipe.clone(),
            rating: 0,
            flag: 0,
        }],
        selected: Some(0),
        ..Default::default()
    })?;
    if !heavy {
        for (name, recipe) in [("expected.tif", recipe), ("default.tif", Recipe::default())] {
            ExportSource::new(EditRequest {
                path: path.clone(),
                recipe,
            })?
            .run(
                &data.join(name),
                Format::Tiff16,
                raw_dll.as_deref(),
                &ExportControl::default(),
            )?;
        }
    }
    println!(
        "{}",
        serde_json::json!({"width":w,"height":h,"heavy":heavy,"source_read_only":true})
    );
    Ok(())
}
