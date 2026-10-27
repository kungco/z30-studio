//! Pointer edits use the verified displayed image and a bounded background
//! planner. Sampling reads the existing linear thumbnail, never a UI decode.
use super::*;
use z30_core::{
    repair_plan::{Action, Planned, Settings},
    repair_view::{ImageRect, Point},
    repairs::Repairs,
    retouch::Mode,
};
type PreviewWorker = LatestWorker<EditRequest, (EditRequest, z30_core::Result<DisplayPreview>)>;
#[derive(Clone)]
struct Job {
    request: EditRequest,
    displayed: Displayed,
    revision: u64,
    action: Action,
}
struct Outcome {
    job: Job,
    result: z30_core::Result<Planned>,
}
struct Control {
    rendered: Option<ViewKey>,
    mode: i32,
    selected: Option<usize>,
    picking: bool,
    manual: Option<Point>,
    defaults: Settings,
    path: Option<PathBuf>,
    binding: Option<Binding>,
    last_points: Option<Repairs>,
    pending: Option<Job>,
}
#[derive(Clone, PartialEq)]
struct ViewKey {
    points: Option<Repairs>,
    selected: Option<usize>,
    size: (u32, u32),
    manual: Option<Point>,
    defaults: Settings,
}
impl Default for Control {
    fn default() -> Self {
        Self {
            rendered: None,
            mode: 0,
            selected: None,
            picking: false,
            manual: None,
            defaults: Settings::default(),
            path: None,
            binding: None,
            last_points: None,
            pending: None,
        }
    }
}
pub struct Workflow {
    state: Rc<RefCell<State>>,
    preview: Rc<PreviewWorker>,
    jobs: LatestWorker<Job, Outcome>,
    control: RefCell<Control>,
}
pub fn bind(
    ui: &AppWindow,
    state: Rc<RefCell<State>>,
    preview: Rc<PreviewWorker>,
    engine: Arc<Mutex<PreviewEngine>>,
    compute: Arc<Mutex<()>>,
) -> Rc<Workflow> {
    let jobs = LatestWorker::new_cancellable(move |job: Job, cancel| {
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _guard = compute.lock().unwrap_or_else(|e| e.into_inner());
            if cancel.cancelled() {
                return Err(z30_core::Error::Policy("取样已取消".into()));
            }
            let engine = engine.lock().unwrap_or_else(|e| e.into_inner());
            let plan = engine.plan_repair(
                &job.request.path,
                &job.request.recipe,
                job.displayed.binding,
                job.displayed.size,
                job.action.clone(),
            )?;
            if cancel.cancelled() {
                return Err(z30_core::Error::Policy("取样已取消".into()));
            }
            Ok(plan)
        }))
        .unwrap_or_else(|_| Err(z30_core::Error::Policy("取样异常，未应用修复".into())));
        Outcome { job, result }
    });
    let workflow = Rc::new(Workflow {
        state,
        preview,
        jobs,
        control: RefCell::new(Control::default()),
    });
    macro_rules! bind_callback{($method:ident,$callback:ident,($($arg:ident),*))=>{{let w=Rc::clone(&workflow);let weak=ui.as_weak();ui.$callback(move |$($arg),*|{if let Some(ui)=weak.upgrade(){w.$method(&ui,$($arg),*);}});}};}
    bind_callback!(tool, on_repair_tool, (mode));
    bind_callback!(click, on_repair_canvas_click, (x, y));
    bind_callback!(select, on_select_repair, (index));
    bind_callback!(adjust, on_adjust_repair, (name, value));
    bind_callback!(pick, on_pick_repair_source, ());
    bind_callback!(suggest, on_suggest_repair_source, ());
    bind_callback!(remove, on_remove_repair, ());
    bind_callback!(clear, on_clear_repairs, ());
    bind_callback!(cancel, on_cancel_repair, ());
    workflow.sync(ui);
    workflow
}
impl Workflow {
    fn modal(ui: &AppWindow) -> bool {
        ui.get_ai_settings_visible()
            || ui.get_ai_report_visible()
            || ui.get_about_visible()
            || ui.get_batch_settings_visible()
            || ui.get_batch_report_visible()
            || ui.get_exit_warning()
    }
    fn source_ready(&self, ui: &AppWindow) -> bool {
        let state = self.state.borrow();
        !state.protected
            && ui.get_has_preview()
            && !ui.get_busy()
            && !ui.get_compare()
            && !ui.get_ai_busy()
            && !ui.get_face_busy()
            && !ui.get_batch_running()
            && !ui.get_ai_settings_visible()
            && !ui.get_ai_report_visible()
            && !ui.get_about_visible()
            && !ui.get_batch_settings_visible()
            && !ui.get_batch_report_visible()
            && !ui.get_exit_warning()
            && !state.export_dialog
            && !state.batch_dialog
            && !state.face_dialog
            && state
                .displayed
                .as_ref()
                .is_some_and(|d| state.edits.current().as_ref() == Some(&d.request))
    }
    fn rect(ui: &AppWindow) -> ImageRect {
        ImageRect {
            x: ui.get_image_left() as f64,
            y: ui.get_image_top() as f64,
            width: ui.get_image_width() as f64,
            height: ui.get_image_height() as f64,
        }
    }
    fn message(&self, ui: &AppWindow, message: impl Into<slint::SharedString>) {
        ui.set_repair_status(message.into());
    }
    pub fn cancel(&self, ui: &AppWindow) {
        self.jobs.cancel();
        let mut c = self.control.borrow_mut();
        c.pending = None;
        c.picking = false;
        drop(c);
        ui.set_repair_busy(false);
        self.message(ui, "取样已取消，原有修复保留");
        self.sync(ui);
    }
    pub fn tool(&self, ui: &AppWindow, mode: i32) {
        if !(0..=2).contains(&mode) {
            return;
        }
        self.jobs.cancel();
        let mut c = self.control.borrow_mut();
        c.mode = mode;
        c.selected = None;
        c.picking = false;
        c.manual = None;
        c.pending = None;
        drop(c);
        ui.set_repair_busy(false);
        if mode > 0 {
            ui.set_repair_controls_visible(true);
            ui.set_show_skin_mask(false);
            ui.set_show_face_box(false);
        }
        self.message(
            ui,
            match mode {
                1 => "单击照片添加修复点；可先选择取样位置。",
                2 => "先选择原图取样位置，再单击照片添加克隆点。",
                _ => "浏览模式：拖动照片可平移。",
            },
        );
        self.sync(ui);
    }
    pub fn select(&self, ui: &AppWindow, index: i32) {
        if self.control.borrow().pending.is_some() {
            return;
        }
        let state = self.state.borrow();
        let points = state.edits.current().and_then(|r| r.recipe.repairs);
        let selected = usize::try_from(index)
            .ok()
            .filter(|i| points.as_ref().is_some_and(|p| *i < p.points().len()));
        let mut c = self.control.borrow_mut();
        c.selected = selected;
        c.picking = false;
        c.manual = None;
        if let Some(i) = selected {
            c.mode = if points.as_ref().unwrap().points()[i].mode == Mode::Heal {
                1
            } else {
                2
            };
            ui.set_repair_controls_visible(true);
        }
        drop(c);
        drop(state);
        self.message(
            ui,
            if selected.is_some() {
                "已选择修复点，可调整、重新取样或移除。"
            } else {
                "单击照片添加新点；克隆前先选取样位置。"
            },
        );
        self.sync(ui);
    }
    fn queue(&self, ui: &AppWindow, action: Action) {
        if !self.source_ready(ui) || self.control.borrow().pending.is_some() {
            return;
        }
        let mut state = self.state.borrow_mut();
        flush_edits(&mut state, ui);
        let Some(request) = state.edits.current() else {
            return;
        };
        let Some(displayed) = state.displayed.clone() else {
            return;
        };
        if request != displayed.request {
            return;
        }
        let job = Job {
            request,
            displayed,
            revision: state.edit_revision,
            action,
        };
        drop(state);
        self.control.borrow_mut().pending = Some(job.clone());
        self.jobs.submit(job);
        ui.set_repair_busy(true);
        self.message(ui, "正在读取已显示照片的取样区域…");
        self.sync(ui);
    }
    pub fn click(&self, ui: &AppWindow, x: f32, y: f32) {
        if !x.is_finite()
            || !y.is_finite()
            || x < 0.
            || y < 0.
            || x >= ui.get_canvas_width()
            || y >= ui.get_canvas_height()
        {
            return;
        }
        if !self.source_ready(ui) {
            self.message(ui, "请显示当前调整后的预览，再选择照片位置。");
            return;
        }
        let state = self.state.borrow();
        let Some(displayed) = &state.displayed else {
            return;
        };
        let size = displayed.size;
        let rect = Self::rect(ui);
        let point = match rect.at(x as f64, y as f64, size) {
            Ok(Some(p)) => p,
            Ok(None) => {
                self.message(ui, "照片留白区域不会添加修复点。");
                return;
            }
            Err(e) => {
                self.message(ui, e.to_string());
                return;
            }
        };
        let Some(request) = state.edits.current() else {
            return;
        };
        let c = self.control.borrow();
        if c.mode == 0 || c.pending.is_some() {
            return;
        }
        if c.picking {
            let index = c.selected;
            drop(c);
            drop(state);
            if let Some(index) = index {
                self.queue(ui, Action::Donor { index, point });
            } else {
                let mut c = self.control.borrow_mut();
                c.manual = Some(point);
                c.picking = false;
                drop(c);
                self.message(ui, "已选择原图取样位置；再单击目标位置添加修复点。");
                self.sync(ui);
            }
            return;
        }
        if ui.get_show_repair_marks() {
            let points = request
                .recipe
                .repairs
                .as_ref()
                .map_or(&[][..], |p| p.points());
            if let Ok(Some(index)) = rect.hit(x as f64, y as f64, size, points, c.selected) {
                drop(c);
                drop(state);
                self.select(ui, index as i32);
                return;
            }
        }
        let action = Action::Add {
            target: point,
            mode: if c.mode == 1 { Mode::Heal } else { Mode::Clone },
            settings: c.defaults,
            donor: c.manual.or_else(|| {
                if c.mode == 2 {
                    c.selected
                        .and_then(|i| {
                            request
                                .recipe
                                .repairs
                                .as_ref()
                                .and_then(|set| set.points().get(i))
                        })
                        .map(|p| Point { x: p.sx, y: p.sy })
                } else {
                    None
                }
            }),
        };
        drop(c);
        drop(state);
        self.queue(ui, action);
    }
    pub fn pick(&self, ui: &AppWindow) {
        if !self.source_ready(ui) || self.control.borrow().pending.is_some() {
            return;
        }
        let mut c = self.control.borrow_mut();
        if c.mode == 0 {
            c.mode = 1;
        }
        c.picking = !c.picking;
        let picking = c.picking;
        drop(c);
        self.message(
            ui,
            if picking {
                "请单击原图中用于取样的区域；拖动仍可平移。"
            } else {
                "已取消选择取样位置。"
            },
        );
        self.sync(ui);
    }
    pub fn suggest(&self, ui: &AppWindow) {
        let index = self.control.borrow().selected;
        if let Some(index) = index {
            self.queue(ui, Action::Suggest { index });
        } else {
            let mut c = self.control.borrow_mut();
            c.manual = None;
            c.picking = false;
            drop(c);
            self.message(ui, "新修复点使用邻近取样；克隆点仍需手动选择来源。");
            self.sync(ui);
        }
    }
    fn apply(
        &self,
        ui: &AppWindow,
        repairs: Option<Repairs>,
        selected: Option<usize>,
    ) -> z30_core::Result<()> {
        let mut state = self.state.borrow_mut();
        if state.protected {
            return Err(z30_core::Error::Policy("草稿受保护，未更改".into()));
        }
        let changed = state.edits.apply_repairs(repairs)?;
        if changed {
            invalidate_ai(&mut state, ui);
            state.last_edit = None;
            state.pending_preview = None;
            state.dirty = true;
            sync_recipe(&state, ui);
            save_state(&mut state, ui);
            ui.set_busy(true);
            ui.set_compare(false);
            if let Some(request) = state.edits.current() {
                self.preview.submit(request);
            }
        }
        let mut c = self.control.borrow_mut();
        c.last_points = state.edits.current().and_then(|r| r.recipe.repairs);
        c.selected = selected;
        c.picking = false;
        drop(c);
        drop(state);
        self.sync(ui);
        Ok(())
    }
    pub fn adjust(&self, ui: &AppWindow, name: slint::SharedString, value: f32) {
        if !ui.get_repair_settings_enabled() {
            return;
        }
        self.control.borrow_mut().rendered = None;
        if self.control.borrow().pending.is_some() {
            return;
        }
        let normalized = value as f64 / 100.;
        let selected = self.control.borrow().selected;
        if let Some(index) = selected {
            let mut state = self.state.borrow_mut();
            if state.protected || !ui.get_engine_ready() {
                return;
            }
            match state.edits.adjust_repair(index, name.as_str(), normalized) {
                Ok(true) => {
                    invalidate_ai(&mut state, ui);
                    state.dirty = true;
                    state.last_edit = Some(Instant::now());
                    state.pending_preview = state.edits.current();
                    self.preview.cancel();
                    ui.set_busy(true);
                    ui.set_compare(false);
                    ui.set_draft_status("正在自动保存修复参数…".into());
                    sync_recipe(&state, ui);
                    self.control.borrow_mut().last_points =
                        state.edits.current().and_then(|r| r.recipe.repairs);
                }
                Err(e) => self.message(ui, e.to_string()),
                _ => {}
            }
            drop(state);
        } else {
            let mut c = self.control.borrow_mut();
            let mut next = c.defaults;
            match name.as_str() {
                "radius" => next.radius = normalized,
                "opacity" => next.opacity = normalized,
                "feather" => next.feather = normalized,
                _ => return,
            }
            match (z30_core::retouch::Repair {
                radius: next.radius,
                opacity: next.opacity,
                feather: next.feather,
                ..Default::default()
            })
            .validate()
            {
                Ok(()) => c.defaults = next,
                Err(e) => self.message(ui, e.to_string()),
            }
        }
        self.sync(ui);
    }
    pub fn remove(&self, ui: &AppWindow) {
        if !ui.get_repair_settings_enabled() {
            return;
        }
        if self.control.borrow().pending.is_some() {
            return;
        }
        let Some(index) = self.control.borrow().selected else {
            return;
        };
        let state = self.state.borrow();
        let Some(request) = state.edits.current() else {
            return;
        };
        let Some(set) = request.recipe.repairs else {
            return;
        };
        let mut points = set.points().to_vec();
        if index >= points.len() {
            return;
        }
        points.remove(index);
        drop(state);
        let next = if points.is_empty() {
            Ok(None)
        } else {
            Repairs::new(set.binding(), points).map(Some)
        };
        match next.and_then(|p| self.apply(ui, p, None)) {
            Ok(()) => self.message(ui, "已移除当前修复点，可撤销。"),
            Err(e) => self.message(ui, e.to_string()),
        }
    }
    pub fn clear(&self, ui: &AppWindow) {
        if !ui.get_repair_can_clear() {
            return;
        }
        self.jobs.cancel();
        self.control.borrow_mut().pending = None;
        self.control.borrow_mut().manual = None;
        ui.set_repair_busy(false);
        match self.apply(ui, None, None) {
            Ok(()) => self.message(ui, "已清除修复记录，可撤销；原片保留。"),
            Err(e) => self.message(ui, e.to_string()),
        }
    }
    pub fn poll(&self, ui: &AppWindow) {
        while let Some((revision, outcome)) = self.jobs.take_result() {
            if !self.jobs.current(revision) {
                continue;
            }
            let pending = self.control.borrow_mut().pending.take();
            if pending.is_none() {
                continue;
            }
            ui.set_repair_busy(false);
            let state = self.state.borrow();
            let valid = !state.protected
                && state.edit_revision == outcome.job.revision
                && state.edits.current().as_ref() == Some(&outcome.job.request)
                && state.displayed.as_ref() == Some(&outcome.job.displayed)
                && !ui.get_compare()
                && self.source_ready(ui)
                && self.control.borrow().mode > 0;
            drop(state);
            if !valid {
                self.message(ui, "照片或参数已变化，旧取样结果未应用。");
                continue;
            }
            match outcome
                .result
                .and_then(|p| self.apply(ui, Some(p.repairs), Some(p.selected)))
            {
                Ok(()) => self.message(ui, "修复已应用；金色目标、蓝色取样，可分别调整或撤销。"),
                Err(e) => self.message(ui, e.to_string()),
            }
        }
        self.sync(ui);
    }
    fn sync(&self, ui: &AppWindow) {
        let ready = self.source_ready(ui);
        let state = self.state.borrow();
        let current = state.edits.current();
        let points = current.as_ref().and_then(|r| r.recipe.repairs.clone());
        let displayed = state.displayed.as_ref();
        let size = displayed.map_or((0, 0), |d| d.size);
        let mut c = self.control.borrow_mut();
        let path = current.as_ref().map(|r| r.path.clone());
        let binding = displayed.map(|d| d.binding);
        let path_changed = c.path != path;
        if path_changed || c.binding.is_some() && binding.is_some() && c.binding != binding {
            c.selected = None;
            c.manual = None;
            c.picking = false;
        }
        if c.last_points != points {
            c.selected = None;
            c.picking = false;
            self.message(ui, "修复记录已更新；可选择已有点或添加新点。");
        }
        c.path = path;
        if path_changed || binding.is_some() {
            c.binding = binding;
        }
        c.last_points = points.clone();
        if c.pending.as_ref().is_some_and(|job| {
            state.edit_revision != job.revision
                || current.as_ref() != Some(&job.request)
                || state.displayed.as_ref() != Some(&job.displayed)
                || ui.get_compare()
                || ui.get_ai_busy()
                || ui.get_face_busy()
                || Self::modal(ui)
                || state.export_dialog
                || state.batch_dialog
                || state.face_dialog
        }) {
            self.jobs.cancel();
            c.pending = None;
            c.picking = false;
            self.message(ui, "取样已取消，照片或编辑状态已变化。");
        }
        let values = points.as_ref().map_or(&[][..], |p| p.points());
        if c.selected.is_some_and(|i| i >= values.len()) {
            c.selected = None;
        }
        let settings = c
            .selected
            .map(|i| Settings {
                radius: values[i].radius,
                opacity: values[i].opacity,
                feather: values[i].feather,
            })
            .unwrap_or(c.defaults);
        let key = ViewKey {
            points: points.clone(),
            selected: c.selected,
            size,
            manual: c.manual,
            defaults: c.defaults,
        };
        if c.rendered.as_ref() != Some(&key) {
            let mut labels = vec![slint::SharedString::from("新修复点")];
            let mut marks = Vec::new();
            for (i, p) in values.iter().enumerate() {
                labels.push(
                    format!(
                        "{} · {}",
                        i + 1,
                        if p.mode == Mode::Heal {
                            "修复"
                        } else {
                            "克隆"
                        }
                    )
                    .into(),
                );
                if let Ok(uv) = (Point { x: p.x, y: p.y }).uv(size) {
                    let r = p.radius * size.0.min(size.1) as f64;
                    marks.push(RepairMark {
                        u: uv.x as f32,
                        v: uv.y as f32,
                        rx: (r / size.0 as f64) as f32,
                        ry: (r / size.1 as f64) as f32,
                    });
                }
            }
            let donor = c
                .selected
                .map(|i| {
                    (
                        Point {
                            x: values[i].sx,
                            y: values[i].sy,
                        },
                        values[i].radius,
                    )
                })
                .or(c.manual.map(|p| (p, c.defaults.radius)));
            if let Some((donor, radius)) = donor {
                if let Ok(uv) = donor.uv(size) {
                    let r = radius * size.0.min(size.1) as f64;
                    ui.set_repair_has_source(true);
                    ui.set_repair_source_u(uv.x as f32);
                    ui.set_repair_source_v(uv.y as f32);
                    ui.set_repair_source_rx((r / size.0 as f64) as f32);
                    ui.set_repair_source_ry((r / size.1 as f64) as f32);
                } else {
                    ui.set_repair_has_source(false);
                }
            } else {
                ui.set_repair_has_source(false);
            }
            ui.set_displayed_width(size.0 as i32);
            ui.set_displayed_height(size.1 as i32);
            ui.set_repair_mode(c.mode);
            ui.set_repair_count(values.len() as i32);
            ui.set_repair_selected(c.selected.map_or(-1, |i| i as i32));
            ui.set_repair_marks(ModelRc::new(VecModel::from(marks)));
            ui.set_repair_labels(ModelRc::new(VecModel::from(labels)));
            // A ComboBox model replacement may reset its local selection.
            // Publish the mirror afterwards; never bind that reset to our index.
            ui.set_repair_choice(c.selected.map_or(0, |i| i as i32 + 1));
            ui.set_repair_radius((settings.radius * 100.) as f32);
            ui.set_repair_opacity((settings.opacity * 100.) as f32);
            ui.set_repair_feather((settings.feather * 100.) as f32);
            c.rendered = Some(key);
        }
        ui.set_repair_mode(c.mode);
        ui.set_repair_count(values.len() as i32);
        ui.set_repair_selected(c.selected.map_or(-1, |i| i as i32));
        ui.set_repair_picking(c.picking);
        ui.set_repair_busy(c.pending.is_some());
        ui.set_repair_ready(ready);
        ui.set_repair_settings_enabled(
            !state.protected
                && !Self::modal(ui)
                && c.pending.is_none()
                && if c.selected.is_some() {
                    ui.get_engine_ready()
                } else {
                    ready
                },
        );
        ui.set_repair_can_clear(
            !state.protected && !Self::modal(ui) && current.is_some() && points.is_some(),
        );
    }
}
