//! The same recipe path is used for the preview and full-size output.
use crate::{
    Result,
    develop::{self, SrgbFrame},
    frame::Frame,
    recipe::Recipe,
};
use std::path::Path;

pub fn render(
    source: &Frame,
    recipe: &Recipe,
    path: &Path,
    original_size: (u32, u32),
) -> Result<SrgbFrame> {
    render_with_skin(source, recipe, path, original_size, recipe.skin.as_ref())
}

pub fn render_with_cancel(
    source: &Frame,
    recipe: &Recipe,
    path: &Path,
    original_size: (u32, u32),
    cancelled: impl FnMut() -> bool,
) -> Result<SrgbFrame> {
    render_internal(
        source,
        recipe,
        path,
        original_size,
        recipe.skin.as_ref(),
        cancelled,
    )
}

/// Explicit engine entry point for a previously source-bound semantic gate.
pub fn render_with_skin(
    source: &Frame,
    recipe: &Recipe,
    path: &Path,
    original_size: (u32, u32),
    skin: Option<&crate::skin::SkinMask>,
) -> Result<SrgbFrame> {
    render_internal(source, recipe, path, original_size, skin, || false)
}
fn render_internal(
    source: &Frame,
    recipe: &Recipe,
    path: &Path,
    original_size: (u32, u32),
    skin: Option<&crate::skin::SkinMask>,
    mut cancelled: impl FnMut() -> bool,
) -> Result<SrgbFrame> {
    if cancelled() {
        return Err(crate::Error::Policy("显影已取消".into()));
    }
    recipe.validate()?;
    if recipe.skin.is_some() && skin.is_some() && recipe.skin.as_ref() != skin {
        return Err(crate::Error::Policy(
            "显影蒙版与保存配方不一致，未退回几何柔化".into(),
        ));
    }
    recipe.portrait.check_source_for_effect(
        path,
        original_size.0,
        original_size.1,
        recipe.portrait.smooth > 0. || recipe.skin_tone.active(),
    )?;
    let repaired = if let Some(repairs) = &recipe.repairs {
        repairs.check_source(path, original_size.0, original_size.1)?;
        crate::retouch::render_with_cancel(source, repairs.points(), &mut cancelled)?
    } else {
        None
    };
    if cancelled() {
        return Err(crate::Error::Policy("显影已取消".into()));
    }
    let mut output = develop::render(repaired.as_ref().unwrap_or(source), &recipe.parameters)?;
    drop(repaired);
    crate::portrait::apply_with_skin(&mut output, &recipe.portrait, skin.or(recipe.skin.as_ref()))?;
    if recipe.skin_tone.active() {
        crate::skin_tone::apply(
            &mut output,
            &recipe.portrait,
            skin.or(recipe.skin.as_ref()).unwrap(),
            recipe.skin_tone,
        )?;
    }
    if cancelled() {
        return Err(crate::Error::Policy("显影已取消".into()));
    }
    Ok(output)
}
