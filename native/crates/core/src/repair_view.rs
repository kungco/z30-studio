//! Logical canvas coordinates -> repair coordinates on the displayed pixel grid.
//! Repair centres use (width-1); semantic masks use a different normalization.
use crate::{Error, Result, retouch::Repair};
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}
impl Point {
    pub fn validate(self) -> Result<Self> {
        if [self.x, self.y]
            .iter()
            .any(|v| !v.is_finite() || !(0.0..=1.0).contains(v))
        {
            Err(Error::Policy("修复坐标必须位于照片内".into()))
        } else {
            Ok(self)
        }
    }
    pub fn uv(self, size: (u32, u32)) -> Result<Self> {
        self.validate()?;
        check_size(size)?;
        Ok(Self {
            x: (self.x * (size.0 - 1) as f64 + 0.5) / size.0 as f64,
            y: (self.y * (size.1 - 1) as f64 + 0.5) / size.1 as f64,
        })
    }
}
fn check_size((w, h): (u32, u32)) -> Result<()> {
    if w == 0 || h == 0 || w as u64 * h as u64 > 80_000_000 {
        Err(Error::Policy("显示像素尺寸无效".into()))
    } else {
        Ok(())
    }
}
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ImageRect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}
impl ImageRect {
    pub fn validate(self) -> Result<Self> {
        if [self.x, self.y, self.width, self.height]
            .iter()
            .any(|v| !v.is_finite())
            || self.width <= 0.
            || self.height <= 0.
        {
            Err(Error::Policy("照片显示区域无效".into()))
        } else {
            Ok(self)
        }
    }
    pub fn fit(view: (f64, f64), size: (u32, u32), zoom: f64, pan: (f64, f64)) -> Result<Self> {
        check_size(size)?;
        if [view.0, view.1, zoom, pan.0, pan.1]
            .iter()
            .any(|v| !v.is_finite())
            || view.0 <= 0.
            || view.1 <= 0.
            || !(1.0..=4.0).contains(&zoom)
        {
            return Err(Error::Policy("缩放/画布尺寸无效".into()));
        }
        let aspect = size.0 as f64 / size.1 as f64;
        let width = view.0.min(view.1 * aspect) * zoom;
        let height = width / aspect;
        let limit_x = ((width - view.0) * 0.5).max(0.);
        let limit_y = ((height - view.1) * 0.5).max(0.);
        Ok(Self {
            x: (view.0 - width) * 0.5 + pan.0.clamp(-limit_x, limit_x),
            y: (view.1 - height) * 0.5 + pan.1.clamp(-limit_y, limit_y),
            width,
            height,
        })
    }
    pub fn at(self, x: f64, y: f64, size: (u32, u32)) -> Result<Option<Point>> {
        self.validate()?;
        check_size(size)?;
        if !x.is_finite() || !y.is_finite() {
            return Err(Error::Policy("指针坐标无效".into()));
        }
        if x < self.x || y < self.y || x >= self.x + self.width || y >= self.y + self.height {
            return Ok(None);
        }
        Ok(Some(Point {
            x: if size.0 == 1 {
                0.
            } else {
                (((x - self.x) / self.width * size.0 as f64 - 0.5) / (size.0 - 1) as f64)
                    .clamp(0., 1.)
            },
            y: if size.1 == 1 {
                0.
            } else {
                (((y - self.y) / self.height * size.1 as f64 - 0.5) / (size.1 - 1) as f64)
                    .clamp(0., 1.)
            },
        }))
    }
    pub fn project(self, point: Point, size: (u32, u32)) -> Result<(f64, f64)> {
        self.validate()?;
        let uv = point.uv(size)?;
        Ok((self.x + uv.x * self.width, self.y + uv.y * self.height))
    }
    pub fn hit(
        self,
        x: f64,
        y: f64,
        size: (u32, u32),
        points: &[Repair],
        selected: Option<usize>,
    ) -> Result<Option<usize>> {
        if self.at(x, y, size)?.is_none() {
            return Ok(None);
        }
        let near = |index: usize| -> Result<bool> {
            let p = points
                .get(index)
                .ok_or_else(|| Error::Policy("修复点索引无效".into()))?;
            let (px, py) = self.project(Point { x: p.x, y: p.y }, size)?;
            Ok((px - x).hypot(py - y) <= 10.)
        };
        if let Some(i) = selected.filter(|i| *i < points.len()) {
            if near(i)? {
                return Ok(Some(i));
            }
        }
        for i in (0..points.len()).rev() {
            if near(i)? {
                return Ok(Some(i));
            }
        }
        Ok(None)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn round_trips_pixel_centres_in_wide_portrait_panned_and_zoomed_views() {
        for size in [(1600, 1067), (256, 512), (1, 1), (1, 400)] {
            for zoom in [1., 1.5, 4.] {
                let rect = ImageRect::fit((700., 510.), size, zoom, (200., -300.)).unwrap();
                for p in [
                    Point { x: 0., y: 0. },
                    Point { x: 0.3, y: 0.7 },
                    Point { x: 1., y: 1. },
                ] {
                    let (x, y) = rect.project(p, size).unwrap();
                    let found = rect.at(x, y, size).unwrap().unwrap();
                    assert!((found.x - if size.0 == 1 { 0. } else { p.x }).abs() < 1e-10);
                    assert!((found.y - if size.1 == 1 { 0. } else { p.y }).abs() < 1e-10);
                }
            }
        }
    }
    #[test]
    fn bars_edges_and_invalid_coordinates_never_become_photo_edits() {
        let rect = ImageRect::fit((800., 600.), (200, 400), 1., (400., 900.)).unwrap();
        assert_eq!(rect.x, 250.);
        assert!(rect.at(100., 200., (200, 400)).unwrap().is_none());
        assert!(rect.at(550., 100., (200, 400)).unwrap().is_none());
        assert!(rect.at(f64::NAN, 0., (200, 400)).is_err());
        assert!(rect.at(300., 200., (0, 10)).is_err());
        assert!(ImageRect::fit((0., 1.), (10, 10), 1., (0., 0.)).is_err());
        let edge = rect.at(250., 0., (200, 400)).unwrap().unwrap();
        assert_eq!(edge, Point { x: 0., y: 0. });
    }
    #[test]
    fn centre_hit_selection_prefers_selected_then_latest_without_covering_whole_patch() {
        let rect = ImageRect::fit((600., 400.), (300, 200), 1.5, (0., 0.)).unwrap();
        let points = vec![Repair::default(); 2];
        let (x, y) = rect.project(Point { x: 0.5, y: 0.5 }, (300, 200)).unwrap();
        assert_eq!(rect.hit(x, y, (300, 200), &points, None).unwrap(), Some(1));
        assert_eq!(
            rect.hit(x, y, (300, 200), &points, Some(0)).unwrap(),
            Some(0)
        );
        assert_eq!(
            rect.hit(x + 11., y, (300, 200), &points, None).unwrap(),
            None
        );
    }
}
