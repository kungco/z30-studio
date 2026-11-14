//! Metadata-only drag planning in the CURRENT composited image, not source space.
use crate::{
    Error, Result,
    portrait::Binding,
    repair_view::Point,
    warp::{MAX_DRAGS, MAX_SHIFT_RADIUS, Map, Pin},
    warps::Warps,
};

pub struct Planned {
    pub warps: Warps,
    pub limited: bool,
}

pub fn plan(
    existing: Option<&Warps>,
    binding: Binding,
    displayed: (u32, u32),
    from: Point,
    to: Point,
    radius: f64,
) -> Result<Option<Planned>> {
    from.validate()?;
    to.validate()?;
    Pin {
        from: [from.x, from.y],
        to: [to.x, to.y],
        radius,
    }
    .validate()?;
    let original = (binding.width, binding.height);
    if [original, displayed].iter().any(|&(w, h)| w < 2 || h < 2) {
        return Err(Error::Policy("形变需要二维照片".into()));
    }
    let amount = if let Some(old) = existing {
        old.validate()?;
        if old.binding() != binding {
            return Err(Error::Policy("形变原片绑定已变化，请先清除旧记录".into()));
        }
        if old.amount() == 0. {
            return Err(Error::Policy("请提高形变强度或清除记录后再拖动".into()));
        }
        old.amount()
    } else {
        1.
    };
    let delta = [to.x - from.x, to.y - from.y];
    let length =
        |size: (u32, u32)| (delta[0] * (size.0 - 1) as f64).hypot(delta[1] * (size.1 - 1) as f64);
    if length(displayed) < 0.5 {
        return Ok(None);
    }
    let mut pins = existing.map_or_else(Vec::new, |w| w.pins().to_vec());
    if pins.len() >= MAX_DRAGS {
        return Err(Error::Policy("最多4次形变，请撤销或清除后再拖动".into()));
    }
    // Both preview and full-size geometry must obey the displacement bound,
    // including odd dimensions and rounded thumbnail aspect ratios.
    let mut scale = 1_f64;
    for size in [original, displayed] {
        scale = scale.min(MAX_SHIFT_RADIUS * radius * size.0.min(size.1) as f64 / length(size));
    }
    let limited = scale < 1.;
    if limited {
        scale *= 1. - 1e-9;
    }
    pins.push(Pin {
        from: [from.x, from.y],
        to: [from.x + delta[0] * scale, from.y + delta[1] * scale],
        radius,
    });
    Map::new(displayed.0, displayed.1, &pins)?;
    let warps = Warps::new(binding, pins)?.with_amount(amount)?;
    Ok(Some(Planned { warps, limited }))
}
