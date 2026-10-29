//! Reviewable skin-spot suggestions. Images stay in the existing preview cache.
use super::*;
use z30_core::{repairs::Repairs, retouch::Repair};
type PreviewWorker = LatestWorker<EditRequest, (EditRequest, z30_core::Result<DisplayPreview>)>;
#[derive(Clone)]
struct Job {
    request: EditRequest,
    displayed: Displayed,
    revision: u64,
}
struct Outcome {
    job: Job,
    result: z30_core::Result<Vec<Repair>>,
}
#[derive(Default)]
struct Control {
    pending: Option<Job>,
    proposal: Option<(Job, Vec<Repair>)>,
    selected: Vec<bool>,
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
            let _guard = loop {
                if cancel.cancelled() {
                    return Err(z30_core::Error::Policy("瑕疵分析已取消".into()));
                }
                match compute.try_lock() {
                    Ok(g) => break g,
                    Err(std::sync::TryLockError::Poisoned(e)) => break e.into_inner(),
                    Err(std::sync::TryLockError::WouldBlock) => {
                        std::thread::sleep(Duration::from_millis(5))
                    }
                }
            };
            let engine = engine.lock().unwrap_or_else(|e| e.into_inner());
            engine.propose_blemishes(
                &job.request.path,
                &job.request.recipe,
                job.displayed.binding,
                job.displayed.size,
                || cancel.cancelled(),
            )
        }))
        .unwrap_or_else(|_| Err(z30_core::Error::Policy("候选分析异常，未修改照片".into())));
        Outcome { job, result }
    });
    let w = Rc::new(Workflow {
        state,
        preview,
        jobs,
        control: RefCell::new(Control::default()),
    });
    macro_rules! bind_callback{($method:ident,$callback:ident,($($arg:ident),*))=>{{let c=Rc::clone(&w);let weak=ui.as_weak();ui.$callback(move|$($arg),*|{if let Some(ui)=weak.upgrade(){c.$method(&ui,$($arg),*);}});}};}
    bind_callback!(find, on_find_blemishes, ());
    bind_callback!(clear, on_cancel_blemishes, ());
    bind_callback!(select, on_select_blemish, (index, selected));
    bind_callback!(apply, on_apply_blemishes, ());
    w.publish(ui);
    w
}
impl Workflow {
    fn ready(&self, ui: &AppWindow) -> bool {
        let s = self.state.borrow();
        !s.protected
            && ui.get_engine_ready()
            && ui.get_has_preview()
            && ui.get_has_skin_mask()
            && ui.get_portrait_ready()
            && !ui.get_has_warp()
            && !ui.get_busy()
            && !ui.get_compare()
            && !ui.get_repair_busy()
            && !ui.get_warp_dragging()
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
    fn valid(&self, ui: &AppWindow, job: &Job) -> bool {
        let s = self.state.borrow();
        self.ready(ui)
            && s.edit_revision == job.revision
            && s.edits.current().as_ref() == Some(&job.request)
            && s.displayed.as_ref() == Some(&job.displayed)
    }
    fn publish(&self, ui: &AppWindow) {
        let c = self.control.borrow();
        let mut items = Vec::new();
        let mut marks = Vec::new();
        if let Some((job, points)) = &c.proposal {
            let (w, h) = job.displayed.size;
            for (i, p) in points.iter().enumerate() {
                let chosen = c.selected[i];
                items.push(BlemishItem {
                    label: format!("候选 {}", i + 1).into(),
                    chosen,
                });
                marks.push(BlemishMark {
                    u: ((p.x * (w - 1) as f64 + 0.5) / w as f64) as f32,
                    v: ((p.y * (h - 1) as f64 + 0.5) / h as f64) as f32,
                    rx: (p.radius * w.min(h) as f64 / w as f64) as f32,
                    ry: (p.radius * w.min(h) as f64 / h as f64) as f32,
                    chosen,
                });
            }
        }
        ui.set_blemish_count(items.len() as i32);
        ui.set_blemish_selected(c.selected.iter().filter(|&&v| v).count() as i32);
        ui.set_blemish_items(ModelRc::new(VecModel::from(items)));
        ui.set_blemish_marks(ModelRc::new(VecModel::from(marks)));
        ui.set_blemish_busy(c.pending.is_some());
    }
    fn clear(&self, ui: &AppWindow) {
        self.jobs.cancel();
        *self.control.borrow_mut() = Control::default();
        self.publish(ui);
        ui.set_blemish_status("候选已清除；照片编辑保留。".into());
    }
    fn find(&self, ui: &AppWindow) {
        self.clear(ui);
        if !self.ready(ui) {
            ui.set_blemish_status("请先定位面部、识别皮肤并等待预览；形变需归零。".into());
            return;
        }
        let s = self.state.borrow();
        let job = Job {
            request: s.edits.current().unwrap(),
            displayed: s.displayed.as_ref().unwrap().clone(),
            revision: s.edit_revision,
        };
        drop(s);
        self.jobs.submit(job.clone());
        self.control.borrow_mut().pending = Some(job);
        self.publish(ui);
        ui.set_blemish_status("正在检查皮肤内的小暗点…".into());
    }
    fn select(&self, ui: &AppWindow, index: i32, selected: bool) {
        let mut c = self.control.borrow_mut();
        if let Ok(i) = usize::try_from(index) {
            if let Some(value) = c.selected.get_mut(i) {
                *value = selected;
            }
        }
        drop(c);
        self.publish(ui);
    }
    fn apply(&self, ui: &AppWindow) {
        if let Err(e) = self.apply_inner(ui) {
            ui.set_blemish_status(e.to_string().into());
        }
    }
    fn apply_inner(&self, ui: &AppWindow) -> z30_core::Result<()> {
        let c = self.control.borrow();
        let Some((job, points)) = c.proposal.as_ref() else {
            return Ok(());
        };
        if !self.valid(ui, job) || !job.displayed.binding.matches_source(&job.request.path)? {
            drop(c);
            self.clear(ui);
            return Err(z30_core::Error::Policy(
                "来源或编辑已变化，旧候选未应用".into(),
            ));
        }
        let strength = ui.get_blemish_strength() as f64 / 100.;
        if !strength.is_finite() || strength <= 0. || strength > 1. {
            return Err(z30_core::Error::Policy("请先设置有效的修复强度".into()));
        }
        let mut all = job
            .request
            .recipe
            .repairs
            .as_ref()
            .map_or_else(Vec::new, |r| r.points().to_vec());
        if job
            .request
            .recipe
            .repairs
            .as_ref()
            .is_some_and(|r| r.binding() != job.displayed.binding)
        {
            return Err(z30_core::Error::Policy("已有修复不属于当前原片".into()));
        }
        let mut count = 0;
        for (p, selected) in points.iter().zip(&c.selected) {
            if *selected {
                let mut p = *p;
                p.opacity = strength;
                all.push(p);
                count += 1;
            }
        }
        if count == 0 {
            return Err(z30_core::Error::Policy("请先勾选需要应用的候选".into()));
        }
        let set = Repairs::new(job.displayed.binding, all)?;
        drop(c);
        let mut s = self.state.borrow_mut();
        if s.edits.apply_repairs(Some(set))? {
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
        ui.set_blemish_status(format!("上次添加{count}项候选修复；支持撤销/重做。").into());
        Ok(())
    }
    pub fn poll(&self, ui: &AppWindow) {
        let stale = {
            let c = self.control.borrow();
            c.pending
                .as_ref()
                .or_else(|| c.proposal.as_ref().map(|p| &p.0))
                .is_some_and(|j| !self.valid(ui, j))
        };
        if stale {
            self.clear(ui);
            ui.set_blemish_status("照片、参数或窗口状态已变化，候选已取消。".into());
        }
        while let Some((revision, outcome)) = self.jobs.take_result() {
            if !self.jobs.current(revision) {
                continue;
            }
            if self.control.borrow_mut().pending.take().is_none() {
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
                ui.set_blemish_status("旧分析结果未应用，请重新检查。".into());
                continue;
            }
            match outcome.result {
                Ok(points) => {
                    let count = points.len();
                    let mut c = self.control.borrow_mut();
                    c.selected = vec![true; count];
                    c.proposal = Some((outcome.job, points));
                    drop(c);
                    self.publish(ui);
                    ui.set_blemish_status(if count == 0 {
                        "未发现符合保守条件的候选；可使用手动修复。".into()
                    } else {
                        format!("找到{count}个候选，请检查后勾选应用。").into()
                    });
                }
                Err(e) => {
                    self.clear(ui);
                    ui.set_blemish_status(e.to_string().into());
                }
            }
        }
        ui.set_blemish_ready(self.ready(ui));
    }
}
