//! UI orchestration for explicit local segmentation. Persisted masks render
//! without the model; result acceptance uses the same source/revision guards
//! as face localization. No original files or cloud endpoints are written.
use super::*;
use z30_core::{
    recipe::Recipe,
    skin::{SIDE, SkinMask},
};

type PreviewWorker = LatestWorker<EditRequest, (EditRequest, z30_core::Result<DisplayPreview>)>;
struct Outcome {
    request: EditRequest,
    source: ExportSource,
    revision: u64,
    result: z30_core::Result<SkinMask>,
}
pub struct Overlay {
    key: ([u8; 32], Face),
    image: slint::Image,
}
pub struct Workflow {
    state: Rc<RefCell<State>>,
    worker: Rc<PreviewWorker>,
    completed: mpsc::Receiver<Outcome>,
    directories: mpsc::Receiver<Option<PathBuf>>,
    pub chooser: Rc<RefCell<Option<PathBuf>>>,
}
pub fn sync(state: &State, ui: &AppWindow, recipe: &Recipe) {
    ui.set_has_skin_mask(recipe.skin.is_some());
    ui.set_skin_mode(
        if recipe.skin.is_some() {
            "已保存皮肤区域 · 离线可用"
        } else {
            "当前使用面部几何区域"
        }
        .into(),
    );
    let mut cache = state.skin_overlay.borrow_mut();
    if let (Some(mask), Some(face), Some(binding)) =
        (&recipe.skin, recipe.portrait.face, recipe.portrait.binding)
    {
        let key = (mask.fingerprint(), face);
        if cache.as_ref().is_none_or(|cached| cached.key != key) {
            let mut rgba = Vec::with_capacity(SIDE * SIDE * 4);
            for y in 0..SIDE {
                for x in 0..SIDE {
                    let geometry = z30_core::portrait::mask(
                        &face,
                        (x as f32 + 0.5) * binding.width as f32 / SIDE as f32,
                        (y as f32 + 0.5) * binding.height as f32 / SIDE as f32,
                        binding.width,
                        binding.height,
                    );
                    rgba.extend_from_slice(&[
                        75,
                        230,
                        170,
                        (mask.weights()[y * SIDE + x] as f32 * geometry * 0.50).round() as u8,
                    ]);
                }
            }
            *cache = Some(Overlay {
                key,
                image: slint::Image::from_rgba8(
                    SharedPixelBuffer::<slint::Rgba8Pixel>::clone_from_slice(
                        &rgba,
                        SIDE as u32,
                        SIDE as u32,
                    ),
                ),
            });
        }
        ui.set_skin_preview(cache.as_ref().unwrap().image.clone());
    } else {
        *cache = None;
        ui.set_skin_preview(Default::default());
    }
}

