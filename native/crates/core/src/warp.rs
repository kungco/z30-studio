//! Bounded local inverse warps in unquantized linear RGB.
//! Core only: no face-contour inference, recipe persistence or desktop tool.
use crate::{Error, Result, frame::Frame};
use serde::{Deserialize, Serialize};

pub const MAX_DRAGS: usize = 4;
pub const MAX_MAPPING_STEPS: u64 = 128_000_000;
pub const MAX_SHIFT_RADIUS: f64 = 0.1;
/// An ordered drag in the current intermediate image's pixel coordinates.
/// Centres use x*(width-1), y*(height-1); radius uses min(width,height).
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Pin {
    pub from: [f64; 2],
    pub to: [f64; 2],
    pub radius: f64,
}
impl Pin {
    pub fn validate(self) -> Result<()> {
        if self
            .from
            .iter()
            .chain(self.to.iter())
            .any(|v| !v.is_finite() || !(0.0..=1.0).contains(v))
            || !self.radius.is_finite()
            || !(0.002..=0.2).contains(&self.radius)
        {
            return Err(Error::Policy("局部形变坐标或半径无效".into()));
        }
        Ok(())
    }
}
#[derive(Clone, Copy)]
struct Drag {
    centre: [f64; 2],
    delta: [f64; 2],
    radius_squared: f64,
}
#[derive(Clone, Copy)]
struct Bounds {
    left: u32,
    top: u32,
    right: u32,
    bottom: u32,
}
/// Prepared, bounded geometry. Does not allocate image-sized coordinate maps.
pub struct Map {
    width: u32,
    height: u32,
    drags: Vec<Drag>,
    bounds: Option<Bounds>,
}
impl Map {
    pub fn new(width: u32, height: u32, pins: &[Pin]) -> Result<Self> {
        if width == 0
            || height == 0
            || width as u64 * height as u64 > 80_000_000
            || pins.len() > MAX_DRAGS
        {
            return Err(Error::Policy("局部形变尺寸或控制点数量超限".into()));
        }
        let mut drags = Vec::with_capacity(pins.len());
        let mut bounds: Option<Bounds> = None;
        for &pin in pins {
            pin.validate()?;
            if pin.from == pin.to {
                continue;
            }
            let centre = [
                pin.to[0] * (width - 1) as f64,
                pin.to[1] * (height - 1) as f64,
            ];
            let delta = [
                (pin.to[0] - pin.from[0]) * (width - 1) as f64,
                (pin.to[1] - pin.from[1]) * (height - 1) as f64,
            ];
            if delta == [0., 0.] {
                continue;
            }
            let radius = pin.radius * width.min(height) as f64;
            if delta[0].hypot(delta[1]) > MAX_SHIFT_RADIUS * radius * (1. + 1e-12) {
                return Err(Error::Policy("形变位移超过半径的10%，未处理".into()));
            }
            if centre[0] - radius < 0.
                || centre[1] - radius < 0.
                || centre[0] + radius > (width - 1) as f64
                || centre[1] + radius > (height - 1) as f64
            {
                return Err(Error::Policy("形变区域接近照片边缘，未处理".into()));
            }
            let b = Bounds {
                left: (centre[0] - radius).floor() as u32,
                top: (centre[1] - radius).floor() as u32,
                right: ((centre[0] + radius).ceil() as u32 + 1).min(width),
                bottom: ((centre[1] + radius).ceil() as u32 + 1).min(height),
            };
            bounds = Some(match bounds {
                None => b,
                Some(a) => Bounds {
                    left: a.left.min(b.left),
                    top: a.top.min(b.top),
                    right: a.right.max(b.right),
                    bottom: a.bottom.max(b.bottom),
                },
            });
            drags.push(Drag {
                centre,
                delta,
                radius_squared: radius * radius,
            });
        }
        if bounds.is_some_and(|b| {
            (b.right - b.left) as u64 * (b.bottom - b.top) as u64 * drags.len() as u64
                > MAX_MAPPING_STEPS
        }) {
            return Err(Error::Policy("形变映射区域超出计算预算，未处理".into()));
        }
        Ok(Self {
            width,
            height,
            drags,
            bounds,
        })
    }
    /// Destination -> original source. Compose geometry, interpolate only once.
    /// phi=(1-r²/R²)² is C1 at its compact boundary. With |d|<=.1R,
    /// |d|*max|grad(phi)|<=.1*8/(3*sqrt(3))<.154, so each determinant is positive.
    pub fn source_at(&self, x: f64, y: f64) -> Result<[f64; 2]> {
        if !x.is_finite()
            || !y.is_finite()
            || x < 0.
            || y < 0.
            || x > (self.width - 1) as f64
            || y > (self.height - 1) as f64
        {
            return Err(Error::Policy("形变目标像素无效".into()));
        }
        Ok(self.mapped(x, y))
    }
    fn mapped(&self, mut x: f64, mut y: f64) -> [f64; 2] {
        for drag in self.drags.iter().rev() {
            let dx = x - drag.centre[0];
            let dy = y - drag.centre[1];
            let t = 1. - (dx * dx + dy * dy) / drag.radius_squared;
            if t > 0. {
                let weight = t * t;
                x -= drag.delta[0] * weight;
                y -= drag.delta[1] * weight;
            }
        }
        [x, y]
    }
}
pub fn render(frame: &Frame, pins: &[Pin]) -> Result<Option<Frame>> {
    render_with_cancel(frame, pins, || false)
}
pub fn render_with_cancel(
    frame: &Frame,
    pins: &[Pin],
    mut cancelled: impl FnMut() -> bool,
) -> Result<Option<Frame>> {
    if cancelled() {
        return Err(Error::Policy("形变已取消".into()));
    }
    let map = Map::new(frame.width, frame.height, pins)?;
    if frame.pixels.len() as u64 != frame.width as u64 * frame.height as u64
        || frame.pixels.iter().flatten().any(|v| !v.is_finite())
    {
        return Err(Error::Policy("形变输入浮点缓冲区无效".into()));
    }
    let Some(bounds) = map.bounds else {
        return Ok(None);
    };
    if cancelled() {
        return Err(Error::Policy("形变已取消".into()));
    }
    let mut pixels = Vec::new();
    pixels
        .try_reserve_exact(frame.pixels.len())
        .map_err(|_| Error::Policy("形变输出内存不足".into()))?;
    pixels.extend_from_slice(&frame.pixels);
    let width = frame.width as usize;
    for y in bounds.top..bounds.bottom {
        if (y - bounds.top) % 32 == 0 && cancelled() {
            return Err(Error::Policy("形变已取消".into()));
        }
        for x in bounds.left..bounds.right {
            let [sx, sy] = map.mapped(x as f64, y as f64);
            if sx == x as f64 && sy == y as f64 {
                continue;
            }
            // Fully contained disks and the strict displacement bound keep
            // the map inside the photo. Clamp only floating-point edge noise.
            let sx = sx.clamp(0., (frame.width - 1) as f64);
            let sy = sy.clamp(0., (frame.height - 1) as f64);
            let x0 = sx.floor() as usize;
            let y0 = sy.floor() as usize;
            let x1 = (x0 + 1).min(width - 1);
            let y1 = (y0 + 1).min(frame.height as usize - 1);
            let fx = sx - x0 as f64;
            let fy = sy - y0 as f64;
            let samples = [
                (y0 * width + x0, (1. - fx) * (1. - fy)),
                (y0 * width + x1, fx * (1. - fy)),
                (y1 * width + x0, (1. - fx) * fy),
                (y1 * width + x1, fx * fy),
            ];
            pixels[y as usize * width + x as usize] = std::array::from_fn(|c| {
                samples
                    .iter()
                    .map(|&(i, w)| frame.pixels[i][c] as f64 * w)
                    .sum::<f64>() as f32
            });
        }
    }
    if cancelled() {
        return Err(Error::Policy("形变已取消".into()));
    }
    Ok(Some(Frame {
        width: frame.width,
        height: frame.height,
        pixels,
    }))
}
