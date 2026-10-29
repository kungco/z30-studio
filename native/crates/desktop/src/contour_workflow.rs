//! Explicit local mesh proposals. A proposal is ephemeral; applying creates
//! an ordinary source-bound warp recipe with existing undo/export recovery.
use super::*;
use z30_core::{recipe::Recipe, warp::Pin, warps::Warps};
use z30_vision::contour::{Landmarker, Mesh, OVAL};
type PreviewWorker = LatestWorker<EditRequest, (EditRequest, z30_core::Result<DisplayPreview>)>;
#[derive(Clone)]
struct Job {
    request: EditRequest,
    displayed: Displayed,
    revision: u64,
    runtime: PathBuf,
    model: PathBuf,
}
struct Outcome {
    job: Job,
    result: z30_core::Result<(Mesh, Vec<Pin>)>,
}
#[derive(Default)]
struct Control {
    pending: Option<Job>,
    proposal: Option<(Job, Mesh, Vec<Pin>)>,
}
pub struct Workflow {
    state: Rc<RefCell<State>>,
    preview: Rc<PreviewWorker>,
    jobs: LatestWorker<Job, Outcome>,
    control: RefCell<Control>,
    model: RefCell<Option<PathBuf>>,
    directories: mpsc::Receiver<Option<PathBuf>>,
    pub chooser: Rc<RefCell<Option<PathBuf>>>,
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
            let _guard = loop {
                if cancel.cancelled() {
                    return Err(z30_core::Error::Policy("轮廓分析已取消".into()));
                }
                match compute.try_lock() {
                    Ok(g) => break g,
                    Err(std::sync::TryLockError::Poisoned(e)) => break e.into_inner(),
                    Err(std::sync::TryLockError::WouldBlock) => {
                        std::thread::sleep(Duration::from_millis(5))
                    }
                }
            };
            let source = ExportSource::new(job.request.clone())?;
            source.unchanged()?;
            let face = job
                .request
                .recipe
                .portrait
                .face
                .ok_or_else(|| z30_core::Error::Policy("请先定位面部".into()))?;
            let preview = engine.lock().unwrap_or_else(|e| e.into_inner()).preview(
                &job.request.path,
                Recipe {
                    white_balance: job.request.recipe.white_balance,
                    ..Default::default()
                },
            )?;
            if preview.binding != job.displayed.binding
                || (preview.neutral.width, preview.neutral.height) != job.displayed.size
            {
                return Err(z30_core::Error::Policy(
                    "显示来源或尺寸已变化，未分析".into(),
                ));
            }
            let neutral = Arc::clone(&preview.neutral);
            drop(preview);
            let mut model = Landmarker::open(&job.runtime, &job.model)?;
            let mesh = model.infer(&neutral, &face, || cancel.cancelled())?;
            let pins = z30_vision::contour::propose(
                &mesh,
                (job.displayed.binding.width, job.displayed.binding.height),
            )?;
            source.unchanged()?;
            Ok((mesh, pins))
        }))
        .unwrap_or_else(|_| Err(z30_core::Error::Policy("轮廓分析异常，照片编辑保留".into())));
        Outcome { job, result }
    });
    let (picked, directories) = mpsc::channel();
    let chooser = Rc::new(RefCell::new(None));
    let model = std::env::var_os("Z30_CONTOUR_DIR")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|d| d.join("contour")))
                .filter(|p| p.is_dir())
        });
    let w = Rc::new(Workflow {
        state,
        preview,
        jobs,
        control: RefCell::new(Control::default()),
        model: RefCell::new(model),
        directories,
        chooser,
    });
    macro_rules! bind_callback {
        ($method:ident,$callback:ident) => {{
            let c = Rc::clone(&w);
            let weak = ui.as_weak();
            ui.$callback(move || {
                if let Some(ui) = weak.upgrade() {
                    c.$method(&ui);
                }
            });
        }};
    }
    bind_callback!(find, on_find_contour);
    bind_callback!(clear, on_cancel_contour);
    bind_callback!(apply, on_apply_contour);
    {
        let c = Rc::clone(&w);
        let weak = ui.as_weak();
        ui.on_choose_contour_assets(move || {
            let Some(ui) = weak.upgrade() else {
                return;
            };
            c.clear(&ui);
            let mut s = c.state.borrow_mut();
            if s.face_dialog || s.export_dialog || s.batch_dialog || s.face_control.is_some() {
                return;
            }
            s.face_dialog = true;
            ui.set_face_busy(true);
            ui.set_contour_status("请选择本机轮廓模型组件…".into());
            let override_path = c.chooser.borrow_mut().take();
            let picked = picked.clone();
            std::thread::spawn(move || {
                let p = override_path.or_else(|| {
                    rfd::FileDialog::new()
                        .set_title("选择面部轮廓模型组件")
                        .pick_folder()
                });
                let _ = picked.send(p);
            });
        });
    }
    w.publish(ui);
    w
}
impl Workflow {
    fn ready(&self, ui: &AppWindow) -> bool {
        let s = self.state.borrow();
        !s.protected
            && self.model.borrow().is_some()
            && s.face_assets.is_some()
            && ui.get_engine_ready()
            && ui.get_has_preview()
            && ui.get_portrait_ready()
            && !ui.get_has_warp_record()
            && !ui.get_busy()
            && !ui.get_compare()
            && !ui.get_repair_busy()
            && ui.get_repair_mode() == 0
            && !ui.get_warp_mode()
            && !ui.get_blemish_busy()
            && !ui.get_ai_busy()
            && !ui.get_face_busy()
            && !ui.get_batch_running()
            && !ui.get_export_busy()
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
    fn valid(&self, ui: &AppWindow, job: &Job) -> bool {
        if !self.ready(ui) {
            return false;
        }
        let s = self.state.borrow();
        s.edit_revision == job.revision
            && s.edits.current().as_ref() == Some(&job.request)
            && s.displayed.as_ref() == Some(&job.displayed)
            && s.face_assets.as_ref() == Some(&job.runtime)
            && self.model.borrow().as_ref() == Some(&job.model)
    }
    fn publish(&self, ui: &AppWindow) {
        let c = self.control.borrow();
        let points = if let Some((_, mesh, _)) = &c.proposal {
            OVAL.iter()
                .map(|&i| FacePoint {
                    x: ((mesh.points[i][0] * (mesh.width - 1) as f64 + 0.5) / mesh.width as f64)
                        as f32,
                    y: ((mesh.points[i][1] * (mesh.height - 1) as f64 + 0.5) / mesh.height as f64)
                        as f32,
                })
                .collect()
        } else {
            Vec::new()
        };
        ui.set_contour_points(ModelRc::new(VecModel::from(points)));
        ui.set_contour_count(c.proposal.as_ref().map_or(0, |p| p.2.len() as i32));
        ui.set_contour_busy(c.pending.is_some());
        ui.set_contour_assets_ready(self.model.borrow().is_some());
    }
    fn clear(&self, ui: &AppWindow) {
        self.jobs.cancel();
        *self.control.borrow_mut() = Control::default();
        self.publish(ui);
        ui.set_contour_status("轮廓建议已收起；照片编辑保留。".into());
    }
    fn find(&self, ui: &AppWindow) {
        self.clear(ui);
        if !self.ready(ui) {
            ui.set_contour_status("请先定位面部并选择轮廓组件；已有形变需先清除。".into());
            return;
        }
        let s = self.state.borrow();
        let job = Job {
            request: s.edits.current().unwrap(),
            displayed: s.displayed.as_ref().unwrap().clone(),
            revision: s.edit_revision,
            runtime: s.face_assets.clone().unwrap(),
            model: self.model.borrow().clone().unwrap(),
        };
        drop(s);
        self.jobs.submit(job.clone());
        self.control.borrow_mut().pending = Some(job);
        self.publish(ui);
        ui.set_contour_status("正在本机定位轮廓；尚未修改照片…".into());
    }
    fn apply(&self, ui: &AppWindow) {
        if let Err(e) = self.apply_inner(ui) {
            ui.set_contour_status(e.to_string().into());
        }
    }
    fn apply_inner(&self, ui: &AppWindow) -> z30_core::Result<()> {
        let c = self.control.borrow();
        let Some((job, _, pins)) = &c.proposal else {
            return Ok(());
        };
        if !self.valid(ui, job) || !job.displayed.binding.matches_source(&job.request.path)? {
            drop(c);
            self.clear(ui);
            return Err(z30_core::Error::Policy(
                "来源或编辑已变化，旧轮廓未应用".into(),
            ));
        }
        let amount = ui.get_contour_strength() as f64 / 100.;
        if !amount.is_finite() || amount <= 0. || amount > 1. {
            return Err(z30_core::Error::Policy("请先设置有效的修形强度".into()));
        }
        let warp = Warps::new(job.displayed.binding, pins.clone())?.with_amount(amount)?;
        z30_core::warp::Map::new(
            job.displayed.size.0,
            job.displayed.size.1,
            &warp.effective_pins(),
        )?;
        drop(c);
        let mut s = self.state.borrow_mut();
        if s.edits.apply_warp(Some(warp))? {
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
        drop(s);
        self.clear(ui);
        ui.set_contour_status("上次已应用轻微修形；可调整形变强度，并撤销/重做。".into());
        Ok(())
    }
    pub fn poll(&self, ui: &AppWindow) {
        for selected in self.directories.try_iter() {
            let mut s = self.state.borrow_mut();
            s.face_dialog = false;
            ui.set_face_busy(false);
            if s.close_after_face {
                continue;
            }
            drop(s);
            if let Some(p) = selected {
                if p.is_absolute() && p.join(z30_vision::contour::MODEL_NAME).is_file() {
                    *self.model.borrow_mut() = Some(p);
                    ui.set_contour_status("已选组件；分析时校验固定模型，没有发送照片。".into());
                } else {
                    ui.set_contour_status("目录缺少固定轮廓模型；原组件设置保留。".into());
                }
            } else {
                ui.set_contour_status("已取消选择组件。".into());
            }
            self.publish(ui);
        }
        let stale = {
            let c = self.control.borrow();
            c.pending
                .as_ref()
                .or_else(|| c.proposal.as_ref().map(|p| &p.0))
                .is_some_and(|j| !self.valid(ui, j))
        };
        if stale {
            self.clear(ui);
            ui.set_contour_status("照片、参数或窗口状态已变化，轮廓建议已取消。".into());
        }
        while let Some((revision, outcome)) = self.jobs.take_result() {
            if !self.jobs.current(revision) || self.control.borrow_mut().pending.take().is_none() {
                continue;
            }
            if !self.valid(ui, &outcome.job)
                || !outcome
                    .job
                    .displayed
                    .binding
                    .matches_source(&outcome.job.request.path)
                    .unwrap_or(false)
            {
                self.clear(ui);
                continue;
            }
            match outcome.result {
                Ok((mesh, pins)) => {
                    self.control.borrow_mut().proposal = Some((outcome.job, mesh, pins));
                    self.publish(ui);
                    ui.set_contour_status("已定位轮廓并生成4项轻微修形建议；请检查后应用。".into());
                }
                Err(e) => {
                    self.clear(ui);
                    ui.set_contour_status(e.to_string().into());
                }
            }
        }
        ui.set_contour_ready(self.ready(ui));
    }
}
