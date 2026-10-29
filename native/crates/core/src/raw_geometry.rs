use crate::{Error, Result, frame::Frame};

#[derive(Clone, Copy, Debug)]
pub struct RawSizes {
    pub raw_width: u32,
    pub raw_height: u32,
    pub width: u32,
    pub height: u32,
    pub left: u32,
    pub top: u32,
    pub crop_left: u32,
    pub crop_top: u32,
    pub crop_width: u32,
    pub crop_height: u32,
    pub flip: i32,
}
impl RawSizes {
    pub fn crop(&self) -> (u32, u32, u32, u32) {
        let full = (0, 0, self.width, self.height);
        if self.crop_left == 65535
            || self.crop_top == 65535
            || self.crop_width == 0
            || self.crop_height == 0
            || self.crop_left as u64 + self.crop_width as u64 > self.raw_width as u64
            || self.crop_top as u64 + self.crop_height as u64 > self.raw_height as u64
        {
            return full;
        }
        let x0 = (self.crop_left as i64 - self.left as i64).max(0);
        let y0 = (self.crop_top as i64 - self.top as i64).max(0);
        let x1 = (self.crop_left as i64 + self.crop_width as i64 - self.left as i64)
            .min(self.width as i64);
        let y1 = (self.crop_top as i64 + self.crop_height as i64 - self.top as i64)
            .min(self.height as i64);
        if x1 <= x0 || y1 <= y0 {
            return full;
        }
        (x0 as u32, y0 as u32, (x1 - x0) as u32, (y1 - y0) as u32)
    }
}

pub(crate) fn coefficients(length: usize, output: usize, start: f64, end: f64) -> Vec<Vec<(usize, f64)>> {
    let scale = (end - start) / output as f64;
    let filter_scale = scale.max(1.);
    (0..output)
        .map(|i| {
            let center = start + (i as f64 + 0.5) * scale;
            let low = ((center - filter_scale + 0.5) as i64).max(0) as usize;
            let high = ((center + filter_scale + 0.5) as i64).clamp(0, length as i64) as usize;
            let mut weights: Vec<_> = (low..high)
                .map(|x| {
                    (
                        x,
                        (1. - ((x as f64 - center + 0.5) / filter_scale).abs()).max(0.),
                    )
                })
                .collect();
            let sum: f64 = weights.iter().map(|(_, v)| v).sum();
            for (_, weight) in &mut weights {
                *weight /= sum;
            }
            weights
        })
        .collect()
}

