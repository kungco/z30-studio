//! Local YuNet face boxes and five landmarks, never identity recognition.
use ort::{
    session::{Session, SessionOutputs},
    value::Tensor,
};
use serde::Serialize;
use sha2::{Digest, Sha256};
#[cfg(any(windows, test))]
use std::path::PathBuf;
use std::{
    fs,
    io::Read,
    path::Path,
    sync::atomic::{AtomicBool, Ordering},
    time::Instant,
};
pub use z30_core::portrait::{Face, Point};
use z30_core::{Error, Result, develop::SrgbFrame};

pub const SIDE: usize = 640;
pub const MODEL_NAME: &str = "face_detection_yunet_2023mar.onnx";
const MODEL_BYTES: u64 = 232589;
const MODEL_HASH: &str = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4";
const RUNTIME_BYTES: u64 = 17253176;
const RUNTIME_HASH: &str = "0c42dc4e278f0968fdf9ebf1b1e54a74c657a9942e70803e453c91993451cf4c";
const MAX_FACES: usize = 16;
const CONFIDENCE: f32 = 0.9;

#[derive(Debug, Serialize)]
pub struct Detection {
    pub source_width: u32,
    pub source_height: u32,
    pub faces: Vec<Face>,
    pub prepare_seconds: f64,
    pub inference_seconds: f64,
    pub postprocess_seconds: f64,
    pub local_only: bool,
}

