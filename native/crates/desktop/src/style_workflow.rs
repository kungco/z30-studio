//! Explicit small selections. Persist advice, never credentials or preview pixels.
use super::*;
use z30_core::style::{Book, Reference, Shot, Status, Store};
struct Data {
    book: Book,
    store: Store,
    protected: bool,
    selected: Vec<usize>,
    running: bool,
    backend: Option<Arc<AiBackend>>,
}
struct Task {
    generation: u64,
    index: usize,
    shot: Shot,
    proof_revision: Option<u64>,
}
struct Outcome {
    task: Task,
    source: Arc<ExportSource>,
    result: z30_core::Result<AiProduct>,
}
pub struct Workflow {
    state: Rc<RefCell<State>>,
    data: RefCell<Data>,
    engine: Arc<Mutex<PreviewEngine>>,
    compute: Arc<Mutex<()>>,
    sender: mpsc::Sender<Outcome>,
    receiver: mpsc::Receiver<Outcome>,
    last_selected: std::cell::Cell<i32>,
}
fn commit(d: &mut Data, next: Book) -> z30_core::Result<()> {
    if d.protected {
        return Err(z30_core::Error::Policy(
            "风格记录只读；未覆盖磁盘数据".into(),
        ));
    }
    if let Err(e) = d.store.save(&next) {
        d.protected = true;
        d.running = false;
        d.book = next; // Keep completed advice in memory; disk stays untouched.
        return Err(e);
    }
    d.book = next;
    Ok(())
}
fn label(status: Status) -> &'static str {
    match status {
        Status::Queued => "待分析",
        Status::Running => "分析中",
        Status::Ready => "建议待确认",
        Status::Applied => "曾确认应用（可微调/撤销）",
        Status::Failed => "失败，可重试",
        Status::Cancelled => "已停止，可重试",
    }
}
impl Workflow {
    fn request(&self, index: usize) -> z30_core::Result<EditRequest> {
        self.state
            .borrow()
            .edits
            .session
            .photos
            .get(index)
            .map(|p| EditRequest {
                path: p.path.clone(),
                recipe: p.recipe.clone(),
            })
            .ok_or_else(|| z30_core::Error::Policy("照片已不存在".into()))
    }
    fn available(&self, ui: &AppWindow) -> bool {
        let s = self.state.borrow();
        !s.protected
            && s.style_control.is_none()
            && s.ai_control.is_none()
            && !s.export_dialog
            && !s.batch_dialog
            && s.batch_control.is_none()
            && s.face_control.is_none()
            && !s.face_dialog
            && !ui.get_busy()
            && !ui.get_repair_busy()
    }
    fn candidates(&self, d: &Data) -> Vec<usize> {
        d.selected
            .iter()
            .copied()
            .filter(|&i| {
                self.request(i).is_ok_and(|p| {
                    d.book.reference.as_ref().is_some_and(|r| r.path != p.path)
                        && !d.book.shots.iter().any(|shot| {
                            shot.path == p.path
                                && (shot.status == Status::Applied
                                    || (shot.status == Status::Ready
                                        && shot.recipe == p.recipe
                                        && shot.stamp.matches(&shot.path)))
                        })
                })
            })
            .collect()
    }
    fn publish(&self, ui: &AppWindow) {
        let d = self.data.borrow();
        let s = self.state.borrow();
        let items = s
            .edits
            .session
            .photos
            .iter()
            .enumerate()
            .map(|(i, p)| {
                let is_reference = d.book.reference.as_ref().is_some_and(|r| r.path == p.path);
                let status = if is_reference {
                    "参考照片".to_owned()
                } else {
                    d.book.shots.iter().find(|shot| shot.path == p.path).map_or(
                        "未适配".to_owned(),
                        |shot| {
                            format!(
                                "{}{}",
                                label(shot.status),
                                if shot.error.is_empty() {
                                    String::new()
                                } else {
                                    format!("：{}", shot.error)
                                }
                            )
                        },
                    )
                };
                StyleItem {
                    index: i as i32,
                    name: p.name().into(),
                    chosen: d.selected.contains(&i),
                    is_reference,
                    status: status.into(),
                }
            })
            .collect::<Vec<_>>();
        ui.set_style_items(ModelRc::new(VecModel::from(items)));
        ui.set_style_count(d.selected.len() as i32);
        ui.set_style_calls(self.candidates(&d).len() as i32);
        ui.set_style_running(d.running);
        ui.set_style_busy(s.style_control.is_some());
        ui.set_style_protected(d.protected);
        ui.set_style_has_advice(s.edits.current().is_some_and(|p| {
            d.book
                .shots
                .iter()
                .any(|shot| shot.path == p.path && shot.status == Status::Ready)
        }));
        ui.set_style_reference(
            d.book
                .reference
                .as_ref()
                .map_or(
                    "请先确认代表照片的调色，再保存参考风格".to_owned(),
                    |r| {
                        format!(
                            "参考：{} · 九参数与文字目标已冻结",
                            r.path.file_name().unwrap_or_default().to_string_lossy()
                        )
                    },
                )
                .into(),
        );
    }
    fn error(&self, ui: &AppWindow, e: impl std::fmt::Display) {
        ui.set_style_status(e.to_string().into());
        self.publish(ui);
    }
    fn capture(&self, ui: &AppWindow) -> z30_core::Result<()> {
        if !self.available(ui) || ui.get_ai_suggestion_ready() || self.data.borrow().running {
            return Err(z30_core::Error::Policy(
                "请先应用或丢弃试片，并等待当前任务结束".into(),
            ));
        }
        let mut s = self.state.borrow_mut();
        flush_edits(&mut s, ui);
        let request = s
            .edits
            .current()
            .ok_or_else(|| z30_core::Error::Policy("请先选择参考照片".into()))?;
        drop(s);
        let mut d = self.data.borrow_mut();
        let mut next = Book::empty();
        next.generation = d
            .book
            .generation
            .checked_add(1)
            .ok_or_else(|| z30_core::Error::Policy("风格版本超限".into()))?;
        next.reference = Some(Reference {
            path: request.path,
            parameters: request.recipe.parameters,
            goal: ai_trial::goal(ui)?,
        });
        commit(&mut d, next)?;
        d.selected.clear();
        drop(d);
        ui.set_style_status(
            "已保存参考。新参考会保留上一批记录备份；请选择最多20张目标照片。".into(),
        );
        self.publish(ui);
        Ok(())
    }
    fn choose(&self, ui: &AppWindow, index: i32, chosen: bool) -> z30_core::Result<()> {
        let i =
            usize::try_from(index).map_err(|_| z30_core::Error::Policy("照片序号无效".into()))?;
        let p = self.request(i)?;
        let mut d = self.data.borrow_mut();
        if d.running || self.state.borrow().style_control.is_some() {
            return Err(z30_core::Error::Policy("请先暂停当前适配".into()));
        }
        if d.book.reference.as_ref().is_some_and(|r| r.path == p.path) {
            return Ok(());
        }
        if chosen && !d.selected.contains(&i) {
            if d.selected.len() >= 20 {
                return Err(z30_core::Error::Policy("一批最多选择20张照片".into()));
            }
            d.selected.push(i);
        }
        if !chosen {
            d.selected.retain(|&v| v != i);
        }
        drop(d);
        self.publish(ui);
        Ok(())
    }
    fn copy(&self, ui: &AppWindow) -> z30_core::Result<()> {
        if !self.available(ui) || self.data.borrow().running || ui.get_ai_suggestion_ready() {
            return Err(z30_core::Error::Policy(
                "请先结束任务并应用或丢弃试片".into(),
            ));
        }
        let d = self.data.borrow();
        let reference = d
            .book
            .reference
            .as_ref()
            .ok_or_else(|| z30_core::Error::Policy("请先保存参考风格".into()))?;
        let targets = d
            .selected
            .iter()
            .map(|&i| {
                let r = self.request(i)?;
                ExportSource::new(r.clone())?.unchanged()?;
                Ok((r, reference.parameters))
            })
            .collect::<z30_core::Result<Vec<_>>>()?;
        drop(d);
        let mut s = self.state.borrow_mut();
        let count = s.edits.sync_parameters(&targets)?;
        invalidate_ai(&mut s, ui);
        sync_recipe(&s, ui);
        save_state(&mut s, ui);
        drop(s);
        // Reuse the production selection/preview path, keeping each photo's history.
        let index = ui.get_selected_index();
        if index >= 0 {
            ui.invoke_select_photo(index);
        }
        ui.set_style_status(format!("已复制九参数到{count}张照片；0次模型调用。白平衡模式和局部修饰各自保留，可逐张撤销。").into());
        self.publish(ui);
        Ok(())
    }
    fn review_send(&self, ui: &AppWindow) -> z30_core::Result<()> {
        if !self.available(ui) || self.data.borrow().running || ui.get_ai_suggestion_ready() {
            return Err(z30_core::Error::Policy("请先结束任务或丢弃当前试片".into()));
        }
        let d = self.data.borrow();
        let indices = self.candidates(&d);
        let r = d
            .book
            .reference
            .as_ref()
            .ok_or_else(|| z30_core::Error::Policy("请先保存参考风格".into()))?;
        if indices.is_empty() {
            return Err(z30_core::Error::Policy(
                "请选择未适配或失败的目标；已就绪/已应用建议不会重复调用".into(),
            ));
        }
        let service = if ui.get_ai_provider() == 0 {
            "Codex CLI（本机现有登录）".to_owned()
        } else {
            let c = ai_http::Config::from_selection(
                ui.get_ai_provider(),
                &ui.get_ai_api_base(),
                &ui.get_ai_model(),
                &ui.get_ai_api_key(),
            )?;
            format!("{} · {}", c.endpoint, c.model)
        };
        ui.set_style_send_text(format!("接收服务：{service}\n\n本次最多{}次逐张分析。每张发送自己的两张无EXIF/GPS JPEG预览、九参数和白平衡模式，以及冻结的文字目标和参考参数；不发送原片、参考原片或文件路径。\n\n参考目标：{}\n参考参数：{}\n\n{}\n\n已有成功建议保留。只生成建议，不自动应用；失败不自动重试，重启不自动继续。服务可能计费。",indices.len(),r.goal,serde_json::to_string(&r.parameters)?,indices.iter().map(|&i|self.request(i).unwrap().path.file_name().unwrap_or_default().to_string_lossy().into_owned()).collect::<Vec<_>>().join("\n")).into());
        ui.set_style_confirm(true);
        Ok(())
    }
    fn start(&self, ui: &AppWindow) -> z30_core::Result<()> {
        if !ui.get_style_confirm() {
            return Err(z30_core::Error::Policy("请先检查并确认调用数量".into()));
        }
        ui.set_style_confirm(false);
        if !self.available(ui) || ui.get_ai_suggestion_ready() {
            return Err(z30_core::Error::Policy("当前任务未结束".into()));
        }
        let backend = Arc::new(if ui.get_ai_provider() == 0 {
            AiBackend::Codex
        } else {
            AiBackend::Compatible(ai_http::Config::from_selection(
                ui.get_ai_provider(),
                &ui.get_ai_api_base(),
                &ui.get_ai_model(),
                &ui.get_ai_api_key(),
            )?)
        });
        let mut d = self.data.borrow_mut();
        let indices = self.candidates(&d);
        if indices.is_empty() {
            return Err(z30_core::Error::Policy("没有待适配照片".into()));
        }
        let mut next = d.book.clone();
        for shot in &mut next.shots {
            if shot.status == Status::Queued {
                shot.status = Status::Cancelled;
                shot.error = "本次未选择，未调用模型".into();
            }
        }
        for index in indices {
            let mut shot = Shot::new(self.request(index)?)?;
            if let Some(i) = next.shots.iter().position(|v| v.path == shot.path) {
                shot.attempts = next.shots[i].attempts;
                if shot.attempts >= 5 {
                    return Err(z30_core::Error::Policy(
                        "该照片已尝试5次，请保存新参考后再处理".into(),
                    ));
                }
                next.shots[i] = shot;
            } else {
                if next.shots.len() >= 20 {
                    return Err(z30_core::Error::Policy(
                        "当前参考最多20张任务；请保存新参考建立下一批".into(),
                    ));
                }
                next.shots.push(shot);
            }
        }
        commit(&mut d, next)?;
        d.backend = Some(backend);
        d.running = true;
        drop(d);
        if ui.get_ai_provider() != 0 {
            ui.set_ai_api_key("".into());
        }
        ui.set_style_status("已确认；正在逐张生成建议。点击暂停会在本张完成后停止。".into());
        self.publish(ui);
        Ok(())
    }
    fn stop(&self, ui: &AppWindow, cancel: bool) -> z30_core::Result<()> {
        let mut d = self.data.borrow_mut();
        d.running = false;
        d.backend = None;
        if cancel {
            if let Some(c) = &self.state.borrow().style_control {
                c.store(true, Ordering::Release);
            }
            let mut next = d.book.clone();
            for s in &mut next.shots {
                if s.status == Status::Queued
                    || (s.status == Status::Running && self.state.borrow().style_control.is_none())
                {
                    s.status = Status::Cancelled;
                    s.error = "用户已停止，未调用模型".into();
                }
            }
            commit(&mut d, next)?;
        }
        drop(d);
        ui.set_style_status(
            if cancel {
                "正在停止；已生成的建议保留，可手动选择重试。"
            } else {
                "已暂停后续照片；本张结束后停止。继续需要再次检查调用数量。"
            }
            .into(),
        );
        self.publish(ui);
        Ok(())
    }
    fn proof(&self, ui: &AppWindow) -> z30_core::Result<()> {
        if !self.available(ui) || self.data.borrow().running || ui.get_ai_suggestion_ready() {
            return Err(z30_core::Error::Policy(
                "请先暂停并结束当前任务/试片".into(),
            ));
        }
        let s = self.state.borrow();
        let request = s
            .edits
            .current()
            .ok_or_else(|| z30_core::Error::Policy("请先查看一张目标照片".into()))?;
        let d = self.data.borrow();
        let index = d
            .book
            .shots
            .iter()
            .position(|v| v.path == request.path && v.status == Status::Ready)
            .ok_or_else(|| {
                z30_core::Error::Policy("当前照片没有待确认建议；请在列表中查看目标照片".into())
            })?;
        let shot = d.book.shots[index].clone();
        if shot.recipe != request.recipe || !shot.stamp.matches(&shot.path) {
            return Err(z30_core::Error::Policy(
                "照片或参数已变化，请勾选后手动重新适配".into(),
            ));
        }
        let task = Task {
            generation: d.book.generation,
            index,
            shot,
            proof_revision: Some(s.edit_revision),
        };
        drop(d);
        drop(s);
        self.spawn(task, None, String::new())?;
        ui.set_style_visible(false);
        self.publish(ui);
        Ok(())
    }
    fn spawn(
        &self,
        task: Task,
        backend: Option<Arc<AiBackend>>,
        goal: String,
    ) -> z30_core::Result<()> {
        let request = task.shot.request();
        let source = Arc::new(ExportSource::new(request.clone())?);
        let cancel = Arc::new(AtomicBool::new(false));
        self.state.borrow_mut().style_control = Some(Arc::clone(&cancel));
        let compute = Arc::clone(&self.compute);
        let engine = Arc::clone(&self.engine);
        let sender = self.sender.clone();
        std::thread::spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                if !task.shot.stamp.matches(&request.path) {
                    return Err(z30_core::Error::Policy("原片已变化".into()));
                }
                let display = {
                    let _guard = loop {
                        if cancel.load(Ordering::Acquire) {
                            return Err(z30_core::Error::Policy("风格任务已取消".into()));
                        }
                        match compute.try_lock() {
                            Ok(g) => break g,
                            Err(std::sync::TryLockError::Poisoned(e)) => break e.into_inner(),
                            Err(_) => std::thread::sleep(Duration::from_millis(5)),
                        }
                    };
                    DisplayPreview::from(
                        engine
                            .lock()
                            .unwrap_or_else(|e| e.into_inner())
                            .preview_with_cancel(&request.path, request.recipe.clone(), || {
                                cancel.load(Ordering::Acquire)
                            })?,
                    )
                };
                let current = SharedPixelBuffer::<Rgb8Pixel>::clone_from_slice(
                    &display.rgb,
                    display.width,
                    display.height,
                );
                let neutral = SharedPixelBuffer::<Rgb8Pixel>::clone_from_slice(
                    &display.neutral,
                    display.width,
                    display.height,
                );
                drop(display);
                let product = if let Some(backend) = backend {
                    evaluate_ai(
                        &request,
                        &source,
                        &backend,
                        &goal,
                        Options::default(),
                        &current,
                        &neutral,
                        &compute,
                        &engine,
                        &cancel,
                    )?
                } else {
                    render_ai_advice(
                        &request,
                        &source,
                        task.shot.advice.clone().unwrap(),
                        Options::default(),
                        ai::metrics(current.as_bytes(), current.width(), current.height())?,
                        &compute,
                        &engine,
                        || cancel.load(Ordering::Acquire),
                    )?
                };
                source.unchanged()?;
                if !task.shot.stamp.matches(&request.path) {
                    return Err(z30_core::Error::Policy("原片已变化".into()));
                }
                Ok(product)
            }))
            .unwrap_or_else(|_| Err(z30_core::Error::Policy("风格任务异常；编辑保留".into())));
            let _ = sender.send(Outcome {
                task,
                source,
                result,
            });
        });
        Ok(())
    }
    pub fn applied(&self, id: (u64, usize), ui: &AppWindow) {
        let mut d = self.data.borrow_mut();
        if d.book.generation != id.0 {
            return;
        }
        let mut next = d.book.clone();
        if let Some(s) = next.shots.get_mut(id.1) {
            s.status = Status::Applied;
        }
        if let Err(e) = commit(&mut d, next) {
            ui.set_style_status(format!("编辑已保存，但建议状态保存失败：{e}").into());
        }
        drop(d);
        self.publish(ui);
    }
    pub fn poll(&self, ui: &AppWindow) {
        let mut changed =
            self.last_selected.replace(ui.get_selected_index()) != ui.get_selected_index();
        for out in self.receiver.try_iter() {
            let mut s = self.state.borrow_mut();
            let cancelled = s
                .style_control
                .as_ref()
                .is_some_and(|c| c.load(Ordering::Acquire));
            s.style_control = None;
            if s.close_after_ai {
                drop(s);
                let _ = self.stop(ui, true);
                continue;
            }
            let matches = s
                .edits
                .session
                .photos
                .iter()
                .any(|p| p.path == out.task.shot.path && p.recipe == out.task.shot.recipe)
                && out.task.shot.stamp.matches(&out.task.shot.path);
            let mut d = self.data.borrow_mut();
            if d.book.generation != out.task.generation {
                continue;
            }
            if let Some(revision) = out.task.proof_revision {
                if !cancelled
                    && matches
                    && s.edit_revision == revision
                    && s.edits.current().as_ref() == Some(&out.task.shot.request())
                {
                    match out.result {
                        Ok(product) => {
                            ai_trial::show(ui, &product, &out.task.shot.recipe.parameters);
                            ui.set_ai_trial_strength(100.);
                            ui.set_ai_report(ai_report(&product).into());
                            ui.set_ai_suggestion_ready(true);
                            s.ai_suggestion = Some(AiSuggestion {
                                request: out.task.shot.request(),
                                source: out.source,
                                revision,
                                product,
                                style_id: Some((out.task.generation, out.task.index)),
                            });
                            ui.set_ai_status(
                                "批量建议试片已就绪；调强度不再次调用模型，确认后应用。".into(),
                            );
                        }
                        Err(e) => ui.set_style_status(e.to_string().into()),
                    }
                } else {
                    ui.set_style_status("照片或参数已变化，旧试片未显示。".into());
                }
            } else {
                let mut next = d.book.clone();
                let shot = &mut next.shots[out.task.index];
                match out.result {
                    Ok(product) if !cancelled && matches => {
                        shot.advice = Some(product.advice);
                        shot.status = Status::Ready;
                        shot.error.clear();
                    }
                    result => {
                        shot.status = if cancelled {
                            Status::Cancelled
                        } else {
                            Status::Failed
                        };
                        shot.error = if !matches {
                            "照片或参数已变化，未应用".into()
                        } else {
                            result.err().map_or("用户已取消".into(), |e| {
                                e.to_string().chars().take(500).collect()
                            })
                        };
                    }
                }
                if let Err(e) = commit(&mut d, next) {
                    ui.set_style_status(e.to_string().into());
                }
            }
            changed = true;
        }
        let can_run = {
            let s = self.state.borrow();
            s.style_control.is_none()
                && s.ai_control.is_none()
                && !s.close_after_ai
                && !s.protected
                && !s.batch_dialog
                && s.batch_control.is_none()
                && s.export_control.is_none()
                && !s.export_dialog
                && s.face_control.is_none()
        };
        if can_run && self.data.borrow().running {
            let mut d = self.data.borrow_mut();
            if let Some(index) = d.book.shots.iter().position(|s| s.status == Status::Queued) {
                let mut next = d.book.clone();
                let shot = &mut next.shots[index];
                let valid = self
                    .state
                    .borrow()
                    .edits
                    .session
                    .photos
                    .iter()
                    .any(|p| p.path == shot.path && p.recipe == shot.recipe)
                    && shot.stamp.matches(&shot.path);
                if !valid {
                    shot.status = Status::Failed;
                    shot.error = "来源或编辑变化；未发送照片".into();
                    let _ = commit(&mut d, next);
                } else {
                    shot.status = Status::Running;
                    shot.attempts += 1;
                    let shot = shot.clone();
                    let reference = next.reference.as_ref().unwrap();
                    let goal = format!(
                        "{} 同组风格参考参数（不强制照抄）：{}。根据本张曝光与偏色单独适配，保持同组风格与肤色自然。",
                        reference.goal,
                        serde_json::to_string(&reference.parameters).unwrap()
                    );
                    let task = Task {
                        generation: next.generation,
                        index,
                        shot,
                        proof_revision: None,
                    };
                    let backend = d.backend.clone();
                    match commit(&mut d, next) {
                        Ok(()) => {
                            drop(d);
                            if let Err(e) = self.spawn(task, backend, goal) {
                                let _ = self.stop(ui, true);
                                ui.set_style_status(e.to_string().into());
                            }
                        }
                        Err(e) => ui.set_style_status(e.to_string().into()),
                    }
                }
                changed = true;
            } else {
                d.running = false;
                d.backend = None;
                changed = true;
                ui.set_style_status(
                    "本批已停止或完成；成功建议保留，请逐张查看、试片和确认。".into(),
                );
            }
        }
        if changed {
            self.publish(ui);
        }
    }
}
pub fn bind(
    ui: &AppWindow,
    state: Rc<RefCell<State>>,
    engine: Arc<Mutex<PreviewEngine>>,
    compute: Arc<Mutex<()>>,
) -> z30_core::Result<Rc<Workflow>> {
    let mut store = Store::new(data_root())?;
    let (book, protected, error) = match store.load() {
        Ok(v) => (v, false, None),
        Err(e) => (Book::empty(), true, Some(e.to_string())),
    };
    let selected = state
        .borrow()
        .edits
        .session
        .photos
        .iter()
        .enumerate()
        .filter(|(_, p)| book.shots.iter().any(|s| s.path == p.path))
        .map(|(i, _)| i)
        .collect();
    let (sender, receiver) = mpsc::channel();
    let w = Rc::new(Workflow {
        state,
        data: RefCell::new(Data {
            book,
            store,
            protected,
            selected,
            running: false,
            backend: None,
        }),
        engine,
        compute,
        sender,
        receiver,
        last_selected: std::cell::Cell::new(-2),
    });
    macro_rules! cb {
        ($callback:ident,$method:ident) => {{
            let w = Rc::clone(&w);
            let weak = ui.as_weak();
            ui.$callback(move || {
                if let Some(ui) = weak.upgrade() {
                    if let Err(e) = w.$method(&ui) {
                        w.error(&ui, e);
                    }
                }
            });
        }};
    }
    cb!(on_capture_style, capture);
    cb!(on_copy_style, copy);
    cb!(on_review_style_send, review_send);
    cb!(on_start_style, start);
    cb!(on_proof_style, proof);
    {
        let w = Rc::clone(&w);
        let weak = ui.as_weak();
        ui.on_choose_style_photo(move |i, chosen| {
            if let Some(ui) = weak.upgrade() {
                if let Err(e) = w.choose(&ui, i, chosen) {
                    w.error(&ui, e);
                }
            }
        });
    }
    {
        let w = Rc::clone(&w);
        let weak = ui.as_weak();
        ui.on_stop_style(move |cancel| {
            if let Some(ui) = weak.upgrade() {
                if let Err(e) = w.stop(&ui, cancel) {
                    w.error(&ui, e);
                }
            }
        });
    }
    {
        let w = Rc::clone(&w);
        let weak = ui.as_weak();
        ui.on_open_style(move || {
            if let Some(ui) = weak.upgrade() {
                w.publish(&ui);
                ui.set_style_visible(true);
            }
        });
    }
    if let Some(e) = error {
        ui.set_style_status(format!("风格记录读取失败，保持只读：{e}").into());
    }
    w.publish(ui);
    Ok(w)
}
