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
    recipe.validate()?;
    recipe
        .portrait
        .check_source(path, original_size.0, original_size.1)?;
    let mut output = develop::render(source, &recipe.parameters)?;
    crate::portrait::apply(&mut output, &recipe.portrait)?;
    Ok(output)
}