/// Same sensor-relative inset / fractional half-size / eight-flip contract as imaging.py.
pub fn reframe16(
    bytes: &[u8],
    decoded_width: u32,
    decoded_height: u32,
    sizes: RawSizes,
    half: bool,
) -> Result<Frame> {
    if sizes.width == 0 || sizes.height == 0 || !(0..8).contains(&sizes.flip) {
        return Err(Error::Policy("RAW 尺寸/方向无效".into()));
    }
    let factor = if half { 2 } else { 1 };
    if (decoded_width, decoded_height)
        != (sizes.width.div_ceil(factor), sizes.height.div_ceil(factor))
    {
        return Err(Error::Policy(
            "RAW 解码尺寸与有效区域不一致，未猜测几何".into(),
        ));
    }
    let count = (decoded_width as usize)
        .checked_mul(decoded_height as usize)
        .and_then(|n| n.checked_mul(6))
        .ok_or_else(|| Error::Policy("RAW 缓冲长度溢出".into()))?;
    if count != bytes.len() || count > 480_000_000 {
        return Err(Error::Policy("RAW 像素缓冲无效或超过限制".into()));
    }
    let pixel = |x: usize, y: usize| -> [f32; 3] {
        let at = (y * decoded_width as usize + x) * 6;
        std::array::from_fn(|c| {
            u16::from_le_bytes([bytes[at + c * 2], bytes[at + c * 2 + 1]]) as f32
                * (1.0f32 / 65535.)
        })
    };
    let (left, top, width, height) = sizes.crop();
    let f = factor as f64;
    let x0 = left as f64 / f;
    let y0 = top as f64 / f;
    let x1 = (left + width) as f64 / f;
    let y1 = (top + height) as f64 / f;
    let w = ((width as f64 / f).round_ties_even() as usize).max(1);
    let h = ((height as f64 / f).round_ties_even() as usize).max(1);
    let transposed = sizes.flip & 4 != 0;
    let (ow, oh) = if transposed { (h, w) } else { (w, h) };
    let output_count = ow
        .checked_mul(oh)
        .filter(|&n| n <= 80_000_000)
        .ok_or_else(|| Error::Policy("RAW 输出尺寸超过限制".into()))?;
    let mut output = Vec::new();
    output
        .try_reserve_exact(output_count)
        .map_err(|_| Error::Policy("RAW 图像内存不足".into()))?;
    let fractional = half && [x0, y0, x1, y1].iter().any(|v| v.fract() != 0.);
    let intermediate = if fractional {
        let xs = coefficients(decoded_width as usize, w, x0, x1);
        let ys = coefficients(decoded_height as usize, h, y0, y1);
        let mut rows = vec![[0.0f32; 3]; w * decoded_height as usize];
        for y in 0..decoded_height as usize {
            for (x, weights) in xs.iter().enumerate() {
                let mut sum = [0.0f64; 3];
                for &(sx, weight) in weights {
                    let value = pixel(sx, y);
                    for c in 0..3 {
                        sum[c] += value[c] as f64 * weight;
                    }
                }
                rows[y * w + x] = sum.map(|v| v as f32);
            }
        }
        let mut resized = vec![[0.0f32; 3]; w * h];
        for (y, weights) in ys.iter().enumerate() {
            for x in 0..w {
                let mut sum = [0.0f64; 3];
                for &(sy, weight) in weights {
                    for c in 0..3 {
                        sum[c] += rows[sy * w + x][c] as f64 * weight;
                    }
                }
                resized[y * w + x] = sum.map(|v| v as f32);
            }
        }
        Some(resized)
    } else {
        None
    };
    for y in 0..oh {
        for x in 0..ow {
            let (mut sx, mut sy) = if transposed { (y, x) } else { (x, y) };
            if sizes.flip & 1 != 0 {
                sx = w - 1 - sx;
            }
            if sizes.flip & 2 != 0 {
                sy = h - 1 - sy;
            }
            output.push(if let Some(ref resized) = intermediate {
                resized[sy * w + sx]
            } else {
                pixel(x0 as usize + sx, y0 as usize + sy)
            });
        }
    }
    Frame::new(ow as u32, oh as u32, output)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fractional_half_size_matches_24_frozen_python_cases() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/raw-geometry.json")).unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let s = |key: &str| case["sizes"][key].as_u64().unwrap() as u32;
            let sizes = RawSizes {
                raw_width: s("raw_width"),
                raw_height: s("raw_height"),
                width: s("width"),
                height: s("height"),
                left: s("left_margin"),
                top: s("top_margin"),
                crop_left: s("crop_left_margin"),
                crop_top: s("crop_top_margin"),
                crop_width: s("crop_width"),
                crop_height: s("crop_height"),
                flip: s("flip") as i32,
            };
            let bytes: Vec<u8> = case["pixels"]
                .as_array()
                .unwrap()
                .iter()
                .flat_map(|n| (n.as_u64().unwrap() as u16).to_le_bytes())
                .collect();
            let result = reframe16(&bytes, 4, 3, sizes, true).unwrap();
            assert_eq!(
                (result.width, result.height),
                (
                    case["width"].as_u64().unwrap() as u32,
                    case["height"].as_u64().unwrap() as u32
                )
            );
            for (actual, expected) in result
                .pixels
                .iter()
                .zip(case["expected"].as_array().unwrap())
            {
                for c in 0..3 {
                    assert!(
                        (actual[c] - expected[c].as_f64().unwrap() as f32).abs() <= 1e-7,
                        "fractional geometry differs: {sizes:?}"
                    );
                }
            }
        }
    }
    fn sizes() -> RawSizes {
        RawSizes {
            raw_width: 3,
            raw_height: 2,
            width: 3,
            height: 2,
            left: 0,
            top: 0,
            crop_left: 65535,
            crop_top: 65535,
            crop_width: 0,
            crop_height: 0,
            flip: 0,
        }
    }
    fn values() -> Vec<u8> {
        (1u16..=6)
            .flat_map(|v| [v, v, v].into_iter().flat_map(u16::to_le_bytes))
            .collect()
    }
    #[test]
    fn all_eight_directions_keep_the_same_pixel_identity() {
        let expected = [
            vec![1, 2, 3, 4, 5, 6],
            vec![3, 2, 1, 6, 5, 4],
            vec![4, 5, 6, 1, 2, 3],
            vec![6, 5, 4, 3, 2, 1],
            vec![1, 4, 2, 5, 3, 6],
            vec![3, 6, 2, 5, 1, 4],
            vec![4, 1, 5, 2, 6, 3],
            vec![6, 3, 5, 2, 4, 1],
        ];
        for flip in 0..8 {
            let result = reframe16(&values(), 3, 2, RawSizes { flip, ..sizes() }, false).unwrap();
            let actual: Vec<_> = result
                .pixels
                .iter()
                .map(|p| (p[0] * 65535.).round() as u16)
                .collect();
            assert_eq!(actual, expected[flip as usize]);
            assert_eq!(
                (result.width, result.height),
                if flip & 4 == 0 { (3, 2) } else { (2, 3) }
            );
        }
    }
    #[test]
    fn sensor_crop_accounts_for_active_margins_and_invalid_insets() {
        let s = RawSizes {
            raw_width: 8,
            raw_height: 6,
            left: 2,
            top: 1,
            crop_left: 3,
            crop_top: 1,
            crop_width: 2,
            crop_height: 2,
            ..sizes()
        };
        assert_eq!(s.crop(), (1, 0, 2, 2));
        let result = reframe16(&values(), 3, 2, s, false).unwrap();
        assert_eq!(
            result
                .pixels
                .iter()
                .map(|p| (p[0] * 65535.).round() as u16)
                .collect::<Vec<_>>(),
            vec![2, 3, 5, 6]
        );
        assert_eq!(RawSizes { crop_left: 99, ..s }.crop(), (0, 0, 3, 2));
    }
    #[test]
    fn inconsistent_buffer_and_geometry_fail_closed() {
        assert!(reframe16(&values(), 2, 3, sizes(), false).is_err());
        assert!(reframe16(&values()[..3], 3, 2, sizes(), false).is_err());
        assert!(reframe16(&values(), 3, 2, RawSizes { flip: 8, ..sizes() }, false).is_err());
    }
}
