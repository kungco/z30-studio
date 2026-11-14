//! Immutable, source-bound repair lists; coordinate data never goes to AI prompts.
use crate::{
    Error, Result,
    portrait::Binding,
    retouch::{self, Repair},
};
use serde::{Deserialize, Deserializer, Serialize, Serializer, de::Error as _};
use std::{path::Path, sync::Arc};
#[derive(Clone, Debug)]
pub struct Repairs {
    binding: Binding,
    points: Arc<[Repair]>,
}
impl PartialEq for Repairs {
    fn eq(&self, other: &Self) -> bool {
        self.binding == other.binding
            && (Arc::ptr_eq(&self.points, &other.points) || self.points == other.points)
    }
}
impl Repairs {
    pub fn new(binding: Binding, points: Vec<Repair>) -> Result<Self> {
        let value = Self {
            binding,
            points: points.into(),
        };
        value.validate()?;
        Ok(value)
    }
    pub fn points(&self) -> &[Repair] {
        &self.points
    }
    pub fn binding(&self) -> Binding {
        self.binding
    }
    pub fn active(&self) -> bool {
        self.points.iter().any(|p| p.opacity > 0.)
    }
    pub fn validate(&self) -> Result<()> {
        self.binding.validate()?;
        if self.points.is_empty() || self.points.len() > retouch::MAX_REPAIRS {
            return Err(Error::Policy(
                "修复集合需为 1–256 点；清除时使用空设置".into(),
            ));
        }
        for point in self.points.iter() {
            point.validate()?;
        }
        retouch::check_work(self.binding.width, self.binding.height, &self.points)
    }
    pub fn check_source(&self, path: &Path, width: u32, height: u32) -> Result<()> {
        self.validate()?;
        if self.active()
            && ((width, height) != (self.binding.width, self.binding.height)
                || !self.binding.matches_source(path)?)
        {
            return Err(Error::Policy(
                "修复记录不属于当前原片或尺寸；未应用，请重新取样".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Serialize)]
struct RefWire<'a> {
    version: u8,
    binding: Binding,
    points: &'a [Repair],
}
impl Serialize for Repairs {
    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        RefWire {
            version: 1,
            binding: self.binding,
            points: &self.points,
        }
        .serialize(serializer)
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Wire {
    version: u8,
    binding: Binding,
    #[serde(deserialize_with = "crate::bounded::list::<'de,_,Repair,256>")]
    points: Vec<Repair>,
}
impl<'de> Deserialize<'de> for Repairs {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let wire = Wire::deserialize(deserializer)?;
        if wire.version != 1 {
            return Err(D::Error::custom("未知修复集合版本"));
        }
        Self::new(wire.binding, wire.points).map_err(D::Error::custom)
    }
}