fn failure(error: impl std::fmt::Display) -> Error {
    Error::Policy(format!("本机人像检测失败：{error}"))
}
fn check_cancel(cancel: &AtomicBool) -> Result<()> {
    if cancel.load(Ordering::Acquire) {
        Err(Error::Policy("人像检测已取消".into()))
    } else {
        Ok(())
    }
}
pub(crate) fn verified_bytes(path: &Path, size: u64, hash: &str) -> Result<Vec<u8>> {
    if !path.is_absolute() || !path.is_file() || path.metadata()?.len() != size {
        return Err(Error::Policy(format!(
            "人像组件缺失或大小不符：{}",
            path.file_name().unwrap_or_default().to_string_lossy()
        )));
    }
    let mut bytes = Vec::with_capacity(size as usize);
    fs::File::open(path)?
        .take(size + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 != size || format!("{:x}", Sha256::digest(&bytes)) != hash {
        return Err(Error::Policy("人像组件 SHA-256 校验失败，未加载".into()));
    }
    Ok(bytes)
}

#[cfg(windows)]
fn runtime_environment(path: &Path) -> Result<ort::environment::Environment> {
    use std::sync::{Mutex, OnceLock};
    struct Runtime {
        path: PathBuf,
        environment: ort::environment::Environment,
        _library: libloading::os::windows::Library,
    }
    static RUNTIME: OnceLock<Mutex<Option<Runtime>>> = OnceLock::new();
    let mut runtime = RUNTIME
        .get_or_init(|| Mutex::new(None))
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let path = path.canonicalize()?;
    if let Some(loaded) = runtime.as_ref() {
        if loaded.path != path {
            return Err(Error::Policy(
                "进程已使用另一人像运行库，请重启后切换".into(),
            ));
        }
        return Ok(loaded.environment.clone());
    }
    // An explicit verified DLL, with dependencies restricted to System32.
    // No process current-directory/PATH dependency search or registry change.
    let library = unsafe { libloading::os::windows::Library::load_with_flags(&path, 0x00000800) }
        .map_err(|e| {
        failure(format!(
            "无法加载已校验的运行库（需要本机 Microsoft VC++ x64 运行库）：{e}"
        ))
    })?;
    let environment = ort::init_from(&path)
        .map_err(failure)?
        .with_telemetry(false)
        .build()
        .map_err(failure)?;
    *runtime = Some(Runtime {
        path,
        environment: environment.clone(),
        _library: library,
    });
    Ok(environment)
}

pub(crate) fn open_session(directory: &Path, model: &[u8]) -> Result<Session> {
    if !directory.is_absolute() {
        return Err(Error::Policy("人像组件目录必须为绝对路径".into()));
    }
    let runtime = directory.join("onnxruntime.dll");
    #[cfg(windows)]
    let _runtime_read_lock = {
        use std::os::windows::fs::OpenOptionsExt;
        fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(&runtime)?
    };
    let runtime_bytes = verified_bytes(&runtime, RUNTIME_BYTES, RUNTIME_HASH)?;
    drop(runtime_bytes);
    #[cfg(windows)]
    {
        let environment = runtime_environment(&runtime)?;
        let session = Session::builder(&environment)
            .map_err(failure)?
            .with_execution_providers([ort::ep::CPU::default().build()])
            .map_err(failure)?
            .with_intra_threads(2)
            .map_err(failure)?
            .with_inter_threads(1)
            .map_err(failure)?
            .with_parallel_execution(false)
            .map_err(failure)?
            .commit_from_memory(model)
            .map_err(failure)?;
        Ok(session)
    }
    #[cfg(not(windows))]
    {
        let _ = model;
        Err(Error::Policy(
            "此固定人像运行库仅接受 Windows x64 验证".into(),
        ))
    }
}

pub struct Detector {
    session: Session,
}
impl Detector {
    /// This accepts only the pinned assets, not arbitrary user ONNX graphs.
    /// No download or inference happens while the base editor starts.
    pub fn open(directory: &Path) -> Result<Self> {
        if !directory.is_absolute() {
            return Err(Error::Policy("人像组件目录必须为绝对路径".into()));
        }
        let model = verified_bytes(&directory.join(MODEL_NAME), MODEL_BYTES, MODEL_HASH)?;
        Ok(Self {
            session: open_session(directory, &model)?,
        })
    }
    pub fn detect(&mut self, frame: &SrgbFrame, cancel: &AtomicBool) -> Result<Detection> {
        check_cancel(cancel)?;
        let started = Instant::now();
        let prepared = prepare(frame)?;
        let prepare_seconds = started.elapsed().as_secs_f64();
        check_cancel(cancel)?;
        let started = Instant::now();
        let tensor =
            Tensor::from_array(([1usize, 3, SIDE, SIDE], prepared.values)).map_err(failure)?;
        let outputs = self
            .session
            .run(ort::inputs!["input" => tensor])
            .map_err(failure)?;
        let inference_seconds = started.elapsed().as_secs_f64();
        check_cancel(cancel)?;
        let started = Instant::now();
        let mut scales = Vec::new();
        for stride in [8, 16, 32] {
            let rows = (SIDE / stride).pow(2);
            scales.push(Scale {
                stride,
                cls: output(&outputs, &format!("cls_{stride}"), rows, 1)?,
                obj: output(&outputs, &format!("obj_{stride}"), rows, 1)?,
                bbox: output(&outputs, &format!("bbox_{stride}"), rows, 4)?,
                kps: output(&outputs, &format!("kps_{stride}"), rows, 10)?,
            });
        }
        let faces = decode(&scales, prepared.width, prepared.height)?;
        Ok(Detection {
            source_width: frame.width,
            source_height: frame.height,
            faces,
            prepare_seconds,
            inference_seconds,
            postprocess_seconds: started.elapsed().as_secs_f64(),
            local_only: true,
        })
    }
}

struct Prepared {
    values: Vec<f32>,
    width: usize,
    height: usize,
}
fn prepare(frame: &SrgbFrame) -> Result<Prepared> {
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
        return Err(Error::Policy(
            "人像输入必须为有效的定向浮点 sRGB 图像".into(),
        ));
    }
    let ratio = SIDE as f64 / frame.width.max(frame.height) as f64;
    let width = (frame.width as f64 * ratio).round().clamp(1., SIDE as f64) as usize;
    let height = (frame.height as f64 * ratio).round().clamp(1., SIDE as f64) as usize;
    let mut values = vec![0.; 3 * SIDE * SIDE];
    for y in 0..height {
        let fy = ((y as f64 + 0.5) * frame.height as f64 / height as f64 - 0.5)
            .clamp(0., frame.height as f64 - 1.);
        let y0 = fy.floor() as usize;
        let y1 = (y0 + 1).min(frame.height as usize - 1);
        let dy = (fy - y0 as f64) as f32;
        for x in 0..width {
            let fx = ((x as f64 + 0.5) * frame.width as f64 / width as f64 - 0.5)
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
                values[(2 - channel) * SIDE * SIDE + y * SIDE + x] =
                    (top + (bottom - top) * dy).clamp(0., 1.) * 255.;
            }
        }
    }
    Ok(Prepared {
        values,
        width,
        height,
    })
}

