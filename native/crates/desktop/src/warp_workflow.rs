//! A drag captures the displayed revision/view; no decoding on the UI thread.
use super::*;
use z30_core::{
    repair_view::{ImageRect, Point},
    warp_plan,
};
type PreviewWorker = LatestWorker<EditRequest, (EditRequest, z30_core::Result<DisplayPreview>)>;
type RecipeKey = Option<(PathBuf, Option<z30_core::warps::Warps>)>;
struct Gesture {
    displayed: Displayed,
    revision: u64,
    rect: ImageRect,
    canvas: (f32, f32),
    from: Point,
    radius: f64,
}
pub struct Workflow {
    state: Rc<RefCell<State>>,
    preview: Rc<PreviewWorker>,
    gesture: RefCell<Option<Gesture>>,
    shown: RefCell<RecipeKey>,
}
pub fn bind(ui: &AppWindow, state: Rc<RefCell<State>>, preview: Rc<PreviewWorker>) -> Rc<Workflow> {
    let w = Rc::new(Workflow {
        state,
        preview,
        gesture: RefCell::new(None),
        shown: RefCell::new(None),
    });
    let c = Rc::clone(&w);
    let weak = ui.as_weak();
    ui.on_warp_tool(move |enabled| {
        if let Some(ui) = weak.upgrade() {
            c.tool(&ui, enabled);
        }
    });
    let c = Rc::clone(&w);
    let weak = ui.as_weak();
    ui.on_warp_pointer(move |phase, x, y| {
        if let Some(ui) = weak.upgrade() {
            c.pointer(&ui, phase, x, y);
        }
    });
    w.sync(ui);
    w
}
impl Workflow {
    fn rect(ui: &AppWindow) -> ImageRect {
        ImageRect {
            x: ui.get_image_left() as f64,
            y: ui.get_image_top() as f64,
            width: ui.get_image_width() as f64,
            height: ui.get_image_height() as f64,
        }
    }
    fn ready(&self, ui: &AppWindow) -> bool {
        let s = self.state.borrow();
        !s.protected
            && !ui.get_blemish_busy()
            && ui.get_has_preview()
            && ui.get_engine_ready()
            && !ui.get_busy()
            && !ui.get_compare()
            && !ui.get_repair_busy()
            && ui.get_repair_mode() == 0
            && !ui.get_ai_busy()
            && !ui.get_face_busy()
            && !ui.get_batch_running()
            && !ui.get_ai_settings_visible()
            && !ui.get_ai_report_visible()
            && !ui.get_about_visible()
            && !ui.get_batch_settings_visible()
            && !ui.get_batch_report_visible()
            && !ui.get_exit_warning()
            && !s.export_dialog
            && !s.batch_dialog
            && !s.face_dialog
            && s.displayed
                .as_ref()
                .is_some_and(|d| s.edits.current().as_ref() == Some(&d.request))
    }
    fn valid(&self, ui: &AppWindow, g: &Gesture) -> bool {
        let s = self.state.borrow();
        self.ready(ui)
            && ui.get_warp_mode()
            && ui.get_repair_mode() == 0
            && s.edit_revision == g.revision
            && s.displayed.as_ref() == Some(&g.displayed)
            && Self::rect(ui) == g.rect
            && (ui.get_canvas_width(), ui.get_canvas_height()) == g.canvas
            && ui.get_warp_radius() as f64 / 100. == g.radius
    }
    pub fn sync(&self, ui: &AppWindow) {
        let key = self
            .state
            .borrow()
            .edits
            .current()
            .map(|r| (r.path, r.recipe.warp));
        let set = key.as_ref().and_then(|(_, w)| w.as_ref());
        if *self.shown.borrow() != key {
            *self.shown.borrow_mut() = key.clone();
            ui.set_warp_status(match set {
                None => "当前无形变；在照片内拖动后松开应用。".into(),
                Some(w) if w.amount() == 0. => "强度为0；提高强度或清除记录后再拖动。".into(),
                Some(w) if w.pins().len() >= z30_core::warp::MAX_DRAGS => {
                    "已达4次形变，请撤销或清除后再拖动。".into()
                }
                Some(w) => format!("当前有{}次形变，可调整强度或撤销。", w.pins().len()).into(),
            });
        }
        ui.set_warp_count(set.as_ref().map_or(0, |w| w.pins().len() as i32));
        ui.set_warp_ready(
            self.ready(ui)
                && ui.get_repair_mode() == 0
                && set
                    .as_ref()
                    .is_none_or(|w| w.pins().len() < z30_core::warp::MAX_DRAGS && w.amount() > 0.),
        );
        let stale = self
            .gesture
            .borrow()
            .as_ref()
            .is_some_and(|g| !self.valid(ui, g));
        if stale {
            self.gesture.borrow_mut().take();
            ui.set_warp_dragging(false);
            ui.set_warp_status("显示或编辑已变化，拖动已取消。".into());
        }
    }
    fn tool(&self, ui: &AppWindow, enabled: bool) {
        self.gesture.borrow_mut().take();
        ui.set_warp_dragging(false);
        if enabled {
            ui.invoke_repair_tool(0);
            ui.set_compare(false);
            ui.set_show_skin_mask(false);
            ui.set_show_face_box(false);
        }
        ui.set_warp_mode(enabled);
        ui.set_warp_status(
            if enabled {
                "在照片内拖动，松开后更新预览；退出工具后可平移。"
            } else {
                "浏览模式：拖动照片可平移。"
            }
            .into(),
        );
        self.sync(ui);
    }
    fn pointer(&self, ui: &AppWindow, phase: i32, x: f32, y: f32) {
        let result = self.pointer_inner(ui, phase, x, y);
        if let Err(e) = result {
            self.gesture.borrow_mut().take();
            ui.set_warp_dragging(false);
            ui.set_warp_status(e.to_string().into());
        }
        self.sync(ui);
    }
    fn pointer_inner(&self, ui: &AppWindow, phase: i32, x: f32, y: f32) -> z30_core::Result<()> {
        if phase == 2 {
            self.gesture.borrow_mut().take();
            ui.set_warp_dragging(false);
            return Ok(());
        }
        if phase == 0 {
            self.gesture.borrow_mut().take();
            ui.set_warp_dragging(false);
            if !ui.get_warp_mode() || !self.ready(ui) {
                return Ok(());
            }
            let s = self.state.borrow();
            let d = s.displayed.as_ref().unwrap();
            if let Some(w) = &d.request.recipe.warp {
                if w.amount() == 0. || w.pins().len() >= z30_core::warp::MAX_DRAGS {
                    return Err(z30_core::Error::Policy(
                        "强度为0或已达4次，请提高强度、撤销或清除记录".into(),
                    ));
                }
            }
            let rect = Self::rect(ui);
            let Some(from) = rect.at(x as f64, y as f64, d.size)? else {
                return Ok(());
            };
            if !d.binding.matches_source(&d.request.path)? {
                return Err(z30_core::Error::Policy("原片已变化，未开始形变".into()));
            }
            let radius = ui.get_warp_radius() as f64 / 100.;
            z30_core::warp::Pin {
                from: [from.x, from.y],
                to: [from.x, from.y],
                radius,
            }
            .validate()?;
            *self.gesture.borrow_mut() = Some(Gesture {
                displayed: d.clone(),
                revision: s.edit_revision,
                rect,
                canvas: (ui.get_canvas_width(), ui.get_canvas_height()),
                from,
                radius,
            });
            ui.set_warp_origin_x(x);
            ui.set_warp_origin_y(y);
            ui.set_warp_dragging(true);
            return Ok(());
        }
        if phase != 1 {
            return Ok(());
        }
        let Some(g) = self.gesture.borrow_mut().take() else {
            return Ok(());
        };
        ui.set_warp_dragging(false);
        if !self.valid(ui, &g) {
            return Err(z30_core::Error::Policy(
                "显示或编辑已变化，拖动未应用".into(),
            ));
        }
        if !g
            .displayed
            .binding
            .matches_source(&g.displayed.request.path)?
        {
            return Err(z30_core::Error::Policy("原片已变化，拖动未应用".into()));
        }
        let Some(to) = g.rect.at(x as f64, y as f64, g.displayed.size)? else {
            return Err(z30_core::Error::Policy("终点在照片外，拖动未应用".into()));
        };
        let Some(plan) = warp_plan::plan(
            g.displayed.request.recipe.warp.as_ref(),
            g.displayed.binding,
            g.displayed.size,
            g.from,
            to,
            g.radius,
        )?
        else {
            return Ok(());
        };
        let mut s = self.state.borrow_mut();
        if s.edits.apply_warp(Some(plan.warps))? {
            invalidate_ai(&mut s, ui);
            s.last_edit = None;
            s.pending_preview = None;
            s.dirty = true;
            sync_recipe(&s, ui);
            save_state(&mut s, ui);
            ui.set_busy(true);
            ui.set_compare(false);
            if let Some(request) = s.edits.current() {
                self.preview.submit(request);
            }
        }
        *self.shown.borrow_mut() = s.edits.current().map(|r| (r.path, r.recipe.warp));
        ui.set_warp_status(
            if plan.limited {
                "已应用：拖动幅度已限制为半径的10%。"
            } else {
                "已应用一次形变；可撤销、调节强度或继续拖动。"
            }
            .into(),
        );
        Ok(())
    }
}
