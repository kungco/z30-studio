//! Per-photo edit state; the AI contract remains the existing nine parameters.
use crate::{Result, develop::Parameters, portrait::Portrait};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum WhiteBalance {
    #[default]
    Camera,
    Auto,
    Daylight,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct Recipe {
    pub parameters: Parameters,
    pub white_balance: WhiteBalance,
    pub portrait: Portrait,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub skin: Option<crate::skin::SkinMask>,
}
impl Recipe {
    pub fn validate(&self) -> Result<()> {
        self.parameters.validate()?;
        self.portrait.validate()?;
        if let Some(skin) = &self.skin {
            skin.check_portrait(&self.portrait)?;
        }
        Ok(())
    }
}

/// Fifty undo transitions plus the current state, bounded independently of
/// image buffers. A history belongs to one photo; it is not a global AI state.
pub struct EditHistory {
    states: Vec<Recipe>,
    position: usize,
}
impl EditHistory {
    pub fn new(recipe: Recipe) -> Result<Self> {
        recipe.validate()?;
        Ok(Self {
            states: vec![recipe],
            position: 0,
        })
    }
    pub fn current(&self) -> Recipe {
        self.states[self.position].clone()
    }
    pub fn can_undo(&self) -> bool {
        self.position > 0
    }
    pub fn can_redo(&self) -> bool {
        self.position + 1 < self.states.len()
    }
    pub fn record(&mut self, recipe: Recipe) -> Result<bool> {
        recipe.validate()?;
        if self.current() == recipe {
            return Ok(false);
        }
        self.states.truncate(self.position + 1);
        self.states.push(recipe);
        if self.states.len() > 51 {
            self.states.remove(0);
        }
        self.position = self.states.len() - 1;
        Ok(true)
    }
    pub fn undo(&mut self) -> Option<Recipe> {
        if self.position == 0 {
            return None;
        }
        self.position -= 1;
        Some(self.current())
    }
    pub fn redo(&mut self) -> Option<Recipe> {
        if self.position + 1 == self.states.len() {
            return None;
        }
        self.position += 1;
        Some(self.current())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn recipe(value: f32) -> Recipe {
        Recipe {
            parameters: Parameters {
                contrast: value,
                ..Default::default()
            },
            ..Default::default()
        }
    }
    #[test]
    fn histories_are_per_photo_branch_after_undo_and_keep_fifty_steps() {
        let mut first = EditHistory::new(Recipe::default()).unwrap();
        let other = EditHistory::new(recipe(-25.)).unwrap();
        for value in 1..=60 {
            first.record(recipe(value as f32)).unwrap();
        }
        for expected in (10..60).rev() {
            assert_eq!(first.undo(), Some(recipe(expected as f32)));
        }
        assert_eq!(first.undo(), None);
        assert_eq!(first.redo(), Some(recipe(11.)));
        assert!(!first.record(recipe(11.)).unwrap());
        assert_eq!(first.redo(), Some(recipe(12.)));
        first.undo();
        first.record(recipe(-10.)).unwrap();
        assert_eq!(first.redo(), None);
        assert_eq!(other.current(), recipe(-25.));
        let mut invalid = recipe(0.);
        invalid.parameters.exposure = f32::NAN;
        let before = first.current();
        assert!(first.record(invalid).is_err());
        assert_eq!(first.current(), before);
    }
    #[test]
    fn recipe_json_is_strict_and_white_balance_is_typed() {
        assert!(serde_json::from_str::<Recipe>(r#"{"parameters":{"surprise":1}}"#).is_err());
        assert!(serde_json::from_str::<Recipe>(r#"{"white_balance":"unknown"}"#).is_err());
        let value: Recipe =
            serde_json::from_str(r#"{"parameters":{"exposure":1.25},"white_balance":"daylight"}"#)
                .unwrap();
        value.validate().unwrap();
        assert_eq!(value.white_balance, WhiteBalance::Daylight);
        assert_eq!(
            serde_json::from_str::<Recipe>(&serde_json::to_string(&value).unwrap()).unwrap(),
            value
        );
    }
}
