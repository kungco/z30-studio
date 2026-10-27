//! Photo-owned history and immutable preview requests; no UI or worker locks.
use crate::{
    Error, Result,
    develop::Parameters,
    recipe::{EditHistory, Recipe},
    session::Session,
};
use std::{collections::HashMap, path::PathBuf};

#[derive(Clone, Debug, PartialEq)]
pub struct EditRequest {
    pub path: PathBuf,
    pub recipe: Recipe,
}

pub struct EditingSession {
    pub session: Session,
    histories: HashMap<PathBuf, EditHistory>,
    pending_parameter: Option<String>,
}
impl EditingSession {
    pub fn new(session: Session) -> Self {
        Self {
            session,
            histories: HashMap::new(),
            pending_parameter: None,
        }
    }
    pub fn current(&self) -> Option<EditRequest> {
        let photo = self.session.photos.get(self.session.selected?)?;
        Some(EditRequest {
            path: photo.path.clone(),
            recipe: photo.recipe.clone(),
        })
    }
    pub fn select(&mut self, index: usize) -> Result<Option<EditRequest>> {
        if index >= self.session.photos.len() {
            return Ok(None);
        }
        self.commit()?;
        self.session.selected = Some(index);
        Ok(self.current())
    }
    /// The UI commits at the end of a short gesture/debounce interval. Updating
    /// the draft immediately still lets a close or photo switch save the last value.
    pub fn adjust(&mut self, name: &str, value: f32) -> Result<bool> {
        let Some(current) = self.current() else {
            return Ok(false);
        };
        let mut next = current.recipe.clone();
        let p = &mut next.parameters;
        match name {
            "exposure" => p.exposure = value,
            "contrast" => p.contrast = value,
            "highlights" => p.highlights = value,
            "shadows" => p.shadows = value,
            "temperature" => p.temperature = value,
            "tint" => p.tint = value,
            "saturation" => p.saturation = value,
            "vibrance" => p.vibrance = value,
            "sharpen" => p.sharpen = value,
            "skin_smooth" => next.portrait.smooth = value,
            "skin_uniformity" => next.skin_tone.uniformity = value,
            "skin_lighten" => next.skin_tone.lighten = value,
            _ => return Err(Error::Policy("未知调色参数".into())),
        }
        next.validate()?;
        if name.starts_with("skin_") && value > 0. {
            let binding = next.portrait.binding.unwrap();
            if !binding.matches_source(&current.path)? {
                return Err(Error::Policy("原片已变化，请重新检测面部后再柔化".into()));
            }
        }
        if current.recipe == next {
            return Ok(false);
        }
        if self
            .pending_parameter
            .as_deref()
            .is_some_and(|previous| previous != name)
        {
            self.commit()?;
        }
        self.histories
            .entry(current.path)
            .or_insert(EditHistory::new(current.recipe.clone())?);
        self.session.photos[self.session.selected.unwrap()].recipe = next;
        if self.pending_parameter.as_deref() != Some(name) {
            self.pending_parameter = Some(name.to_owned());
        }
        Ok(true)
    }
    pub fn commit(&mut self) -> Result<()> {
        if let Some(current) = self.current() {
            self.histories
                .entry(current.path)
                .or_insert(EditHistory::new(current.recipe.clone())?)
                .record(current.recipe)?;
        }
        self.pending_parameter = None;
        Ok(())
    }
    /// Apply a validated AI suggestion as one undoable edit, keeping the
    /// source photo and white-balance policy owned by the current session.
    pub fn apply_ai(&mut self, parameters: Parameters) -> Result<bool> {
        parameters.validate()?;
        let Some(current) = self.current() else {
            return Ok(false);
        };
        if current.recipe.parameters == parameters {
            return Ok(false);
        }
        self.commit()?;
        let next = Recipe {
            parameters,
            ..current.recipe
        };
        self.histories
            .get_mut(&current.path)
            .unwrap()
            .record(next.clone())?;
        self.session.photos[self.session.selected.unwrap()].recipe = next;
        Ok(true)
    }
    pub fn apply_portrait(&mut self, portrait: crate::portrait::Portrait) -> Result<bool> {
        portrait.validate()?;
        let Some(current) = self.current() else {
            return Ok(false);
        };
        if let Some(binding) = portrait.binding {
            if !binding.matches_source(&current.path)? {
                return Err(Error::Policy("人像结果不属于当前原片，未应用".into()));
            }
        }
        if current.recipe.portrait == portrait
            && current.recipe.skin.is_none()
            && !current.recipe.skin_tone.active()
        {
            return Ok(false);
        }
        self.commit()?;
        let next = Recipe {
            portrait,
            skin: None,
            skin_tone: Default::default(),
            ..current.recipe
        };
        self.histories
            .get_mut(&current.path)
            .unwrap()
            .record(next.clone())?;
        self.session.photos[self.session.selected.unwrap()].recipe = next;
        Ok(true)
    }
    pub fn apply_skin(&mut self, mask: crate::skin::SkinMask) -> Result<bool> {
        let Some(current) = self.current() else {
            return Ok(false);
        };
        mask.check_portrait(&current.recipe.portrait)?;
        if !current
            .recipe
            .portrait
            .binding
            .unwrap()
            .matches_source(&current.path)?
        {
            return Err(Error::Policy("原片已变化，皮肤识别结果未应用".into()));
        }
        if current.recipe.skin.as_ref() == Some(&mask) {
            return Ok(false);
        }
        self.commit()?;
        let mut next = current.recipe;
        next.skin = Some(mask);
        next.validate()?;
        self.histories
            .get_mut(&current.path)
            .unwrap()
            .record(next.clone())?;
        self.session.photos[self.session.selected.unwrap()].recipe = next;
        Ok(true)
    }
    pub fn can_undo(&self) -> bool {
        self.current().is_some_and(|v| {
            self.histories
                .get(&v.path)
                .is_some_and(|h| h.current() != v.recipe || h.can_undo())
        })
    }
    pub fn can_redo(&self) -> bool {
        self.current().is_some_and(|v| {
            self.histories
                .get(&v.path)
                .is_some_and(|h| h.current() == v.recipe && h.can_redo())
        })
    }
    pub fn undo(&mut self) -> Result<bool> {
        self.step(false)
    }
    pub fn redo(&mut self) -> Result<bool> {
        self.step(true)
    }
    fn step(&mut self, redo: bool) -> Result<bool> {
        self.commit()?;
        let Some(current) = self.current() else {
            return Ok(false);
        };
        let history = self.histories.get_mut(&current.path).unwrap();
        let next = if redo { history.redo() } else { history.undo() };
        if let Some(recipe) = &next {
            self.session.photos[self.session.selected.unwrap()].recipe = recipe.clone();
        }
        Ok(next.is_some())
    }
    pub fn reset(&mut self) -> Result<bool> {
        self.commit()?;
        let Some(current) = self.current() else {
            return Ok(false);
        };
        // Reset displayed global/portrait adjustments; keep the RAW WB policy.
        let next = Recipe {
            white_balance: current.recipe.white_balance,
            ..Default::default()
        };
        let changed = self
            .histories
            .get_mut(&current.path)
            .unwrap()
            .record(next.clone())?;
        self.session.photos[self.session.selected.unwrap()].recipe = next;
        Ok(changed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::session::Photo;
    fn state() -> EditingSession {
        EditingSession::new(Session {
            selected: Some(0),
            photos: ["first.png", "second.png"]
                .into_iter()
                .map(|name| Photo {
                    path: std::env::temp_dir().join(name),
                    rating: 0,
                    flag: 0,
                    recipe: Recipe::default(),
                })
                .collect(),
            ..Default::default()
        })
    }
    #[test]
    fn slider_gestures_and_photo_switches_have_separate_histories() {
        let mut s = state();
        for i in 1..=40 {
            s.adjust("exposure", i as f32 / 10.).unwrap();
        }
        let snapshot = s.current().unwrap();
        s.select(1).unwrap();
        assert!(!s.can_undo());
        s.adjust("temperature", 50.).unwrap();
        s.select(0).unwrap();
        assert_eq!(s.current().unwrap(), snapshot);
        assert!(s.undo().unwrap());
        assert_eq!(s.current().unwrap().recipe.parameters.exposure, 0.);
        assert!(!s.can_undo());
        assert!(s.redo().unwrap());
        assert_eq!(s.current().unwrap(), snapshot);
        s.select(1).unwrap();
        assert_eq!(s.current().unwrap().recipe.parameters.temperature, 50.);
        s.undo().unwrap();
        assert_eq!(snapshot.recipe.parameters.exposure, 4.);
    }
    #[test]
    fn reset_and_branch_are_undoable_invalid_input_cannot_mutate_draft() {
        let mut s = state();
        s.adjust("contrast", 25.).unwrap();
        s.commit().unwrap();
        s.adjust("shadows", 30.).unwrap();
        assert!(s.reset().unwrap());
        s.undo().unwrap();
        assert_eq!(s.current().unwrap().recipe.parameters.shadows, 30.);
        s.undo().unwrap();
        s.adjust("contrast", -10.).unwrap();
        assert!(!s.can_redo());
        assert!(!s.redo().unwrap());
        let before = s.current();
        for (key, value) in [("exposure", f32::NAN), ("exposure", 5.), ("missing", 1.)] {
            assert!(s.adjust(key, value).is_err());
            assert_eq!(s.current(), before);
        }
        let restored = EditingSession::new(
            serde_json::from_str(&serde_json::to_string(&s.session).unwrap()).unwrap(),
        );
        assert_eq!(restored.current(), before);
        assert!(!restored.can_undo());
        assert!(s.select(999).unwrap().is_none());
        assert_eq!(s.current(), before);
    }
    #[test]
    fn changing_controls_commits_the_previous_gesture_without_waiting_for_a_timer() {
        let mut state = state();
        for value in [0.1, 0.2, 0.3] {
            state.adjust("exposure", value).unwrap();
        }
        state.adjust("contrast", 15.).unwrap();
        state.adjust("contrast", 20.).unwrap();
        state.undo().unwrap();
        let recipe = state.current().unwrap().recipe;
        assert_eq!(recipe.parameters.exposure, 0.3);
        assert_eq!(recipe.parameters.contrast, 0.);
        state.undo().unwrap();
        assert_eq!(state.current().unwrap().recipe.parameters.exposure, 0.);
    }
    #[test]
    fn ai_edit_is_one_undo_step_and_keeps_the_photo_white_balance() {
        let mut s = state();
        s.adjust("exposure", 0.3).unwrap();
        let before = s.current().unwrap();
        let next = Parameters {
            exposure: 0.7,
            contrast: 12.,
            ..before.recipe.parameters
        };
        assert!(s.apply_ai(next).unwrap());
        assert_eq!(
            s.current().unwrap().recipe.white_balance,
            before.recipe.white_balance
        );
        assert!(s.undo().unwrap());
        assert_eq!(s.current().unwrap(), before);
        assert!(s.redo().unwrap());
        assert_eq!(s.current().unwrap().recipe.parameters, next);
        let invalid = Parameters {
            exposure: f32::INFINITY,
            ..next
        };
        assert!(s.apply_ai(invalid).is_err());
        assert_eq!(s.current().unwrap().recipe.parameters, next);
        s.select(1).unwrap();
        assert!(!s.can_undo());
    }
}
