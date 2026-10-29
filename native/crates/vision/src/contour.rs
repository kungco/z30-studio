//! Pinned MediaPipe 468-point mesh, used only for explicit local proposals.
//! Face boxes use image-edge normalization; returned points/pins use the
//! editor's pixel-centre normalization. Model coordinates are crop pixels.
use crate::detector::{Face, open_session, verified_bytes};
use ort::{session::Session, value::Tensor};
use serde::Serialize;
use std::{path::Path, time::Instant};
use z30_core::{
    Error, Result,
    develop::SrgbFrame,
    warp::{Map, Pin},
};

pub const SIDE: usize = 192;
pub const MODEL_NAME: &str = "face_mesh_Nx3x192x192.onnx";
pub const MODEL_BYTES: u64 = 2466068;
pub const MODEL_HASH: &str = "3ca77cf59c18e4da0eccb46695bf604683fa564253e3385892981a5c274fb10f";
// MediaPipe FACEMESH_FACE_OVAL, with the closing vertex implicit.
pub const OVAL: [usize; 36] = [
    10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152,
    148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109,
];

fn failure(e: impl std::fmt::Display) -> Error {
    Error::Policy(format!("面部轮廓分析：{e}"))
}
fn check(cancelled: &mut impl FnMut() -> bool) -> Result<()> {
    if cancelled() {
        Err(failure("已取消"))
    } else {
        Ok(())
    }
}
#[derive(Clone, Copy, Debug, Serialize)]
pub struct Roi {
    pub centre: [f64; 2],
    pub side: f64,
    pub rotation: f64,
}
impl Roi {
    pub fn from_face(face: &Face, width: u32, height: u32) -> Result<Self> {
        face.validate()?;
        if width < 2
            || height < 2
            || width as u64 * height as u64 > 80_000_000
            || face.confidence < 0.9
        {
            return Err(failure("面部尺寸或置信度不足"));
        }
        let w = width as f64;
        let h = height as f64;
        if face.rect[2] as f64 * w < 64. || face.rect[3] as f64 * h < 64. {
            return Err(failure("预览中的面部至少需要64像素"));
        }
        let mut eyes = face.landmarks[..2].to_vec();
        eyes.sort_by(|a, b| a.x.total_cmp(&b.x));
        let dx = (eyes[1].x - eyes[0].x) as f64 * w;
        let dy = (eyes[1].y - eyes[0].y) as f64 * h;
        let rotation = dy.atan2(dx);
        if dx.hypot(dy) < 12. || rotation.abs() > std::f64::consts::FRAC_PI_4 {
            return Err(failure("眼线或姿态不适合保守修形"));
        }
        Ok(Self {
            centre: [
                (face.rect[0] as f64 + face.rect[2] as f64 / 2.) * w,
                (face.rect[1] as f64 + face.rect[3] as f64 / 2.) * h,
            ],
            side: (face.rect[2] as f64 * w).max(face.rect[3] as f64 * h) * 1.5,
            rotation,
        })
    }
    fn project(self, x: f64, y: f64) -> [f64; 2] {
        let (s, c) = self.rotation.sin_cos();
        let a = (x / SIDE as f64 - 0.5) * self.side;
        let b = (y / SIDE as f64 - 0.5) * self.side;
        [
            self.centre[0] + c * a - s * b,
            self.centre[1] + s * a + c * b,
        ]
    }
}

