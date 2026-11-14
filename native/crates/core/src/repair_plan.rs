//! Bounded, deterministic changes to an immutable source-bound repair list.
use crate::{
    Error, Result,
    portrait::Binding,
    recipe::Recipe,
    repair_view::Point,
    repairs::Repairs,
    retouch::{MAX_REPAIRS, Mode, Repair},
};
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Settings {
    pub radius: f64,
    pub opacity: f64,
    pub feather: f64,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            radius: 0.015,
            opacity: 1.,
            feather: 0.5,
        }
    }
}
#[derive(Clone, Debug)]
pub enum Action {
    Add {
        target: Point,
        mode: Mode,
        settings: Settings,
        donor: Option<Point>,
    },
    Suggest {
        index: usize,
    },
    Donor {
        index: usize,
        point: Point,
    },
}
pub struct Planned {
    pub repairs: Repairs,
    pub selected: usize,
}
pub fn plan(
    recipe: &Recipe,
    binding: Binding,
    action: Action,
    mut choose: impl FnMut(Point, f64) -> Result<Point>,
) -> Result<Planned> {
    recipe.validate()?;
    if recipe
        .repairs
        .as_ref()
        .is_some_and(|p| p.binding() != binding)
    {
        return Err(Error::Policy(
            "原片绑定已变化，请清除旧修复记录后重新取样".into(),
        ));
    }
    let mut points = recipe
        .repairs
        .as_ref()
        .map_or_else(Vec::new, |p| p.points().to_vec());
    let selected = match action {
        Action::Add {
            target,
            mode,
            settings,
            donor,
        } => {
            target.validate()?;
            Repair {
                radius: settings.radius,
                opacity: settings.opacity,
                feather: settings.feather,
                ..Default::default()
            }
            .validate()?;
            if points.len() >= MAX_REPAIRS {
                return Err(Error::Policy("已达256点，请先移除不需要的点".into()));
            }
            let source = match donor {
                Some(p) => p.validate()?,
                None if mode == Mode::Heal => choose(target, settings.radius)?.validate()?,
                None => return Err(Error::Policy("克隆前请先选取原图上的取样位置".into())),
            };
            points.push(Repair {
                mode,
                x: target.x,
                y: target.y,
                sx: source.x,
                sy: source.y,
                radius: settings.radius,
                opacity: settings.opacity,
                feather: settings.feather,
            });
            points.len() - 1
        }
        Action::Suggest { index } => {
            let edit = points
                .get_mut(index)
                .ok_or_else(|| Error::Policy("修复点已变化，请重新选择".into()))?;
            let p = choose(
                Point {
                    x: edit.x,
                    y: edit.y,
                },
                edit.radius,
            )?
            .validate()?;
            edit.sx = p.x;
            edit.sy = p.y;
            index
        }
        Action::Donor { index, point } => {
            let p = point.validate()?;
            let edit = points
                .get_mut(index)
                .ok_or_else(|| Error::Policy("修复点已变化，请重新选择".into()))?;
            edit.sx = p.x;
            edit.sy = p.y;
            index
        }
    };
    Ok(Planned {
        repairs: Repairs::new(binding, points)?,
        selected,
    })
}
