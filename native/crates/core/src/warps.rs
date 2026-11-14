//! Immutable source-bound local geometry. No control data is sent to models.
use crate::{
    Error, Result,
    portrait::Binding,
    warp::{Map, Pin},
};
use serde::{Deserialize, Deserializer, Serialize, Serializer, de::Error as _};
use std::{path::Path, sync::Arc};
#[derive(Clone, Debug)]
pub struct Warps {
    binding: Binding,
    pins: Arc<[Pin]>,
    amount: f64,
}
impl PartialEq for Warps {
    fn eq(&self, other: &Self) -> bool {
        self.binding == other.binding
            && self.amount == other.amount
            && (Arc::ptr_eq(&self.pins, &other.pins) || self.pins == other.pins)
    }
}
impl Warps {
    pub fn new(binding: Binding, pins: Vec<Pin>) -> Result<Self> {
        let value = Self {
            binding,
            pins: pins.into(),
            amount: 1.,
        };
        value.validate()?;
        Ok(value)
    }
    pub fn pins(&self) -> &[Pin] {
        &self.pins
    }
    pub fn binding(&self) -> Binding {
        self.binding
    }
    pub fn active(&self) -> bool {
        self.amount > 0. && self.pins.iter().any(|p| p.from != p.to)
    }
    pub fn amount(&self) -> f64 {
        self.amount
    }
    pub fn with_amount(&self, amount: f64) -> Result<Self> {
        let value = Self {
            binding: self.binding,
            pins: Arc::clone(&self.pins),
            amount,
        };
        value.validate()?;
        Ok(value)
    }
    /// At most four metadata records; baseline controls stay immutable/shared.
    pub fn effective_pins(&self) -> Vec<Pin> {
        if self.amount == 1. {
            return self.pins.to_vec();
        }
        self.pins
            .iter()
            .map(|p| Pin {
                from: std::array::from_fn(|c| p.to[c] - (p.to[c] - p.from[c]) * self.amount),
                ..*p
            })
            .collect()
    }
    pub fn validate(&self) -> Result<()> {
        self.binding.validate()?;
        if !self.amount.is_finite() || !(0.0..=1.0).contains(&self.amount) {
            return Err(Error::Policy("形变强度无效".into()));
        }
        if self.pins.is_empty() || self.pins.len() > crate::warp::MAX_DRAGS {
            return Err(Error::Policy("形变集合需为1–4点；清除时使用空设置".into()));
        }
        Map::new(self.binding.width, self.binding.height, &self.pins)?;
        Ok(())
    }
    pub fn check_source(&self, path: &Path, width: u32, height: u32) -> Result<()> {
        self.validate()?;
        if self.active()
            && ((width, height) != (self.binding.width, self.binding.height)
                || !self.binding.matches_source(path)?)
        {
            return Err(Error::Policy("形变不属于当前原片或尺寸，未应用".into()));
        }
        Ok(())
    }
}
#[derive(Serialize)]
struct RefWire<'a> {
    version: u8,
    binding: Binding,
    pins: &'a [Pin],
    amount: f64,
}
impl Serialize for Warps {
    fn serialize<S: Serializer>(&self, s: S) -> std::result::Result<S::Ok, S::Error> {
        RefWire {
            version: 1,
            binding: self.binding,
            pins: &self.pins,
            amount: self.amount,
        }
        .serialize(s)
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Wire {
    version: u8,
    binding: Binding,
    #[serde(deserialize_with = "crate::bounded::list::<'de,_,Pin,4>")]
    pins: Vec<Pin>,
    #[serde(default = "one")]
    amount: f64,
}
fn one() -> f64 {
    1.
}
impl<'de> Deserialize<'de> for Warps {
    fn deserialize<D: Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        let w = Wire::deserialize(d)?;
        if w.version != 1 {
            return Err(D::Error::custom("未知形变集合版本"));
        }
        Self::new(w.binding, w.pins)
            .and_then(|value| value.with_amount(w.amount))
            .map_err(D::Error::custom)
    }
}