#[derive(Debug, Serialize)]
pub struct Mesh {
    pub width: u32,
    pub height: u32,
    pub presence: f64,
    pub points: Vec<[f64; 2]>,
    pub roi: Roi,
    pub prepare_seconds: f64,
    pub inference_seconds: f64,
}
pub struct Landmarker {
    session: Session,
}
impl Landmarker {
    pub fn open(runtime: &Path, model: &Path) -> Result<Self> {
        let bytes = verified_bytes(&model.join(MODEL_NAME), MODEL_BYTES, MODEL_HASH)?;
        Ok(Self {
            session: open_session(runtime, &bytes)?,
        })
    }
    pub fn infer(
        &mut self,
        frame: &SrgbFrame,
        face: &Face,
        mut cancelled: impl FnMut() -> bool,
    ) -> Result<Mesh> {
        check(&mut cancelled)?;
        let started = Instant::now();
        let roi = Roi::from_face(face, frame.width, frame.height)?;
        let values = prepare(frame, roi)?;
        let prepare_seconds = started.elapsed().as_secs_f64();
        check(&mut cancelled)?;
        let tensor = Tensor::from_array(([1usize, 3, SIDE, SIDE], values)).map_err(failure)?;
        let started = Instant::now();
        let out = self
            .session
            .run(ort::inputs!["input"=>tensor])
            .map_err(failure)?;
        let inference_seconds = started.elapsed().as_secs_f64();
        check(&mut cancelled)?;
        let (shape, points) = out
            .get("landmarks")
            .ok_or_else(|| failure("缺少landmarks"))?
            .try_extract_tensor::<f32>()
            .map_err(failure)?;
        let (score_shape, score) = out
            .get("score")
            .ok_or_else(|| failure("缺少score"))?
            .try_extract_tensor::<f32>()
            .map_err(failure)?;
        if &shape[..] != [1, 468, 3] || &score_shape[..] != [1, 1] {
            return Err(failure("模型输出维度不符"));
        }
        let mut mesh = decode(points, score[0], roi, frame.width, frame.height)?;
        mesh.prepare_seconds = prepare_seconds;
        mesh.inference_seconds = inference_seconds;
        check(&mut cancelled)?;
        Ok(mesh)
    }
}
pub fn prepare(frame: &SrgbFrame, roi: Roi) -> Result<Vec<f32>> {
    if frame.width < 2
        || frame.height < 2
        || frame.pixels.len() as u64 != frame.width as u64 * frame.height as u64
        || frame.pixels.len() > 80_000_000
        || frame
            .pixels
            .iter()
            .flatten()
            .any(|v| !v.is_finite() || !(-1e-6..=1.000001).contains(v))
        || !roi.side.is_finite()
        || roi.side <= 0.
        || roi.side > 4. * frame.width.max(frame.height) as f64
        || !roi.rotation.is_finite()
        || roi.rotation.abs() > std::f64::consts::PI
        || roi.centre.iter().any(|v| !v.is_finite())
        || roi.centre[0].abs() > frame.width as f64 * 3.
        || roi.centre[1].abs() > frame.height as f64 * 3.
    {
        return Err(failure("输入图像或裁剪区域无效"));
    }
    let mut values = vec![0.; 3 * SIDE * SIDE];
    for y in 0..SIDE {
        for x in 0..SIDE {
            let q = roi.project(x as f64 + 0.5, y as f64 + 0.5);
            let fx = q[0] - 0.5;
            let fy = q[1] - 0.5;
            let x0 = fx.floor() as i64;
            let y0 = fy.floor() as i64;
            let dx = (fx - x0 as f64) as f32;
            let dy = (fy - y0 as f64) as f32;
            // ImageToTensor's zero border; never clamp an out-of-frame face into view.
            let at = |xx: i64, yy: i64, k: usize| {
                if xx >= 0 && yy >= 0 && xx < frame.width as i64 && yy < frame.height as i64 {
                    frame.pixels[yy as usize * frame.width as usize + xx as usize][k]
                } else {
                    0.
                }
            };
            for k in 0..3 {
                let a = at(x0, y0, k);
                let b = at(x0 + 1, y0, k);
                let c = at(x0, y0 + 1, k);
                let d = at(x0 + 1, y0 + 1, k);
                let top = a + (b - a) * dx;
                let bottom = c + (d - c) * dx;
                values[k * SIDE * SIDE + y * SIDE + x] = (top + (bottom - top) * dy).clamp(0., 1.);
            }
        }
    }
    Ok(values)
}
pub fn decode(raw: &[f32], logit: f32, roi: Roi, width: u32, height: u32) -> Result<Mesh> {
    if raw.len() != 468 * 3
        || !logit.is_finite()
        || raw.iter().any(|v| !v.is_finite())
        || width < 2
        || height < 2
        || !roi.side.is_finite()
        || roi.side <= 0.
        || !roi.rotation.is_finite()
        || roi.centre.iter().any(|v| !v.is_finite())
    {
        return Err(failure("模型坐标无效"));
    }
    let presence = 1. / (1. + (-(logit as f64)).exp());
    if presence < 0.9 {
        return Err(failure("轮廓存在置信度不足，未提出修形建议"));
    }
    let mut points = Vec::with_capacity(468);
    for p in raw.chunks_exact(3) {
        let q = roi.project(p[0] as f64, p[1] as f64);
        let v = [
            (q[0] - 0.5) / (width - 1) as f64,
            (q[1] - 0.5) / (height - 1) as f64,
        ];
        if v.iter().any(|x| !x.is_finite() || !(0.0..=1.0).contains(x)) {
            return Err(failure("面部轮廓被画面截断，未提出修形建议"));
        }
        points.push(v);
    }
    Ok(Mesh {
        width,
        height,
        presence,
        points,
        roi,
        prepare_seconds: 0.,
        inference_seconds: 0.,
    })
}
/// Four compact, inward cheek/jaw fields. Eyes, nose and mouth are outside
/// every support disc. Both preview and original dimensions must be valid.
pub fn propose(mesh: &Mesh, full: (u32, u32)) -> Result<Vec<Pin>> {
    if mesh.points.len() != 468
        || !mesh.presence.is_finite()
        || !(0.9..=1.).contains(&mesh.presence)
        || mesh.width < 2
        || mesh.height < 2
        || mesh
            .points
            .iter()
            .flatten()
            .any(|v| !v.is_finite() || !(0.0..=1.0).contains(v))
    {
        return Err(failure("轮廓数据不完整"));
    }
    let w = (mesh.width - 1) as f64;
    let h = (mesh.height - 1) as f64;
    let min = mesh.width.min(mesh.height) as f64;
    let at = |i: usize| [mesh.points[i][0] * w, mesh.points[i][1] * h];
    let mean = |a: usize, b: usize| {
        let p = at(a);
        let q = at(b);
        [(p[0] + q[0]) / 2., (p[1] + q[1]) / 2.]
    };
    let left = mean(33, 133);
    let right = mean(263, 362);
    let eye = [right[0] - left[0], right[1] - left[1]];
    let distance = eye[0].hypot(eye[1]);
    if distance < 12. {
        return Err(failure("有效眼距不足"));
    }
    let u = [eye[0] / distance, eye[1] / distance];
    let mid = [(left[0] + right[0]) / 2., (left[1] + right[1]) / 2.];
    let horizontal = |p: [f64; 2]| (p[0] - mid[0]) * u[0] + (p[1] - mid[1]) * u[1];
    if horizontal(at(1)).abs() > distance * 0.22 {
        return Err(failure("侧脸姿态超出保守修形范围"));
    }
    let protected = [33, 133, 263, 362, 1, 61, 291, 13, 14];
    let mut pins = Vec::new();
    for (a, b) in [(132, 361), (172, 397)] {
        let p = at(a);
        let q = at(b);
        let extent = (horizontal(p) - horizontal(q)).abs();
        if horizontal(p) * horizontal(q) >= 0. || extent < distance || extent > distance * 3.5 {
            return Err(failure("双侧下颌几何不确定"));
        }
        for from in [p, q] {
            let direction = if horizontal(from) < 0. { 1. } else { -1. };
            let clear = protected
                .iter()
                .map(|&i| {
                    let p = at(i);
                    (p[0] - from[0]).hypot(p[1] - from[1])
                })
                .fold(f64::INFINITY, f64::min);
            let edge = from[0].min(from[1]).min(w - from[0]).min(h - from[1]);
            let radius = (extent * 0.22)
                .min(clear * 0.55)
                .min(edge * 0.85)
                .min(min * 0.2);
            if radius < 4. || radius / min < 0.002 {
                return Err(failure("下颌与五官或画面边缘距离不足"));
            }
            let to = [
                from[0] + direction * u[0] * radius * 0.075,
                from[1] + direction * u[1] * radius * 0.075,
            ];
            pins.push(Pin {
                from: [from[0] / w, from[1] / h],
                to: [to[0] / w, to[1] / h],
                radius: radius / min,
            });
        }
    }
    Map::new(mesh.width, mesh.height, &pins)?;
    Map::new(full.0, full.1, &pins)?;
    Ok(pins)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn face() -> Face {
        Face {
            rect: [0.2, 0.2, 0.6, 0.6],
            landmarks: [
                crate::detector::Point { x: 0.35, y: 0.4 },
                crate::detector::Point { x: 0.65, y: 0.4 },
                crate::detector::Point { x: 0.5, y: 0.5 },
                crate::detector::Point { x: 0.4, y: 0.65 },
                crate::detector::Point { x: 0.6, y: 0.65 },
            ],
            confidence: 0.99,
        }
    }
    #[test]
    fn roi_eye_alignment_size_and_pose() {
        let mut f = face();
        let r = Roi::from_face(&f, 200, 100).unwrap_err();
        assert!(r.to_string().contains("64"));
        let r = Roi::from_face(&f, 400, 300).unwrap();
        assert!(r.rotation.abs() < 1e-10);
        assert!((r.side - 360.).abs() < 1e-4);
        f.landmarks.swap(0, 1);
        assert_eq!(Roi::from_face(&f, 400, 300).unwrap().rotation, r.rotation);
        f.landmarks[0] = f.landmarks[1];
        assert!(Roi::from_face(&f, 400, 300).is_err());
    }
    #[test]
    fn rgb_planar_centres_and_zero_border() {
        let f = SrgbFrame {
            width: 2,
            height: 2,
            pixels: vec![[1., 0., 0.]; 4],
        };
        let r = Roi {
            centre: [1., 1.],
            side: 2.,
            rotation: 0.,
        };
        let a = prepare(&f, r).unwrap();
        assert_eq!(a[SIDE * SIDE / 2 + SIDE / 2], 1.);
        assert!(a[SIDE * SIDE..].iter().all(|&v| v == 0.));
        let r = Roi {
            centre: [-4., -4.],
            ..r
        };
        assert!(prepare(&f, r).unwrap().iter().all(|&v| v == 0.));
        assert!(prepare(&f, Roi { side: 1e100, ..r }).is_err());
    }
    #[test]
    fn decode_crop_pixels_to_editor_centres() {
        let r = Roi {
            centre: [100.5, 120.5],
            side: 96.,
            rotation: 0.,
        };
        let raw = vec![96.; 468 * 3];
        let m = decode(&raw, 20., r, 201, 241).unwrap();
        assert_eq!(m.points[0], [0.5, 0.5]);
        let mut raw = raw;
        raw[0] = f32::NAN;
        assert!(decode(&raw, 20., r, 201, 241).is_err());
        assert!(decode(&[], 20., r, 201, 241).is_err());
        assert!(decode(&vec![96.; 1404], -5., r, 201, 241).is_err());
    }
    #[test]
    fn cancelled_before_model_or_assets() {
        let mut c = || true;
        assert!(check(&mut c).is_err());
        assert!(Landmarker::open(Path::new("relative"), Path::new("relative")).is_err());
    }
    fn mesh() -> Mesh {
        let mut points = vec![[0.5, 0.5]; 468];
        for (i, p) in [
            (33, [0.4, 0.35]),
            (133, [0.45, 0.35]),
            (263, [0.6, 0.35]),
            (362, [0.55, 0.35]),
            (1, [0.5, 0.5]),
            (61, [0.44, 0.6]),
            (291, [0.56, 0.6]),
            (13, [0.5, 0.58]),
            (14, [0.5, 0.62]),
            (132, [0.3, 0.55]),
            (361, [0.7, 0.55]),
            (172, [0.35, 0.7]),
            (397, [0.65, 0.7]),
        ] {
            points[i] = p;
        }
        Mesh {
            width: 1001,
            height: 801,
            presence: 0.99,
            points,
            roi: Roi {
                centre: [500., 400.],
                side: 500.,
                rotation: 0.,
            },
            prepare_seconds: 0.,
            inference_seconds: 0.,
        }
    }
    #[test]
    fn inward_fields_leave_protected_features_and_background_exact() {
        let m = mesh();
        let pins = propose(&m, (6001, 4801)).unwrap();
        assert_eq!(pins.len(), 4);
        let map = Map::new(m.width, m.height, &pins).unwrap();
        for p in &pins {
            assert!((p.to[0] - 0.5).abs() < (p.from[0] - 0.5).abs());
            assert_eq!(p.to[1], p.from[1]);
            let from = map.source_at(p.to[0] * 1000., p.to[1] * 800.).unwrap();
            assert!((from[0] - p.from[0] * 1000.).abs() < 1e-9);
        }
        for i in [33, 133, 263, 362, 1, 61, 291, 13, 14] {
            let p = [m.points[i][0] * 1000., m.points[i][1] * 800.];
            assert_eq!(map.source_at(p[0], p[1]).unwrap(), p);
        }
        for p in [[0., 0.], [1000., 800.], [1000., 0.]] {
            assert_eq!(map.source_at(p[0], p[1]).unwrap(), p);
        }
    }
    #[test]
    fn no_guess_for_side_pose_uncertainty_or_aspect_mismatch() {
        let mut m = mesh();
        assert!(propose(&m, (8000, 10)).is_err());
        m.points[1] = [0.8, 0.5];
        assert!(propose(&m, (1001, 801)).is_err());
        m = mesh();
        m.presence = 0.5;
        assert!(propose(&m, (1001, 801)).is_err());
        m = mesh();
        m.points[132][0] = f64::NAN;
        assert!(propose(&m, (1001, 801)).is_err());
    }
    #[test]
    fn manifest_and_native_loader_share_exact_asset_contract() {
        let manifest: serde_json::Value =
            serde_json::from_str(include_str!("../../../vendor/contour/manifest.json")).unwrap();
        assert_eq!(manifest["model"]["sha256"], MODEL_HASH);
        assert_eq!(manifest["model"]["bytes"], MODEL_BYTES);
        assert_eq!(manifest["model"]["name"], MODEL_NAME);
        assert_eq!(
            manifest["input"]["shape"],
            serde_json::json!([1, 3, SIDE, SIDE])
        );
    }
    #[test]
    #[ignore = "Requires explicit Z30_VISION_DIR / Z30_CONTOUR_DIR / Z30_VISION_IMAGE; no automatic downloads"]
    fn real_pinned_mesh_on_explicit_public_photo() {
        let dir = |n| std::path::PathBuf::from(std::env::var_os(n).expect(n));
        let runtime = dir("Z30_VISION_DIR");
        let model = dir("Z30_CONTOUR_DIR");
        let image = dir("Z30_VISION_IMAGE");
        let decoded = z30_core::raster::decode(&image).unwrap();
        let frame = z30_core::develop::render(&decoded.frame, &Default::default()).unwrap();
        let faces = crate::detector::Detector::open(&runtime)
            .unwrap()
            .detect(&frame, &std::sync::atomic::AtomicBool::new(false))
            .unwrap();
        let mut engine = Landmarker::open(&runtime, &model).unwrap();
        let mesh = engine
            .infer(
                &frame,
                faces.faces.first().expect("public reference face"),
                || false,
            )
            .unwrap();
        assert_eq!(
            propose(&mesh, (frame.width, frame.height)).unwrap().len(),
            4
        );
        assert!(engine.infer(&frame, &faces.faces[0], || true).is_err());
    }
}
