use crate::{Error, Result};

/// Owned, unquantized linear sRGB. Display encoding is a separate operation.
pub struct Frame {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<[f32; 3]>,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn linear_resize_matches_independent_float_channel_reference() {
        #[derive(serde::Deserialize)]
        struct Source {
            width: u32,
            height: u32,
            pixels: Vec<[f32; 3]>,
        }
        #[derive(serde::Deserialize)]
        struct Resize {
            bounds: [u32; 2],
            width: u32,
            height: u32,
            expected: Vec<[f32; 3]>,
        }
        #[derive(serde::Deserialize)]
        struct Corpus {
            resize_source: Source,
            resizes: Vec<Resize>,
        }
        let data: Corpus =
            serde_json::from_str(include_str!("../tests/fixtures/raster.json")).unwrap();
        let source = Frame::new(
            data.resize_source.width,
            data.resize_source.height,
            data.resize_source.pixels,
        )
        .unwrap();
        let before = source.pixels.clone();
        for case in data.resizes {
            let result = source.thumbnail(case.bounds[0], case.bounds[1]).unwrap();
            assert_eq!((result.width, result.height), (case.width, case.height));
            for (actual, expected) in result
                .pixels
                .iter()
                .flatten()
                .zip(case.expected.iter().flatten())
            {
                assert!((actual - expected).abs() < 2e-7);
            }
        }
        assert_eq!(source.pixels, before);
        assert!(source.thumbnail(0, 1).is_err());
    }
}
impl Frame {
    /// EXIF permutation cycles use one bit of scratch per pixel, not another RGB frame.
    pub fn oriented(mut self, orientation: u8) -> Result<Self> {
        if !(1..=8).contains(&orientation) {
            return Err(Error::Policy("EXIF 方向无效".into()));
        }
        if orientation == 1 {
            return Ok(self);
        }
        let (width, height) = (self.width as usize, self.height as usize);
        let out_width = if orientation >= 5 { height } else { width };
        let destination = |i: usize| {
            let (x, y) = (i % width, i / width);
            let (xx, yy) = match orientation {
                2 => (width - 1 - x, y),
                3 => (width - 1 - x, height - 1 - y),
                4 => (x, height - 1 - y),
                5 => (y, x),
                6 => (height - 1 - y, x),
                7 => (height - 1 - y, width - 1 - x),
                8 => (y, width - 1 - x),
                _ => (x, y),
            };
            yy * out_width + xx
        };
        let mut visited = vec![0u8; self.pixels.len().div_ceil(8)];
        for start in 0..self.pixels.len() {
            if visited[start / 8] & (1 << (start % 8)) != 0 {
                continue;
            }
            let mut at = start;
            let mut value = self.pixels[at];
            loop {
                visited[at / 8] |= 1 << (at % 8);
                let target = destination(at);
                std::mem::swap(&mut value, &mut self.pixels[target]);
                at = target;
                if at == start {
                    break;
                }
            }
        }
        if orientation >= 5 {
            std::mem::swap(&mut self.width, &mut self.height);
        }
        Ok(self)
    }

    /// Linear-light bilinear downsample with a bounded horizontal row cache.
    pub fn thumbnail(&self, max_width: u32, max_height: u32) -> Result<Self> {
        if max_width == 0 || max_height == 0 {
            return Err(Error::Policy("预览尺寸无效".into()));
        }
        let scale = (max_width as f64 / self.width as f64)
            .min(max_height as f64 / self.height as f64)
            .min(1.);
        let width = (self.width as f64 * scale).round().max(1.) as usize;
        let height = (self.height as f64 * scale).round().max(1.) as usize;
        let xs =
            crate::raw_geometry::coefficients(self.width as usize, width, 0., self.width as f64);
        let ys =
            crate::raw_geometry::coefficients(self.height as usize, height, 0., self.height as f64);
        let mut rows = std::collections::BTreeMap::<usize, Vec<[f32; 3]>>::new();
        let mut out = Vec::new();
        out.try_reserve_exact(width * height)
            .map_err(|_| Error::Policy("预览内存不足".into()))?;
        for y_weights in ys {
            rows.retain(|row, _| y_weights.iter().any(|(y, _)| row == y));
            for &(y, _) in &y_weights {
                rows.entry(y).or_insert_with(|| {
                    xs.iter()
                        .map(|weights| {
                            let mut rgb = [0f64; 3];
                            for &(x, weight) in weights {
                                for c in 0..3 {
                                    rgb[c] +=
                                        self.pixels[y * self.width as usize + x][c] as f64 * weight;
                                }
                            }
                            rgb.map(|v| v as f32)
                        })
                        .collect()
                });
            }
            for x in 0..width {
                let mut rgb = [0f64; 3];
                for &(y, weight) in &y_weights {
                    for c in 0..3 {
                        rgb[c] += rows[&y][x][c] as f64 * weight;
                    }
                }
                out.push(rgb.map(|v| v as f32));
            }
        }
        Self::new(width as u32, height as u32, out)
    }

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
