//! Immutable, source-bound confidence gate; image pixels stay floating point.
//! Persistence/UI wiring is separate from this experimental engine contract.
use crate::{
    Error, Result,
    portrait::{Binding, Portrait},
};
use std::sync::Arc;

pub const SIDE: usize = 256;

#[derive(Clone, Debug)]
pub struct SkinMask {
    binding: Binding,
    weights: Arc<[u8]>,
}
impl SkinMask {
    pub fn new(binding: Binding, weights: Vec<u8>) -> Result<Self> {
        binding.validate()?;
        if weights.len() != SIDE * SIDE {
            return Err(Error::Policy("皮肤蒙版尺寸无效".into()));
        }
        Ok(Self {
            binding,
            weights: weights.into(),
        })
    }
    pub fn weights(&self) -> &[u8] {
        &self.weights
    }
    pub fn check_portrait(&self, portrait: &Portrait) -> Result<()> {
        if portrait.binding != Some(self.binding) || portrait.face.is_none() {
            return Err(Error::Policy("皮肤蒙版与人像原片绑定不一致，未应用".into()));
        }
        Ok(())
    }
    /// Pixel-centre coordinates in any preview/export size of the same oriented
    /// image. No extra rotation, letterboxing, or input-image quantization.
    pub fn sample(&self, x: f64, y: f64, width: u32, height: u32) -> f32 {
        if width == 0
            || height == 0
            || !x.is_finite()
            || !y.is_finite()
            || x < 0.
            || y < 0.
            || x >= width as f64
            || y >= height as f64
        {
            return 0.;
        }
        let fx = (x * SIDE as f64 / width as f64 - 0.5).clamp(0., (SIDE - 1) as f64);
        let fy = (y * SIDE as f64 / height as f64 - 0.5).clamp(0., (SIDE - 1) as f64);
        let (x0, y0) = (fx.floor() as usize, fy.floor() as usize);
        let (x1, y1) = ((x0 + 1).min(SIDE - 1), (y0 + 1).min(SIDE - 1));
        let (dx, dy) = ((fx - x0 as f64) as f32, (fy - y0 as f64) as f32);
        let a = self.weights[y0 * SIDE + x0] as f32;
        let b = self.weights[y0 * SIDE + x1] as f32;
        let c = self.weights[y1 * SIDE + x0] as f32;
        let d = self.weights[y1 * SIDE + x1] as f32;
        let top = a + (b - a) * dx;
        (top + (c + (d - c) * dx - top) * dy) / 255.
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn normalized_sampling_preserves_non_square_preview_full_coordinates() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("sample");
        std::fs::write(&path, b"sample").unwrap();
        let binding = Binding::capture(&path, 6016, 4000).unwrap();
        let weights = (0..SIDE * SIDE)
            .map(|i| ((i % SIDE + 3 * (i / SIDE)) % 256) as u8)
            .collect();
        let mask = SkinMask::new(binding, weights).unwrap();
        for (x, y) in [(0.1, 0.2), (0.5, 0.5), (0.9, 0.8)] {
            let full = mask.sample(x * 6016., y * 4000., 6016, 4000);
            assert_eq!(full, mask.sample(x * 3008., y * 2000., 3008, 2000));
            assert_eq!(full, mask.sample(x * 1504., y * 1000., 1504, 1000));
        }
        assert_eq!(mask.sample(f64::NAN, 2., 10, 10), 0.);
        assert_eq!(mask.sample(1., 1., 0, 10), 0.);
        assert_eq!(mask.sample(-1., 1., 10, 10), 0.);
        assert_eq!(mask.sample(10., 1., 10, 10), 0.);
        assert!(SkinMask::new(binding, vec![0; 3]).is_err());
    }
}
