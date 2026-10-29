//! Conservative compact dark-spot suggestions, not acne/mole classification.
//! Callers supply a semantic/geometric eligibility gate and review the result.
use crate::{Error, Result, frame::Frame, retouch::Repair};
pub const MAX_CANDIDATES: usize = 16;
pub const MAX_ANALYSIS_PIXELS: usize = 1600 * 1600;
#[derive(Clone, Copy, Debug, PartialEq, serde::Serialize)]
pub struct Candidate {
    pub x: f64,
    pub y: f64,
    pub radius: f64,
    pub contrast: f64,
}

fn cancelled(check: &mut impl FnMut() -> bool) -> Result<()> {
    if check() {
        Err(Error::Policy("瑕疵分析已取消".into()))
    } else {
        Ok(())
    }
}
fn buffer<T: Clone>(n: usize, value: T) -> Result<Vec<T>> {
    let mut v = Vec::new();
    v.try_reserve_exact(n)
        .map_err(|_| Error::Policy("瑕疵分析内存不足".into()))?;
    v.resize(n, value);
    Ok(v)
}
pub fn detect(
    frame: &Frame,
    allowed: &[u8],
    existing: &[Repair],
    mut check: impl FnMut() -> bool,
) -> Result<Vec<Candidate>> {
    let (w, h) = (frame.width as usize, frame.height as usize);
    let n = w
        .checked_mul(h)
        .ok_or_else(|| Error::Policy("分析尺寸溢出".into()))?;
    if w < 24
        || h < 24
        || w > 1600
        || h > 1600
        || n > MAX_ANALYSIS_PIXELS
        || frame.pixels.len() != n
        || allowed.len() != n
        || existing.len() > crate::retouch::MAX_REPAIRS
    {
        return Err(Error::Policy(
            "瑕疵分析需要有效的24–1600像素预览及皮肤范围".into(),
        ));
    }
    for p in existing {
        p.validate()?;
    }
    cancelled(&mut check)?;
    let stride = w + 1;
    let mut sum = buffer(stride * (h + 1), 0_f64)?;
    let mut square = buffer(stride * (h + 1), 0_f64)?;
    for y in 0..h {
        if y % 32 == 0 {
            cancelled(&mut check)?;
        }
        let (mut row, mut row2) = (0., 0.);
        for x in 0..w {
            let p = frame.pixels[y * w + x];
            if p.iter().any(|v| !v.is_finite()) {
                return Err(Error::Policy("瑕疵分析遇到非有限像素".into()));
            }
            let v = (0.2126 * p[0] as f64 + 0.7152 * p[1] as f64 + 0.0722 * p[2] as f64)
                .max(1e-6)
                .log2();
            row += v;
            row2 += v * v;
            sum[(y + 1) * stride + x + 1] = sum[y * stride + x + 1] + row;
            square[(y + 1) * stride + x + 1] = square[y * stride + x + 1] + row2;
        }
    }
    let mean = |a: &[f64], x: usize, y: usize, r: usize| {
        let (l, t, rr, b) = (
            x.saturating_sub(r),
            y.saturating_sub(r),
            (x + r + 1).min(w),
            (y + r + 1).min(h),
        );
        (a[b * stride + rr] - a[t * stride + rr] - a[b * stride + l] + a[t * stride + l])
            / ((rr - l) * (b - t)) as f64
    };
    let mut flags = buffer(n, 0_u8)?;
    let mut scores = buffer(n, 0_f32)?;
    for y in 10..h - 10 {
        if y % 32 == 0 {
            cancelled(&mut check)?;
        }
        for x in 10..w - 10 {
            let i = y * w + x;
            if allowed[i] < 230 {
                continue;
            }
            let bg = mean(&sum, x, y, 10);
            let variance = (mean(&square, x, y, 10) - bg * bg).max(0.);
            let foreground = mean(&sum, x, y, 1);
            let contrast = bg - foreground;
            let p = frame.pixels[i];
            let centre = (0.2126 * p[0] as f64 + 0.7152 * p[1] as f64 + 0.0722 * p[2] as f64)
                .max(1e-6)
                .log2();
            // A broad shadow may cross the background threshold only at its
            // centre and mimic a tiny component. Require fine-scale curvature.
            if contrast >= 0.13_f64.max(1.5 * variance.sqrt()) && foreground - centre >= 0.018 {
                flags[i] = 1;
                scores[i] = contrast as f32;
            }
        }
    }
    drop(sum);
    drop(square);
    if !flags.iter().any(|&v| v == 1) {
        cancelled(&mut check)?;
        return Ok(Vec::new());
    }
    let max_side = (frame.width.min(frame.height) as f64 * 0.05).clamp(5., 12.);
    let mut queue: Vec<usize> = Vec::new();
    queue
        .try_reserve_exact(n)
        .map_err(|_| Error::Policy("候选区域内存不足".into()))?;
    let mut found = Vec::<Candidate>::with_capacity(MAX_CANDIDATES);
    for seed in 0..n {
        if flags[seed] != 1 {
            continue;
        }
        cancelled(&mut check)?;
        queue.clear();
        queue.push(seed);
        flags[seed] = 2;
        let (mut count, mut sx, mut sy, mut sxx, mut syy, mut sxy, mut score) =
            (0_usize, 0., 0., 0., 0., 0., 0_f64);
        let (mut l, mut t, mut r, mut b) = (w, h, 0, 0);
        let mut head = 0;
        while head < queue.len() {
            if head % 1024 == 0 {
                cancelled(&mut check)?;
            }
            let i = queue[head];
            head += 1;
            let (x, y) = (i % w, i / w);
            count += 1;
            sx += x as f64;
            sy += y as f64;
            sxx += (x * x) as f64;
            syy += (y * y) as f64;
            sxy += (x * y) as f64;
            score = score.max(scores[i] as f64);
            l = l.min(x);
            r = r.max(x);
            t = t.min(y);
            b = b.max(y);
            for yy in y.saturating_sub(1)..=(y + 1).min(h - 1) {
                for xx in x.saturating_sub(1)..=(x + 1).min(w - 1) {
                    let j = yy * w + xx;
                    if flags[j] == 1 {
                        flags[j] = 2;
                        queue.push(j);
                    }
                }
            }
        }
        if count < 3 || (r - l + 1) as f64 > max_side || (b - t + 1) as f64 > max_side {
            continue;
        }
        let (cx, cy) = (sx / count as f64, sy / count as f64);
        let (xx, yy, xy) = (
            sxx / count as f64 - cx * cx,
            syy / count as f64 - cy * cy,
            sxy / count as f64 - cx * cy,
        );
        let trace = xx + yy;
        let disc = ((xx - yy).powi(2) + 4. * xy * xy).sqrt();
        if trace <= 0. || (trace - disc) / (trace + disc).max(1e-12) < 0.35 {
            continue;
        }
        let radius = ((r - l + 1).max(b - t + 1) as f64 * 0.5 + 2.) / w.min(h) as f64;
        let radius = radius.clamp(0.002, 0.02);
        let candidate = Candidate {
            x: cx / (w - 1) as f64,
            y: cy / (h - 1) as f64,
            radius,
            contrast: score,
        };
        if existing.iter().any(|p| {
            ((candidate.x - p.x) * (w - 1) as f64).hypot((candidate.y - p.y) * (h - 1) as f64)
                < (candidate.radius + p.radius) * w.min(h) as f64 * 1.5
        }) {
            continue;
        }
        found.push(candidate);
        found.sort_by(|a, b| {
            b.contrast
                .total_cmp(&a.contrast)
                .then(a.y.total_cmp(&b.y))
                .then(a.x.total_cmp(&b.x))
        });
        found.truncate(MAX_CANDIDATES);
    }
    let mut output: Vec<Candidate> = Vec::with_capacity(found.len());
    for p in found {
        if output.iter().all(|q| {
            ((p.x - q.x) * (w - 1) as f64).hypot((p.y - q.y) * (h - 1) as f64)
                > (p.radius + q.radius) * w.min(h) as f64 * 1.5
        }) {
            output.push(p);
        }
    }
    cancelled(&mut check)?;
    Ok(output)
}