pub fn bind(
    ui: &AppWindow,
    state: Rc<RefCell<State>>,
    worker: Rc<PreviewWorker>,
    engine: Arc<Mutex<PreviewEngine>>,
    compute: Arc<Mutex<()>>,
    cache: Arc<Mutex<FullFrameCache>>,
    dll: Option<PathBuf>,
) -> Workflow {
    let (sender, completed) = mpsc::channel();
    let (picked, directories) = mpsc::channel();
    let chooser = Rc::new(RefCell::new(None::<PathBuf>));
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        let chooser = Rc::clone(&chooser);
        ui.on_choose_skin_assets(move || {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            if state.face_control.is_some()
                || state.face_dialog
                || state.export_dialog
                || state.batch_dialog
            {
                return;
            }
            state.face_dialog = true;
            ui.set_face_busy(true);
            ui.set_face_status("请选择皮肤模型组件目录…".into());
            let selected = chooser.borrow_mut().take();
            let picked = picked.clone();
            std::thread::spawn(move || {
                let path = selected.or_else(|| {
                    rfd::FileDialog::new()
                        .set_title("选择本机皮肤模型组件")
                        .pick_folder()
                });
                let _ = picked.send(path);
            });
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        ui.on_segment_skin(move || {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            if state.protected
                || state.face_control.is_some()
                || state.face_dialog
                || state.ai_control.is_some()
                || state.batch_control.is_some()
                || state.export_control.is_some()
                || state.export_dialog
                || ui.get_busy()
            {
                return;
            }
            let (Some(runtime), Some(model)) =
                (state.face_assets.clone(), state.skin_assets.clone())
            else {
                ui.set_face_status("请选择人像运行库和皮肤模型组件".into());
                return;
            };
            flush_edits(&mut state, &ui);
            let Some(request) = state.edits.current() else {
                return;
            };
            if request.recipe.portrait.face.is_none() || !ui.get_portrait_ready() {
                ui.set_face_status("请先定位有效面部，再识别皮肤".into());
                return;
            }
            let source = match ExportSource::new(request.clone()) {
                Ok(s) => s,
                Err(e) => {
                    ui.set_face_status(e.to_string().into());
                    return;
                }
            };
            let revision = state.edit_revision;
            let cancel = Arc::new(AtomicBool::new(false));
            state.face_control = Some(Arc::clone(&cancel));
            ui.set_face_busy(true);
            ui.set_face_status("本机 CPU 正在识别皮肤；完成后可查看区域并调整柔化…".into());
            let (sender, engine, compute, cache, dll) = (
                sender.clone(),
                Arc::clone(&engine),
                Arc::clone(&compute),
                Arc::clone(&cache),
                dll.clone(),
            );
            std::thread::spawn(move || {
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(
                    || -> z30_core::Result<SkinMask> {
                        let _guard = compute.lock().unwrap_or_else(|e| e.into_inner());
                        if cancel.load(Ordering::Acquire) {
                            return Err(z30_core::Error::Policy("皮肤识别已取消".into()));
                        }
                        source.unchanged()?;
                        cache
                            .lock()
                            .unwrap_or_else(|e| e.into_inner())
                            .discard_other_source(
                                &request.path,
                                request.recipe.white_balance,
                                dll.as_deref(),
                            );
                        let recipe = Recipe {
                            white_balance: request.recipe.white_balance,
                            ..Default::default()
                        };
                        let preview = engine
                            .lock()
                            .unwrap_or_else(|e| e.into_inner())
                            .preview(&request.path, recipe)?;
                        let binding = Binding::capture(
                            &request.path,
                            preview.source_width,
                            preview.source_height,
                        )?;
                        if Some(binding) != request.recipe.portrait.binding {
                            return Err(z30_core::Error::Policy(
                                "原片尺寸或面部绑定已变化，请重新定位".into(),
                            ));
                        }
                        let neutral = Arc::clone(&preview.neutral);
                        drop(preview);
                        // Revalidate model/runtime bytes for each explicit request;
                        // release the model session when that request completes.
                        let mut segmenter = z30_vision::skin::Segmenter::open(&runtime, &model)?;
                        let result = segmenter.segment(&neutral, &cancel)?;
                        let mask = SkinMask::new(binding, result.weights)?;
                        let face = request.recipe.portrait.face.unwrap();
                        let accepted = (0..SIDE * SIDE)
                            .filter(|i| {
                                mask.weights()[*i] > 0
                                    && z30_core::portrait::mask(
                                        &face,
                                        ((*i % SIDE) as f32 + 0.5) * binding.width as f32
                                            / SIDE as f32,
                                        ((*i / SIDE) as f32 + 0.5) * binding.height as f32
                                            / SIDE as f32,
                                        binding.width,
                                        binding.height,
                                    ) > 0.
                            })
                            .count();
                        if accepted < 8 {
                            return Err(z30_core::Error::Policy(
                                "当前面部没有足够可信的皮肤区域；原配方保留，请检查姿态和光线"
                                    .into(),
                            ));
                        }
                        source.unchanged()?;
                        if cancel.load(Ordering::Acquire) {
                            return Err(z30_core::Error::Policy("皮肤识别已取消".into()));
                        }
                        Ok(mask)
                    },
                ))
                .unwrap_or_else(|_| {
                    Err(z30_core::Error::Policy("皮肤识别异常；原配方保留".into()))
                });
                let _ = sender.send(Outcome {
                    request,
                    source,
                    revision,
                    result,
                });
            });
        });
    }
    Workflow {
        state,
        worker,
        completed,
        directories,
        chooser,
    }
}
impl Workflow {
    pub fn poll(&self, ui: &AppWindow) {
        for directory in self.directories.try_iter() {
            let mut state = self.state.borrow_mut();
            state.face_dialog = false;
            ui.set_face_busy(state.face_control.is_some());
            if state.close_after_face {
                continue;
            }
            if let Some(directory) = directory {
                if directory.is_absolute() && directory.join(z30_vision::skin::MODEL_NAME).is_file()
                {
                    state.skin_assets = Some(directory);
                    ui.set_skin_assets_ready(true);
                    ui.set_face_status("已选择皮肤模型；识别前校验，不发送照片".into());
                } else {
                    ui.set_face_status("目录缺少固定的皮肤模型文件".into());
                }
            }
        }
        for outcome in self.completed.try_iter() {
            let mut state = self.state.borrow_mut();
            let cancelled = state
                .face_control
                .as_ref()
                .is_some_and(|c| c.load(Ordering::Acquire));
            state.face_control = None;
            ui.set_face_busy(false);
            if state.close_after_face {
                continue;
            }
            if cancelled
                || state.edit_revision != outcome.revision
                || state.edits.current().as_ref() != Some(&outcome.request)
            {
                ui.set_face_status("照片或参数已变化；旧皮肤结果未应用，可重新识别".into());
                continue;
            }
            if let Err(e) = outcome.source.unchanged() {
                ui.set_face_status(format!("原片已变化；皮肤结果未应用：{e}").into());
                continue;
            }
            match outcome.result.and_then(|mask| state.edits.apply_skin(mask)) {
                Ok(true) => {
                    invalidate_ai(&mut state, ui);
                    state.last_edit = None;
                    state.pending_preview = None;
                    sync_recipe(&state, ui);
                    save_state(&mut state, ui);
                    ui.set_busy(true);
                    ui.set_compare(false);
                    ui.set_show_skin_mask(true);
                    if let Some(request) = state.edits.current() {
                        self.worker.submit(request);
                    }
                    ui.set_face_status(
                        "皮肤区域已保存，可撤销；绿色显示实际作用范围，请检查后调整柔化强度".into(),
                    );
                }
                Ok(false) => {
                    sync_recipe(&state, ui);
                    ui.set_show_skin_mask(true);
                    ui.set_face_status("皮肤区域与已保存结果一致".into());
                }
                Err(e) => ui.set_face_status(e.to_string().into()),
            }
        }
    }
}
