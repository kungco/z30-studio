//! Optional CPU face-skin segmentation using the pinned MediaPipe conversion.
//! The official TFLite metadata requires RGB NHWC, (byte-127.5)/127.5,
//! and SOFTMAX of six output logits. No network or identity recognition.
use crate::detector::{open_session, verified_bytes};
use ort::{session::Session, value::Tensor};
use serde::Serialize;
use std::{
    path::Path,
    sync::atomic::{AtomicBool, Ordering},
    time::Instant,
};
use z30_core::{Error, Result, develop::SrgbFrame, skin::SIDE};

pub const MODEL_NAME: &str = "selfie_multiclass_256x256.onnx";
pub const MODEL_BYTES: u64 = 16454497;
pub const MODEL_HASH: &str = "13961350bb2099688eb96182ffe26f24eb12c77b1df278a2ec95bbdc923136e1";

fn failure(error: impl std::fmt::Display) -> Error {
    Error::Policy(format!("本机皮肤识别失败：{error}"))
}
fn check_cancel(cancel: &AtomicBool) -> Result<()> {
    if cancel.load(Ordering::Acquire) {
        Err(failure("已取消"))
    } else {
        Ok(())
    }
}

#[derive(Serialize)]
pub struct Segmentation {
    pub source_width: u32,
    pub source_height: u32,
    #[serde(skip)]
    pub face_probabilities: Vec<f32>,
    #[serde(skip)]
    pub categories: Vec<u8>,
    #[serde(skip)]
    pub weights: Vec<u8>,
    pub accepted_pixels: usize,
    pub prepare_seconds: f64,
    pub inference_seconds: f64,
    pub postprocess_seconds: f64,
    pub local_only: bool,
}

pub struct Segmenter {
    session: Session,
}
impl Segmenter {
    /// The already-audited vision runtime is shared with the face detector.
    /// The separate model directory must contain these exact pinned weights.
    pub fn open(runtime_directory: &Path, model_directory: &Path) -> Result<Self> {
        let model = verified_bytes(&model_directory.join(MODEL_NAME), MODEL_BYTES, MODEL_HASH)?;
        Ok(Self {
            session: open_session(runtime_directory, &model)?,
        })
    }
    pub fn segment(&mut self, frame: &SrgbFrame, cancel: &AtomicBool) -> Result<Segmentation> {
        check_cancel(cancel)?;
        let started = Instant::now();
        let values = prepare(frame)?;
        let prepare_seconds = started.elapsed().as_secs_f64();
        check_cancel(cancel)?;
        let input = Tensor::from_array(([1usize, SIDE, SIDE, 3], values)).map_err(failure)?;
        let started = Instant::now();
        let output = self
            .session
            .run(ort::inputs!["input_29" => input])
            .map_err(failure)?;
        let inference_seconds = started.elapsed().as_secs_f64();
        check_cancel(cancel)?;
        let started = Instant::now();
        let tensor = output
            .get("Identity")
            .ok_or_else(|| failure("缺少 Identity 输出"))?;
        let (shape, logits) = tensor.try_extract_tensor::<f32>().map_err(failure)?;
        if shape.len() != 4
            || shape[0] != 1
            || shape[1] != SIDE as i64
            || shape[2] != SIDE as i64
            || shape[3] != 6
        {
            return Err(failure("输出维度不符"));
        }
        let (face_probabilities, categories, weights) = decode(logits)?;
        check_cancel(cancel)?;
        Ok(Segmentation {
            source_width: frame.width,
            source_height: frame.height,
            accepted_pixels: weights.iter().filter(|v| **v > 0).count(),
            face_probabilities,
            categories,
            weights,
            prepare_seconds,
            inference_seconds,
            postprocess_seconds: started.elapsed().as_secs_f64(),
            local_only: true,
        })
    }
}

fn prepare(frame: &SrgbFrame) -> Result<Vec<f32>> {
    let count = frame.width as u64 * frame.height as u64;
    if count == 0
        || count > 80_000_000
        || count as usize != frame.pixels.len()
        || frame
            .pixels
            .iter()
            .flatten()
            .any(|v| !v.is_finite() || !(-1e-6..=1.000001).contains(v))
    {
        return Err(failure("需要有效的定向浮点 sRGB 图像"));
    }
    // Stretch to the model's square, matching the official no-letterbox input.
    // Coordinates are sampled at pixel centres and mapped back independently
    // on each axis; portrait/landscape images retain the same source geometry.
    let mut values = Vec::with_capacity(SIDE * SIDE * 3);
    for y in 0..SIDE {
        let fy = ((y as f64 + 0.5) * frame.height as f64 / SIDE as f64 - 0.5)
            .clamp(0., frame.height as f64 - 1.);
        let y0 = fy.floor() as usize;
        let y1 = (y0 + 1).min(frame.height as usize - 1);
        let dy = (fy - y0 as f64) as f32;
        for x in 0..SIDE {
            let fx = ((x as f64 + 0.5) * frame.width as f64 / SIDE as f64 - 0.5)
                .clamp(0., frame.width as f64 - 1.);
            let x0 = fx.floor() as usize;
            let x1 = (x0 + 1).min(frame.width as usize - 1);
            let dx = (fx - x0 as f64) as f32;
            let a = frame.pixels[y0 * frame.width as usize + x0];
            let b = frame.pixels[y0 * frame.width as usize + x1];
            let c = frame.pixels[y1 * frame.width as usize + x0];
            let d = frame.pixels[y1 * frame.width as usize + x1];
            for channel in 0..3 {
                let top = a[channel] + (b[channel] - a[channel]) * dx;
                let bottom = c[channel] + (d[channel] - c[channel]) * dx;
                values.push((top + (bottom - top) * dy).clamp(0., 1.) * 2. - 1.);
            }
        }
    }
    Ok(values)
}