fn output<'a>(
    outputs: &'a SessionOutputs<'_>,
    name: &str,
    rows: usize,
    columns: usize,
) -> Result<&'a [f32]> {
    let value = outputs
        .get(name)
        .ok_or_else(|| failure(format!("模型缺少 {name} 输出")))?;
    let (shape, values) = value.try_extract_tensor::<f32>().map_err(failure)?;
    if shape.len() != 3
        || shape[0] != 1
        || shape[1] != rows as i64
        || shape[2] != columns as i64
        || values.len() != rows * columns
        || values.iter().any(|v| !v.is_finite())
    {
        return Err(failure("模型输出尺寸/数值不符合固定 YuNet 合约"));
    }
    Ok(values)
}

struct Scale<'a> {
    stride: usize,
    cls: &'a [f32],
    obj: &'a [f32],
    bbox: &'a [f32],
    kps: &'a [f32],
}
fn decode(scales: &[Scale<'_>], width: usize, height: usize) -> Result<Vec<Face>> {
    if width == 0 || height == 0 || width > SIDE || height > SIDE {
        return Err(failure("缩放几何无效"));
    }
    let mut candidates = Vec::new();
    for scale in scales {
        if ![8, 16, 32].contains(&scale.stride) {
            return Err(failure("模型步长无效"));
        }
        let grid = SIDE / scale.stride;
        let count = grid * grid;
        if scale.cls.len() != count
            || scale.obj.len() != count
            || scale.bbox.len() != count * 4
            || scale.kps.len() != count * 10
            || [scale.cls, scale.obj, scale.bbox, scale.kps]
                .iter()
                .any(|data| data.iter().any(|v| !v.is_finite()))
        {
            return Err(failure("候选张量无效"));
        }
        for index in 0..count {
            let confidence =
                (scale.cls[index].clamp(0., 1.) * scale.obj[index].clamp(0., 1.)).sqrt();
            if confidence < CONFIDENCE {
                continue;
            }
            let x = (index % grid) as f32;
            let y = (index / grid) as f32;
            let stride = scale.stride as f32;
            let box_values = &scale.bbox[index * 4..index * 4 + 4];
            let cx = (x + box_values[0]) * stride;
            let cy = (y + box_values[1]) * stride;
            let w = box_values[2].exp() * stride;
            let h = box_values[3].exp() * stride;
            if ![cx, cy, w, h].iter().all(|v| v.is_finite())
                || cx < 0.
                || cy < 0.
                || cx >= width as f32
                || cy >= height as f32
                || w < 4.
                || h < 4.
            {
                continue;
            }
            let left = (cx - w * 0.5).clamp(0., width as f32) / width as f32;
            let top = (cy - h * 0.5).clamp(0., height as f32) / height as f32;
            let right = (cx + w * 0.5).clamp(0., width as f32) / width as f32;
            let bottom = (cy + h * 0.5).clamp(0., height as f32) / height as f32;
            if right <= left || bottom <= top {
                continue;
            }
            let mut landmarks = [Point { x: 0., y: 0. }; 5];
            for (point, landmark) in landmarks.iter_mut().enumerate() {
                landmark.x =
                    ((x + scale.kps[index * 10 + point * 2]) * stride / width as f32).clamp(0., 1.);
                landmark.y = ((y + scale.kps[index * 10 + point * 2 + 1]) * stride / height as f32)
                    .clamp(0., 1.);
            }
            candidates.push(Face {
                rect: [left, top, right - left, bottom - top],
                landmarks,
                confidence,
            });
        }
    }
    candidates.sort_by(|a, b| b.confidence.total_cmp(&a.confidence));
    candidates.truncate(5000);
    let mut selected: Vec<Face> = Vec::new();
    for face in candidates {
        if selected
            .iter()
            .all(|other| overlap(&face.rect, &other.rect) < 0.3)
        {
            selected.push(face);
            if selected.len() == MAX_FACES {
                break;
            }
        }
    }
    Ok(selected)
}
fn overlap(a: &[f32; 4], b: &[f32; 4]) -> f32 {
    let width = (a[0] + a[2]).min(b[0] + b[2]) - a[0].max(b[0]);
    let height = (a[1] + a[3]).min(b[1] + b[3]) - a[1].max(b[1]);
    let intersection = width.max(0.) * height.max(0.);
    intersection / (a[2] * a[3] + b[2] * b[3] - intersection).max(1e-12)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn installation_manifest_matches_the_loader_and_actual_model_shape() {
        let manifest: serde_json::Value =
            serde_json::from_str(include_str!("../../../vendor/vision/manifest.json")).unwrap();
        assert_eq!(manifest["model"]["name"], MODEL_NAME);
        assert_eq!(manifest["model"]["bytes"], MODEL_BYTES);
        assert_eq!(manifest["model"]["sha256"], MODEL_HASH);
        assert_eq!(manifest["runtime"]["bytes"], RUNTIME_BYTES);
        assert_eq!(manifest["runtime"]["sha256"], RUNTIME_HASH);
        assert_eq!(manifest["input"]["width"], SIDE);
        assert_eq!(manifest["input"]["height"], SIDE);
    }
    #[test]
    fn preprocess_preserves_aspect_and_uses_bgr_without_gamma_or_metadata() {
        let frame = SrgbFrame {
            width: 2,
            height: 1,
            pixels: vec![[1., 0., 0.], [0., 0., 1.]],
        };
        let prepared = prepare(&frame).unwrap();
        assert_eq!((prepared.width, prepared.height), (640, 320));
        assert_eq!(prepared.values[0], 0.);
        assert_eq!(prepared.values[2 * SIDE * SIDE], 255.);
        assert_eq!(prepared.values[SIDE - 1], 255.);
        assert_eq!(prepared.values[320 * SIDE], 0.);
        let invalid = SrgbFrame {
            width: 1,
            height: 1,
            pixels: vec![[f32::NAN, 0., 0.]],
        };
        assert!(prepare(&invalid).is_err());
    }
    #[test]
    fn geometry_and_landmarks_use_rows_then_columns_and_suppress_duplicates() {
        let count = (SIDE / 8).pow(2);
        let mut cls = vec![0.; count];
        let mut obj = vec![0.; count];
        let mut bbox = vec![0.; count * 4];
        let mut kps = vec![0.; count * 10];
        for (x, score) in [(5usize, 1.), (6, 0.95)] {
            let index = 6 * (SIDE / 8) + x;
            cls[index] = score;
            obj[index] = score;
            bbox[index * 4] = 5.5 - x as f32;
            bbox[index * 4 + 1] = 0.5;
            bbox[index * 4 + 2] = 2f32.ln();
            bbox[index * 4 + 3] = 3f32.ln();
            kps[index * 10] = 0.25;
            kps[index * 10 + 1] = 0.75;
        }
        let faces = decode(
            &[Scale {
                stride: 8,
                cls: &cls,
                obj: &obj,
                bbox: &bbox,
                kps: &kps,
            }],
            640,
            320,
        )
        .unwrap();
        assert_eq!(faces.len(), 1);
        assert!((faces[0].rect[0] - 36. / 640.).abs() < 1e-6);
        assert!((faces[0].rect[1] - 40. / 320.).abs() < 1e-6);
        assert!((faces[0].landmarks[0].x - 42. / 640.).abs() < 1e-6);
        assert!((faces[0].landmarks[0].y - 54. / 320.).abs() < 1e-6);
        cls[0] = f32::NAN;
        assert!(
            decode(
                &[Scale {
                    stride: 8,
                    cls: &cls,
                    obj: &obj,
                    bbox: &bbox,
                    kps: &kps
                }],
                640,
                320
            )
            .is_err()
        );
    }
    #[test]
    fn missing_relative_or_changed_assets_are_rejected_before_loading_native_code() {
        assert!(Detector::open(Path::new("relative")).is_err());
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join(MODEL_NAME), b"fake").unwrap();
        assert!(Detector::open(root.path()).is_err());
        let fixture = root.path().join("hash.bin");
        fs::write(&fixture, b"abc").unwrap();
        assert!(verified_bytes(&fixture, 3, "not a sha256").is_err());
    }
    #[test]
    #[ignore = "Requires explicit Z30_VISION_DIR and Z30_VISION_IMAGE; no automatic model download"]
    fn real_pinned_cpu_model_on_an_explicit_public_photo() {
        let directory = PathBuf::from(std::env::var_os("Z30_VISION_DIR").expect("Z30_VISION_DIR"));
        let image = PathBuf::from(std::env::var_os("Z30_VISION_IMAGE").expect("Z30_VISION_IMAGE"));
        let decoded = z30_core::raster::decode(&image).unwrap();
        let frame = z30_core::develop::render(&decoded.frame, &Default::default()).unwrap();
        let mut detector = Detector::open(&directory).unwrap();
        let result = detector.detect(&frame, &AtomicBool::new(false)).unwrap();
        assert!(!result.faces.is_empty() && result.faces.len() <= MAX_FACES);
        assert!(
            result
                .faces
                .iter()
                .all(|face| face.confidence >= CONFIDENCE)
        );
        let cancelled = AtomicBool::new(true);
        assert!(detector.detect(&frame, &cancelled).is_err());
    }
}
