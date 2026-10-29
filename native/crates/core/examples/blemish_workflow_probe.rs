//! Developer-only synthetic face/skin fixture, not model inference.
use std::{fs, path::PathBuf};
use z30_core::{
    portrait::{Binding, Face, Point, Portrait},
    recipe::Recipe,
    session::{Photo, Session, SessionStore},
    skin::SkinMask,
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 2 {
        return Err("usage: blemish_workflow_probe SYNTHETIC.png NEWPROFILE".into());
    }
    let path = PathBuf::from(&args[0]).canonicalize()?;
    let root = PathBuf::from(&args[1]);
    if root.exists() {
        return Err("choose a new profile".into());
    }
    let decoded = z30_core::raster::decode(&path)?;
    let (w, h) = (decoded.frame.width, decoded.frame.height);
    drop(decoded);
    let binding = Binding::capture(&path, w, h)?;
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
        skin: Some(SkinMask::new(binding, vec![255; 256 * 256])?),
        ..Default::default()
    };
    let mut session = Session::default();
    session.selected = Some(0);
    session.photos.push(Photo {
        path,
        recipe,
        rating: 0,
        flag: 0,
    });
    let store = SessionStore::new(root.clone())?;
    store.load()?;
    store.save(&session)?;
    fs::write(
        root.join("synthetic-fixture.txt"),
        "Synthetic face geometry/mask, NOT real AI inference or real skin validation.",
    )?;
    Ok(())
}
