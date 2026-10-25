use crate::{Error, Result};

/// Owned, unquantized linear sRGB. Display encoding is a separate operation.
pub struct Frame {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<[f32; 3]>,
}
impl Frame {
    pub fn new(width: u32, height: u32, pixels: Vec<[f32; 3]>) -> Result<Self> {
        let count = (width as usize)
            .checked_mul(height as usize)
            .ok_or_else(|| Error::Policy("图像尺寸溢出".into()))?;
        if count == 0
            || count > 80_000_000
            || pixels.len() != count
            || pixels.iter().flatten().any(|v| !v.is_finite())
        {
            return Err(Error::Policy("线性图像尺寸/像素/数值无效".into()));
        }
        Ok(Self {
            width,
            height,
            pixels,
        })
    }
}