fn decode(logits: &[f32]) -> Result<(Vec<f32>, Vec<u8>, Vec<u8>)> {
    if logits.len() != SIDE * SIDE * 6 || logits.iter().any(|v| !v.is_finite()) {
        return Err(failure("模型输出包含无效数值或长度"));
    }
    let mut probabilities = Vec::with_capacity(SIDE * SIDE);
    let mut categories = Vec::with_capacity(SIDE * SIDE);
    let mut weights = Vec::with_capacity(SIDE * SIDE);
    for pixel in logits.chunks_exact(6) {
        let maximum = pixel.iter().copied().fold(f32::NEG_INFINITY, f32::max);
        let exps: [f32; 6] = std::array::from_fn(|i| (pixel[i] - maximum).exp());
        let p = exps[3] / exps.iter().sum::<f32>();
        // First maximum wins ties, matching numpy.argmax / reference runtime.
        let category = pixel.iter().position(|v| *v == maximum).unwrap() as u8;
        probabilities.push(p);
        categories.push(category);
        let t = if category == 3 {
            ((p - 0.55) / 0.30).clamp(0., 1.)
        } else {
            0.
        };
        weights.push((t * t * (3. - 2. * t) * 255.).round() as u8);
    }
    // One model-pixel conservative erosion before bilinear upsampling. This
    // avoids extending the confident interior into adjacent hair/clothes.
    // Empty/low confidence stays empty, never replaced by a geometric mask.
    let raw = weights.clone();
    for y in 0..SIDE {
        for x in 0..SIDE {
            let mut minimum = 255;
            for yy in y.saturating_sub(1)..=(y + 1).min(SIDE - 1) {
                for xx in x.saturating_sub(1)..=(x + 1).min(SIDE - 1) {
                    minimum = minimum.min(raw[yy * SIDE + xx]);
                }
            }
            weights[y * SIDE + x] = minimum;
        }
    }
    Ok((probabilities, categories, weights))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn input_is_rgb_minus_one_to_one_with_non_square_pixel_centres() {
        let mut frame = SrgbFrame {
            width: 2,
            height: 1,
            pixels: vec![[1., 0., 0.], [0., 0., 1.]],
        };
        let input = prepare(&frame).unwrap();
        assert_eq!(&input[..3], &[1., -1., -1.]);
        assert_eq!(&input[input.len() - 3..], &[-1., -1., 1.]);
        let x = SIDE / 2;
        assert!((input[x * 3] + input[x * 3 + 2]).abs() < 1e-6);
        for row in input.chunks_exact(SIDE * 3) {
            assert_eq!(row, &input[..SIDE * 3]);
        }
        frame.pixels[0][0] = f32::NAN;
        assert!(prepare(&frame).is_err());
        frame.pixels.clear();
        assert!(prepare(&frame).is_err());
    }
    #[test]
    fn stable_softmax_rejects_uncertainty_non_face_classes_and_nonfinite() {
        let mut logits = vec![10000.; SIDE * SIDE * 6];
        assert!(decode(&logits).unwrap().2.iter().all(|v| *v == 0));
        for pixel in logits.chunks_exact_mut(6) {
            pixel[3] += 10.;
        }
        let (p, c, w) = decode(&logits).unwrap();
        assert!(p.iter().all(|v| *v > 0.999));
        assert!(c.iter().all(|v| *v == 3));
        assert!(w.iter().all(|v| *v == 255));
        for class in [0, 1, 2, 4, 5] {
            let i = (SIDE * 20 + class * 10) * 6;
            logits[i + class] += 20.;
        }
        let (_, _, w) = decode(&logits).unwrap();
        for class in [0, 1, 2, 4, 5] {
            for dx in 0..=1 {
                assert_eq!(w[SIDE * 20 + class * 10 + dx], 0);
            }
        }
        logits[0] = f32::INFINITY;
        assert!(decode(&logits).is_err());
        assert!(decode(&[]).is_err());
    }
    #[test]
    fn unknown_or_relative_weights_are_rejected_before_native_loading() {
        assert!(Segmenter::open(Path::new("relative"), Path::new("relative")).is_err());
        let temp = tempfile::tempdir().unwrap();
        std::fs::write(temp.path().join(MODEL_NAME), b"arbitrary graph").unwrap();
        assert!(Segmenter::open(temp.path(), temp.path()).is_err());
    }
}
