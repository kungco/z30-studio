#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use slint::{ComponentHandle, Model, ModelRc, Rgb8Pixel, SharedPixelBuffer, VecModel};
use std::{
    cell::RefCell,
    path::PathBuf,
    rc::Rc,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    time::{Duration, Instant},
};
use z30_core::{
    ai::{self, Advice, Options},
    batch::{self, Queue as BatchQueue, RecipeMode, Store as BatchStore},
    develop::Parameters,
    editing::{EditRequest, EditingSession},
    editor::PreviewEngine,
    export::Format,
    export_job::{ExportControl, ExportReceipt, ExportSource, FullFrameCache},
    portrait::{Binding, Face, Portrait},
    session::{Photo, Session, SessionStore},
    worker::LatestWorker,
};
slint::include_modules!();
mod ai_cli;
mod ai_http;
mod qa;
mod skin_workflow;
mod repair_workflow;
mod warp_workflow;
mod blemish_workflow;

enum AiBackend {
    Codex,
    Compatible(ai_http::Config),
}

struct State {
    displayed:Option<Displayed>,
    edits: EditingSession,
    last_edit: Option<Instant>,
    pending_preview: Option<EditRequest>,
    export_control: Option<Arc<ExportControl>>,
    export_dialog: bool,
    close_after_export: bool,
    batch: BatchQueue,
    batch_store: BatchStore,
    batch_protected: bool,
    batch_dialog: bool,
    batch_control: Option<(u64, Arc<ExportControl>)>,
    close_after_batch: bool,
    store: SessionStore,
    protected: bool,
    dirty: bool,
    edit_revision: u64,
    ai_control: Option<Arc<AtomicBool>>,
    ai_suggestion: Option<AiSuggestion>,
    close_after_ai: bool,
    face_assets: Option<PathBuf>,
    skin_assets: Option<PathBuf>,
    skin_overlay: RefCell<Option<skin_workflow::Overlay>>,
    face_control: Option<Arc<AtomicBool>>,
    face_dialog: bool,
    close_after_face: bool,
    face_candidates: Option<FaceCandidates>,
}
#[derive(Clone,Debug,PartialEq)]
struct Displayed {request:EditRequest,binding:Binding,size:(u32,u32)}

struct FaceCandidates { path: PathBuf, binding: Binding, faces: Vec<Face>, selected: usize }
struct FaceOutcome {
    request: EditRequest,
    source: ExportSource,
    revision: u64,
    result: z30_core::Result<(Vec<Face>, Binding)>,
}

struct AiProduct {
    advice: Advice,
    parameters: Parameters,
    preview: DisplayPreview,
    used_strength: u32,
    quality_note: String,
}
struct AiOutcome {
    request: EditRequest,
    source: ExportSource,
    revision: u64,
    auto_apply: bool,
    result: z30_core::Result<AiProduct>,
}
struct AiSuggestion {
    request: EditRequest,
    source: ExportSource,
    revision: u64,
    product: AiProduct,
}

struct DisplayPreview {
    binding:Binding,
    rgb: Vec<u8>,
    neutral: Vec<u8>,
    thumbnail: Vec<u8>,
    width: u32,
    height: u32,
    thumb_width: u32,
    thumb_height: u32,
    source_width: u32,
    source_height: u32,
    policy: String,
    reused: bool,
    render_ms: f64,
}
impl From<z30_core::editor::EditPreview> for DisplayPreview {
    fn from(p: z30_core::editor::EditPreview) -> Self {
        let width = p.rendered.width;
        let height = p.rendered.height;
        // These buffers are already sRGB-encoded. Do not apply gamma again.
        let bytes = |frame: &z30_core::develop::SrgbFrame| -> Vec<u8> {
            frame
                .pixels
                .iter()
                .flatten()
                .map(|v| (v.clamp(0., 1.) * 255.).round_ties_even() as u8)
                .collect()
        };
        let rgb = bytes(&p.rendered);
        let neutral = bytes(&p.neutral);
        let scale = 96. / width.max(height) as f32;
        let thumb_width = (width as f32 * scale).round().max(1.) as u32;
        let thumb_height = (height as f32 * scale).round().max(1.) as u32;
        let mut thumbnail = Vec::with_capacity((thumb_width * thumb_height * 3) as usize);
        for y in 0..thumb_height {
            for x in 0..thumb_width {
                let i =
                    ((y * height / thumb_height * width + x * width / thumb_width) * 3) as usize;
                thumbnail.extend_from_slice(&rgb[i..i + 3]);
            }
        }
        let policy = format!(
            "{} · 源 {} 位{}",
            p.policy,
            p.source_bits,
            if p.warnings.is_empty() {
                String::new()
            } else {
                format!(" · {}", p.warnings.join("；"))
            }
        );
        Self {
            binding:p.binding,
            rgb,
            neutral,
            thumbnail,
            width,
            height,
            thumb_width,
            thumb_height,
            source_width: p.source_width,
            source_height: p.source_height,
            policy,
            reused: p.reused_decode,
            render_ms: p.render_seconds * 1000.,
        }
    }
}

fn sync_recipe(state: &State, ui: &AppWindow) {
    let warp = state.edits.current().and_then(|r| r.recipe.warp);
    ui.set_has_warp_record(warp.is_some());
    ui.set_warp_amount(warp.as_ref().map_or(100., |w| (w.amount() * 100.) as f32));
    ui.set_has_warp(warp.as_ref().is_some_and(|w| w.active()));
    if ui.get_has_warp() {
        ui.set_show_skin_mask(false);
        ui.set_show_face_box(false);
    }
    if let Some(request) = state.edits.current() {
        let p = request.recipe.parameters;
        ui.set_exposure(p.exposure);
        ui.set_contrast(p.contrast);
        ui.set_highlights(p.highlights);
        ui.set_shadows(p.shadows);
        ui.set_temperature(p.temperature);
        ui.set_tint(p.tint);
        ui.set_saturation(p.saturation);
        ui.set_vibrance(p.vibrance);
        ui.set_sharpen(p.sharpen);
        skin_workflow::sync(state,ui,&request.recipe);
        let portrait=request.recipe.portrait;
        ui.set_skin_smooth(portrait.smooth);
        ui.set_skin_uniformity(request.recipe.skin_tone.uniformity);ui.set_skin_lighten(request.recipe.skin_tone.lighten);
        ui.set_has_face(portrait.face.is_some());
        let ready=portrait.face.zip(portrait.binding).is_some_and(|(face,binding)|face.rect[2]*(binding.width as f32)>=32. && face.rect[3]*(binding.height as f32)>=32.);
        ui.set_portrait_ready(ready);
        if let Some(face)=portrait.face {
            ui.set_face_x(face.rect[0]);ui.set_face_y(face.rect[1]);ui.set_face_w(face.rect[2]);ui.set_face_h(face.rect[3]);
            ui.set_face_points(ModelRc::new(VecModel::from(face.landmarks.iter().map(|p|FacePoint{x:p.x,y:p.y}).collect::<Vec<_>>())));
        } else {ui.set_face_points(ModelRc::new(VecModel::from(Vec::<FacePoint>::new())));}
        let candidates=state.face_candidates.as_ref().filter(|set|set.path==request.path);
        ui.set_multiple_faces(candidates.is_some_and(|set|set.faces.len()>1));
        ui.set_face_selection(candidates.map_or_else(||if portrait.face.is_some(){"已保存面部".to_owned()}else{"尚未定位面部".to_owned()},|set|format!("面部 {} / {}",set.selected+1,set.faces.len())).into());
    }
    ui.set_can_undo(!state.protected && state.edits.can_undo());
    ui.set_can_redo(!state.protected && state.edits.can_redo());
}
fn flush_edits(state: &mut State, ui: &AppWindow) {
    if let Err(e) = state.edits.commit() {
        ui.set_status(e.to_string().into());
    }
    state.last_edit = None;
    if state.dirty {
        save_state(state, ui);
    }
    sync_recipe(state, ui);
}
fn invalidate_ai(state: &mut State, ui: &AppWindow) {
    state.edit_revision = state.edit_revision.wrapping_add(1);
    state.ai_suggestion = None;
    ui.set_ai_suggestion_ready(false);
    ui.set_ai_report("".into());
    ui.set_ai_report_visible(false);
    if let Some(control) = &state.ai_control {
        control.store(true, Ordering::Release);
        ui.set_ai_status("照片或参数已变化；正在取消旧 AI 任务…".into());
    }
    if let Some(control)=&state.face_control {control.store(true,Ordering::Release);ui.set_face_status("照片或参数已变化，正在取消旧定位任务…".into());}
    if state.face_candidates.as_ref().is_some_and(|set|state.edits.current().is_none_or(|request|request.path!=set.path)) {state.face_candidates=None;}
}
fn apply_portrait_edit(state:&mut State,ui:&AppWindow,portrait:Portrait) -> z30_core::Result<Option<EditRequest>> {
    if !state.edits.apply_portrait(portrait)? {return Ok(None);}
    invalidate_ai(state,ui);state.last_edit=None;state.pending_preview=None;
    sync_recipe(state,ui);save_state(state,ui);ui.set_busy(true);ui.set_compare(false);
    Ok(state.edits.current())
}
fn prepare_exit(state: &mut State, ui: &AppWindow, discard: bool) -> bool {
    flush_edits(state, ui);
    if state.dirty && !discard {
        ui.set_exit_warning(true);
        return false;
    }
    if discard {
        state.dirty = false;
    }
    if !state.batch.paused && !state.batch_protected {
        let _ = change_batch(state, ui, |queue| { queue.pause(); Ok(()) });
    }
    if let Some((_, control)) = &state.batch_control {
        control.cancel();
        state.close_after_batch = true;
        ui.set_batch_status("退出前正在取消当前批量照片；其余任务已暂停…".into());
    }
    if state.batch_dialog {
        state.close_after_batch = true;
        ui.set_batch_status("请关闭批量目录对话框，随后退出".into());
    }
    if let Some(control)=&state.face_control {
        control.store(true,Ordering::Release);state.close_after_face=true;
        ui.set_face_status("退出前正在停止本机人像定位…".into());
    }
    if state.face_dialog {state.close_after_face=true;ui.set_face_status("请关闭组件目录对话框，随后退出".into());}
    if let Some(control) = &state.ai_control {
        control.store(true, Ordering::Release);
        state.close_after_ai = true;
        ui.set_ai_status("退出前正在停止 AI 请求并清理预览临时文件…".into());
        ui.set_exit_warning(false);
        return false;
    }
    if let Some(control) = &state.export_control {
        control.cancel();
        state.close_after_export = true;
        ui.set_export_status("退出前正在取消导出并清理临时文件…".into());
        ui.set_exit_warning(false);
        return false;
    }
    if state.export_dialog {
        state.close_after_export = true;
        ui.set_export_status("请关闭保存对话框，随后退出；此次不再导出".into());
        return false;
    }
    if state.batch_control.is_some() || state.batch_dialog || state.face_control.is_some() || state.face_dialog {
        return false;
    }
    true
}

fn refresh_batch(state: &State, ui: &AppWindow) {
    let counts = state.batch.counts();
    ui.set_batch_paused(state.batch.paused);
    ui.set_batch_running(state.batch_control.is_some() || state.batch_dialog);
    ui.set_batch_has_queued(counts.queued > 0);
    ui.set_batch_has_retry(state.batch.jobs.iter().any(|job| {
        matches!(job.state, batch::JobState::Failed | batch::JobState::Interrupted | batch::JobState::Cancelled)
            && job.attempts < 5 && !job.destination.exists()
    }));
    ui.set_batch_has_records(counts.total > 0);
    ui.set_batch_protected(state.batch_protected);
    if counts.total == 0 && !state.batch_protected {
        ui.set_batch_status("".into());
        ui.set_batch_report("".into());
        return;
    }
    let summary = format!(
        "批量 {}/{} 完成 · {} 运行 · {} 待处理 · {} 失败/中断 · {} 取消{}{}",
        counts.done, counts.total, counts.running, counts.queued,
        counts.failed + counts.interrupted, counts.cancelled,
        if state.batch.paused { " · 已暂停" } else { "" },
        if state.batch_protected { " · 队列只读，请检查磁盘记录" } else { "" },
    );
    ui.set_batch_status(summary.into());
    let details = state.batch.jobs.iter().map(|job| {
        let name = job.source.file_name().unwrap_or_default().to_string_lossy();
        format!("#{:03} {:?} · {} → {}{}", job.id, job.state, name,
            job.destination.display(), if job.last_error.is_empty() { String::new() } else { format!("\n  {}", job.last_error) })
    }).collect::<Vec<_>>().join("\n");
    ui.set_batch_report(details.into());
}

fn change_batch<T>(state: &mut State, ui: &AppWindow, update: impl FnOnce(&mut BatchQueue) -> z30_core::Result<T>) -> z30_core::Result<T> {
    if state.batch_protected {
        return Err(z30_core::Error::Policy("批量队列读取/保存失败；未覆盖原记录".into()));
    }
    let mut next = state.batch.clone();
    let value = update(&mut next)?;
    if let Err(error) = state.batch_store.save(&next) {
        state.batch_protected = true;
        refresh_batch(state, ui);
        return Err(error);
    }
    state.batch = next;
    refresh_batch(state, ui);
    Ok(value)
}

fn save_state(state: &mut State, ui: &AppWindow) {
    if state.protected {
        return;
    }
    state.dirty = true;
    match state.store.save(&state.edits.session) {
        Ok(()) => {
            state.dirty = false;
            ui.set_draft_status("照片参数与评分已保存到本机".into());
        }
        Err(error) => ui.set_draft_status(format!("草稿保存失败，请勿直接退出：{error}").into()),
    }
}

fn photo_row(photo: &z30_core::session::Photo) -> PhotoView {
    PhotoView {
        name: photo.name().into(),
        detail: photo
            .path
            .extension()
            .unwrap_or_default()
            .to_string_lossy()
            .to_uppercase()
            .into(),
        stars: "★".repeat(photo.rating.into()).into(),
        thumbnail: Default::default(),
    }
}
fn slint_image(bytes: &[u8], w: u32, h: u32) -> slint::Image {
    slint::Image::from_rgb8(SharedPixelBuffer::<Rgb8Pixel>::clone_from_slice(
        bytes, w, h,
    ))
}
fn fingerprint(bytes: &[u8]) -> String {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    bytes.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}
fn evaluate_ai(
    request: &EditRequest,
    source: &ExportSource,
    backend: &AiBackend,
    goal: &str,
    options: Options,
    current: &SharedPixelBuffer<Rgb8Pixel>,
    neutral: &SharedPixelBuffer<Rgb8Pixel>,
    compute: &Mutex<()>,
    engine: &Mutex<PreviewEngine>,
    cancel: &AtomicBool,
) -> z30_core::Result<AiProduct> {
    source.unchanged()?;
    let current_jpeg = ai_cli::encode_jpeg(current)?;
    let neutral_jpeg = ai_cli::encode_jpeg(neutral)?;
    let raw = request
        .path
        .extension()
        .is_some_and(|v| v.eq_ignore_ascii_case("nef"));
    let advice = match backend {
        AiBackend::Codex => ai_cli::run(
            request.recipe.clone(),
            goal,
            raw,
            &neutral_jpeg,
            &current_jpeg,
            cancel,
        )?,
        AiBackend::Compatible(config) => ai_http::run(
            config,
            request.recipe.clone(),
            goal,
            raw,
            &neutral_jpeg,
            &current_jpeg,
            cancel,
        )?,
    };
    let bounded = options.constrain(request.recipe.parameters, advice.adjustments.parameters())?;
    let reference = ai::metrics(current.as_bytes(), current.width(), current.height())?;
    let mut last_reasons = Vec::new();
    let _guard = compute.lock().unwrap_or_else(|e| e.into_inner());
    let mut engine = engine.lock().unwrap_or_else(|e| e.into_inner());
    for fraction in [1.0, 0.75, 0.5, 0.25, 0.0] {
        if cancel.load(Ordering::Acquire) {
            return Err(z30_core::Error::Policy(
                "AI 调色已取消；没有应用参数".into(),
            ));
        }
        source.unchanged()?;
        let parameters = ai::blend(request.recipe.parameters, bounded, fraction)?;
        let recipe = z30_core::recipe::Recipe {
            parameters,
            ..request.recipe.clone()
        };
        let preview = engine.preview_with_cancel(&request.path, recipe,||cancel.load(Ordering::Acquire))?;
        let rgb: Vec<u8> = preview
            .rendered
            .pixels
            .iter()
            .flatten()
            .map(|v| (v.clamp(0., 1.) * 255.).round_ties_even() as u8)
            .collect();
        let measure = ai::metrics(&rgb, preview.rendered.width, preview.rendered.height)?;
        let reasons = ai::degradation(reference, measure);
        if reasons.is_empty() || fraction == 0.0 {
            source.unchanged()?;
            let used_strength = (options.strength * fraction).round() as u32;
            let quality_note = if last_reasons.is_empty() {
                "画质检测通过".to_owned()
            } else {
                format!("画质回退至 {used_strength}%：{}", last_reasons.join("、"))
            };
            return Ok(AiProduct {
                advice,
                parameters,
                preview: preview.into(),
                used_strength,
                quality_note,
            });
        }
        last_reasons = reasons;
    }
    unreachable!("zero-strength candidate must be accepted")
}

fn display_ai_preview(
    ui: &AppWindow,
    preview: &DisplayPreview,
    rows: &Rc<VecModel<PhotoView>>,
    selected: Option<usize>,
) {
    ui.set_preview_aspect(preview.width as f32 / preview.height as f32);
    ui.set_preview(slint_image(&preview.rgb, preview.width, preview.height));
    ui.set_original_preview(slint_image(&preview.neutral, preview.width, preview.height));
    ui.set_preview_fingerprint(fingerprint(&preview.rgb).into());
    ui.set_neutral_fingerprint(fingerprint(&preview.neutral).into());
    ui.set_has_preview(true);
    ui.set_colour_info(preview.policy.clone().into());
    if let Some(i) = selected {
        if let Some(mut row) = rows.row_data(i) {
            row.thumbnail = slint_image(
                &preview.thumbnail,
                preview.thumb_width,
                preview.thumb_height,
            );
            rows.set_row_data(i, row);
        }
    }
}

fn ai_report(product: &AiProduct) -> String {
    format!(
        "{}\n\n{}\n\n画面观察\n{}\n\n建议局限\n{}\n\n{} · 实际强度 {}%",
        product.advice.title,
        product.advice.summary,
        product
            .advice
            .observations
            .iter()
            .map(|s| format!("• {s}"))
            .collect::<Vec<_>>()
            .join("\n"),
        product.advice.limitations,
        product.quality_note,
        product.used_strength
    )
}

fn apply_ai_product(
    state: &mut State,
    ui: &AppWindow,
    rows: &Rc<VecModel<PhotoView>>,
    product: AiProduct,
) {
    match state.edits.apply_ai(product.parameters) {
        Ok(changed) => {
            if changed {
                state.edit_revision = state.edit_revision.wrapping_add(1);
                state.last_edit = None;
                state.pending_preview = None;
                sync_recipe(state, ui);
                display_ai_preview(ui, &product.preview, rows, state.edits.session.selected);
                state.displayed=state.edits.current().map(|request|Displayed{request,binding:product.preview.binding,size:(product.preview.width,product.preview.height)});
                save_state(state, ui);
            }
            ui.set_ai_status(
                format!(
                    "AI {} · 强度 {}% · {} · {}",
                    if changed {
                        "已应用，可撤销"
                    } else {
                        "建议保持当前参数"
                    },
                    product.used_strength,
                    product.advice.title,
                    product.quality_note
                )
                .into(),
            );
            ui.set_ai_suggestion_ready(false);
        }
        Err(e) => ui.set_ai_status(format!("AI 建议未应用：{e}").into()),
    }
}
fn data_root() -> PathBuf {
    std::env::var_os("Z30_NATIVE_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            std::env::var_os("LOCALAPPDATA")
                .map(PathBuf::from)
                .unwrap_or_else(std::env::temp_dir)
                .join("Z30StudioNative")
        })
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        if !std::env::args_os().any(|a| a == "--qa-ready") {
            rfd::MessageDialog::new()
                .set_title("Z30 Studio 启动失败")
                .set_description(error.to_string())
                .set_level(rfd::MessageLevel::Error)
                .show();
        }
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let launched = Instant::now();
    let (mut qa, initial_photos) = qa::Harness::arguments()?;
    slint::BackendSelector::new()
        .backend_name("winit".into())
        .renderer_name("software".into())
        .select()?;
    let store = SessionStore::new(data_root())?;
    let (mut session, protected, mut restore_status) = match store.load() {
        Ok(s) => (s, false, "本机草稿已读取".to_owned()),
        Err(error) => (
            Session::default(),
            true,
            format!("草稿读取失败；已保留原文件，会话只读：{error}"),
        ),
    };
    let mut initial_dirty = false;
    let batch_store = BatchStore::new(data_root())?;
    let (batch, batch_protected, batch_load_error) = match batch_store.load() {
        Ok(queue) => (queue, false, None),
        Err(error) => (BatchQueue::default(), true, Some(error.to_string())),
    };
    if !protected && !initial_photos.is_empty() {
        for path in initial_photos {
            if let Err(e) = session.add(&path) {
                restore_status = format!("导入失败：{e}");
            }
        }
        if let Err(e) = store.save(&session) {
            initial_dirty = true;
            restore_status = format!("导入后草稿保存失败：{e}");
        }
    }
    let rows = Rc::new(VecModel::from(
        session.photos.iter().map(photo_row).collect::<Vec<_>>(),
    ));
    let state = Rc::new(RefCell::new(State {
        edits: EditingSession::new(session),
        last_edit: None,
        pending_preview: None,
        displayed:None,
        export_control: None,
        export_dialog: false,
        close_after_export: false,
        batch,
        batch_store,
        batch_protected,
        batch_dialog: false,
        batch_control: None,
        close_after_batch: false,
        store,
        protected,
        dirty: initial_dirty,
        edit_revision: 0,
        ai_control: None,
        ai_suggestion: None,
        close_after_ai: false,
        face_assets: std::env::var_os("Z30_VISION_DIR").map(PathBuf::from).or_else(||std::env::current_exe().ok().and_then(|path|path.parent().map(|parent|parent.join("vision"))).filter(|path|path.is_dir())),
        skin_assets: std::env::var_os("Z30_SKIN_DIR").map(PathBuf::from).or_else(||std::env::current_exe().ok().and_then(|p|p.parent().map(|d|d.join("skin"))).filter(|p|p.is_dir())),
        skin_overlay: RefCell::new(None),
        face_control: None,
        face_dialog: false,
        close_after_face: false,
        face_candidates: None,
    }));
    let ui = AppWindow::new()?;
    ui.set_ai_ready(true);
    ui.set_face_assets_ready(state.borrow().face_assets.is_some());
    ui.set_skin_assets_ready(state.borrow().skin_assets.is_some());
    ui.set_face_status(if state.borrow().face_assets.is_some(){"本机组件将在检测时校验；没有发送照片"}else{"选择本机人像组件目录；基础编辑无需此组件"}.into());
    ui.set_photos(ModelRc::from(Rc::clone(&rows)));
    ui.set_can_import(!protected);
    ui.set_draft_status(restore_status.into());
    refresh_batch(&state.borrow(), &ui);
    if let Some(error) = batch_load_error {
        ui.set_batch_status(format!("批量记录读取失败，原文件已保留：{error}").into());
    }
    let raw_dll = z30_core::runtime::raw_library(
        &std::env::current_exe()?,
        std::env::var_os("Z30_LIBRAW_DLL").as_deref(),
    )?;
    let engine = Arc::new(Mutex::new(PreviewEngine::new(raw_dll.clone())?));
    // Serialize full-image computation so export + a new selection cannot hold
    // two full RAW decodes at once. The event loop remains independent.
    let compute = Arc::new(Mutex::new(()));
    let full_cache = Arc::new(Mutex::new(FullFrameCache::default()));
    let preview_compute = Arc::clone(&compute);
    let preview_engine = Arc::clone(&engine);
    let preview_full_cache = Arc::clone(&full_cache);
    let preview_raw_dll = raw_dll.clone();
    let worker = Rc::new(LatestWorker::new_cancellable(move |request: EditRequest,preview_cancel| {
        let _guard = preview_compute.lock().unwrap_or_else(|e| e.into_inner());
        preview_full_cache.lock().unwrap_or_else(|e| e.into_inner())
            .discard_other_source(&request.path, request.recipe.white_balance, preview_raw_dll.as_deref());
        let mut engine = preview_engine.lock().unwrap_or_else(|e| e.into_inner());
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            engine
                .preview_with_cancel(&request.path, request.recipe.clone(),||preview_cancel.cancelled())
                .map(DisplayPreview::from)
        }))
        .unwrap_or_else(|_| {
            engine.clear();
            Err(z30_core::Error::Policy("显影异常；原片未修改".into()))
        });
        (request, result)
    }));
    let (destinations, selected_destinations) =
        mpsc::channel::<(ExportSource, Format, Option<PathBuf>)>();
    let (exports, completed_exports) = mpsc::channel::<z30_core::Result<ExportReceipt>>();
    let (batch_destinations, selected_batch_destinations) =
        mpsc::channel::<(Vec<Photo>, Format, RecipeMode, Option<PathBuf>)>();
    let (batch_results, completed_batch) =
        mpsc::channel::<(u64, bool, z30_core::Result<ExportReceipt>)>();
    let (ai_results, completed_ai) = mpsc::channel::<AiOutcome>();
    let (face_results, completed_faces) = mpsc::channel::<FaceOutcome>();
    let (face_directories, picked_face_directories) = mpsc::channel::<Option<PathBuf>>();
    let face_engine = Arc::new(Mutex::new(None::<(PathBuf,z30_vision::detector::Detector)>));
    // Only the explicitly enabled local QA harness can inject a save-dialog
    // choice. The same production callback captures and starts the job.
    let export_override = Rc::new(RefCell::new(None::<PathBuf>));
    let batch_override = Rc::new(RefCell::new(None::<PathBuf>));
    let face_override = Rc::new(RefCell::new(None::<PathBuf>));
    let skin=Rc::new(skin_workflow::bind(&ui,Rc::clone(&state),Rc::clone(&worker),Arc::clone(&engine),Arc::clone(&compute),Arc::clone(&full_cache),raw_dll.clone()));
    let repairs=repair_workflow::bind(&ui,Rc::clone(&state),Rc::clone(&worker),Arc::clone(&engine),Arc::clone(&compute));
    let warps=warp_workflow::bind(&ui,Rc::clone(&state),Rc::clone(&worker));
    let blemishes=blemish_workflow::bind(&ui,Rc::clone(&state),Rc::clone(&worker),Arc::clone(&engine),Arc::clone(&compute));
    let (picked, files) = mpsc::channel::<Vec<PathBuf>>();
    {
        let weak = ui.as_weak();
        ui.on_import_files(move || {
            let Some(ui) = weak.upgrade() else { return };
            ui.set_can_import(false);
            ui.set_status("请选择本机照片…".into());
            let picked = picked.clone();
            std::thread::spawn(move || {
                let paths = rfd::FileDialog::new()
                    .set_title("导入照片（原片保持只读）")
                    .add_filter("照片", &["nef", "jpg", "jpeg", "png"])
                    .pick_files()
                    .unwrap_or_default();
                let _ = picked.send(paths);
            });
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        let worker = Rc::clone(&worker);
        ui.on_select_photo(move |index| {
            let Some(ui) = weak.upgrade() else { return };
            let Ok(index) = usize::try_from(index) else {
                return;
            };
            let mut state = state.borrow_mut();
            let request = match state.edits.select(index) {
                Ok(Some(request)) => request,
                Ok(None) => return,
                Err(e) => {
                    ui.set_status(e.to_string().into());
                    return;
                }
            };
            invalidate_ai(&mut state, &ui);
            state.last_edit = None;
            state.pending_preview = None;
            ui.set_photo_name(state.edits.session.photos[index].name().into());
            sync_recipe(&state, &ui);
            worker.submit(request);
            ui.set_selected_index(index as i32);
            ui.set_busy(true);
            ui.set_has_preview(false);
            ui.set_engine_ready(false);
            ui.set_preview(Default::default());
            ui.set_original_preview(Default::default());
            ui.set_compare(false);
            ui.set_zoom(1.0);
            ui.set_pan_x(0.);ui.set_pan_y(0.);state.displayed=None;
            ui.set_status("后台读取并显影照片…".into());
            save_state(&mut state, &ui);
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        let rows = Rc::clone(&rows);
        ui.on_rate_photo(move |rating| {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            if state.protected || !(0..=5).contains(&rating) {
                return;
            }
            if let Some(i) = state.edits.session.selected {
                state.edits.session.photos[i].rating = rating as u8;
                let mut row = rows.row_data(i).unwrap();
                row.stars = "★".repeat(rating as usize).into();
                rows.set_row_data(i, row);
                save_state(&mut state, &ui);
            }
        });
    }
    {
        let weak = ui.as_weak();
        let worker = Rc::clone(&worker);
        let state = Rc::clone(&state);
        ui.on_cancel_preview(move || {
            worker.cancel();
            let mut state = state.borrow_mut();
            state.pending_preview = None;
            if let Some(ui) = weak.upgrade() {
                invalidate_ai(&mut state, &ui);
                ui.set_busy(false);
                ui.set_has_preview(false);
                ui.set_engine_ready(false);
                ui.set_status("预览已取消；参数已保留，可点重新预览".into());
            }
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        let worker = Rc::clone(&worker);
        ui.on_adjust(move |name, value| {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            if state.protected || (!ui.get_engine_ready() && name.as_str() != "warp_clear") {
                return;
            }
            let result = match name.as_str() {
                "warp_amount" => state.edits.adjust_warp_amount(value as f64 / 100.),
                "warp_clear" => state.edits.apply_warp(None),
                _ => state.edits.adjust(name.as_str(), value),
            };
            match result {
                Ok(true) => {
                    invalidate_ai(&mut state, &ui);
                    state.dirty = true;
                    state.last_edit = Some(Instant::now());
                    state.pending_preview = state.edits.current();
                    worker.cancel(); // Reject an older result during the debounce window too.
                    ui.set_busy(true);
                    ui.set_compare(false);
                    ui.set_draft_status("正在自动保存参数…".into());
                    ui.set_status("正在更新预览…".into());
                    sync_recipe(&state, &ui);
                }
                Err(e) => {
                    sync_recipe(&state, &ui);
                    ui.set_status(e.to_string().into());
                }
                _ => {}
            }
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        let worker = Rc::clone(&worker);
        let action = Rc::new(move |kind| {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            if state.protected {
                return;
            }
            let changed = match kind {
                0 => state.edits.undo(),
                1 => state.edits.redo(),
                _ => state.edits.reset(),
            };
            match changed {
                Ok(true) => {
                    invalidate_ai(&mut state, &ui);
                    state.pending_preview = None;
                    state.last_edit = None;
                    sync_recipe(&state, &ui);
                    if let Some(request) = state.edits.current() {
                        worker.submit(request);
                    }
                    ui.set_busy(true);
                    ui.set_compare(false);
                    save_state(&mut state, &ui);
                }
                Err(e) => ui.set_status(e.to_string().into()),
                _ => {}
            }
        });
        let undo = Rc::clone(&action);
        ui.on_undo_edit(move || undo(0));
        let redo = Rc::clone(&action);
        ui.on_redo_edit(move || redo(1));
        ui.on_reset_edits(move || action(2));
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        ui.on_retry_preview(move || {
            let Some(ui) = weak.upgrade() else { return };
            let selected = state.borrow().edits.session.selected;
            if let Some(i) = selected {
                ui.invoke_select_photo(i as i32);
            }
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        let ai_results = ai_results.clone();
        let compute = Arc::clone(&compute);
        let engine = Arc::clone(&engine);
        ui.on_auto_grade(move || {
            let Some(ui)=weak.upgrade() else {return};
            if !ui.get_has_preview() || ui.get_busy() || ui.get_ai_busy() || ui.get_repair_busy() {return}
            let user_goal=ui.get_ai_goal();
            if user_goal.chars().count()>350 {
                ui.set_ai_status("补充目标最多 350 字；没有发送照片".into());return;
            }
            let style=match ui.get_ai_style() {
                1=>"柔和人像，保持自然肤色和面部层次，不增加生硬对比或过度饱和。",
                2=>"温暖但克制的胶片氛围，柔和高光，保留暗部层次，不声称复刻特定胶片。",
                3=>"产品与静物，保持物体颜色可信、背景干净，避免强制把暖光白色变成纯白。",
                4=>"保留夜景与低调氛围，控制灯光溢出，不把夜景整体变成白天，不承诺降噪。",
                _=>"自然通透，保持现场氛围与真实色彩。",
            };
            let goal=format!("{} 风格偏好：{} 用户补充风格数据：{}",ai::AUTO_GOAL,style,user_goal);
            let options=Options{strength:ui.get_ai_strength(),..Options::default()};
            if let Err(e)=options.validate() {ui.set_ai_status(e.to_string().into());return}
            let backend=match ui.get_ai_provider() {
                0 => AiBackend::Codex,
                selected => match ai_http::Config::from_selection(selected,
                    &ui.get_ai_api_base(),&ui.get_ai_model(),&ui.get_ai_api_key()) {
                    Ok(config)=>AiBackend::Compatible(config),
                    Err(error)=>{ui.set_ai_status(error.to_string().into());return;}
                },
            };
            let backend_name=if matches!(&backend,AiBackend::Codex) {"Codex CLI"} else {"兼容接口"};
            let Some(current)=ui.get_preview().to_rgb8() else {
                ui.set_ai_status("无法读取当前预览；没有发送照片".into());return;
            };
            let Some(neutral)=ui.get_original_preview().to_rgb8() else {
                ui.set_ai_status("无法读取中性预览；没有发送照片".into());return;
            };
            if (current.width(),current.height())!=(neutral.width(),neutral.height()) {
                ui.set_ai_status("中性与当前预览尺寸不一致；没有发送照片".into());return;
            }
            let mut state=state.borrow_mut();
            if state.protected || state.ai_control.is_some() || state.batch_control.is_some() || state.batch_dialog || state.face_control.is_some() || state.face_dialog {
                ui.set_ai_status("AI 任务正在结束，或草稿处于只读保护；请稍后手动重试".into());return;
            }
            let Some(request)=state.edits.current() else {return};
            let source=match ExportSource::new(request.clone()) {
                Ok(v)=>v, Err(e)=>{ui.set_ai_status(e.to_string().into());return;}
            };
            let revision=state.edit_revision;
            let auto_apply=ui.get_ai_mode()==0;
            state.ai_suggestion=None;
            ui.set_ai_suggestion_ready(false);
            ui.set_ai_report("".into());
            ui.set_ai_report_visible(false);
            let cancel=Arc::new(AtomicBool::new(false));
            state.ai_control=Some(Arc::clone(&cancel));
            ui.set_ai_busy(true);
            if !matches!(&backend,AiBackend::Codex) {ui.set_ai_api_key("".into());}
            ui.set_ai_status(format!("{backend_name} 分析中：仅发送两张无 EXIF 的 sRGB JPEG 预览、当前九参数与目标；原片不上传。可取消。").into());
            let sender=ai_results.clone();
            let compute=Arc::clone(&compute);
            let engine=Arc::clone(&engine);
            std::thread::spawn(move || {
                let result=std::panic::catch_unwind(std::panic::AssertUnwindSafe(||
                    evaluate_ai(&request,&source,&backend,&goal,options,&current,&neutral,&compute,&engine,&cancel)
                )).unwrap_or_else(|_|Err(z30_core::Error::Policy("AI 后台任务异常；没有应用参数".into())));
                let _=sender.send(AiOutcome{request,source,revision,auto_apply,result});
            });
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        ui.on_cancel_ai(move || {
            let Some(ui) = weak.upgrade() else { return };
            if let Some(control) = &state.borrow().ai_control {
                control.store(true, Ordering::Release);
                ui.set_ai_status("正在停止 AI 请求并删除临时预览…".into());
            }
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        ui.on_dismiss_ai_suggestion(move || {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            if state.ai_control.is_some() {
                return;
            }
            state.ai_suggestion = None;
            ui.set_ai_suggestion_ready(false);
            ui.set_ai_status("".into());
            ui.set_ai_report("".into());
            ui.set_ai_report_visible(false);
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        let rows = Rc::clone(&rows);
        ui.on_apply_ai_suggestion(move || {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            let Some(suggestion) = state.ai_suggestion.take() else {
                return;
            };
            ui.set_ai_suggestion_ready(false);
            if state.edit_revision != suggestion.revision
                || state.edits.current().as_ref() != Some(&suggestion.request)
            {
                ui.set_ai_status("照片或参数已变化，旧建议已失效；请手动重新分析".into());
                return;
            }
            if let Err(e) = suggestion.source.unchanged() {
                ui.set_ai_status(format!("原片已变化，旧建议不再适用：{e}").into());
                return;
            }
            apply_ai_product(&mut state, &ui, &rows, suggestion.product);
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        let export_override = Rc::clone(&export_override);
        ui.on_export_photo(move || {
            let Some(ui) = weak.upgrade() else { return };
            if ui.get_repair_busy(){ui.set_status("请等待取样完成后再导出".into());return;}
            let mut state = state.borrow_mut();
            if state.protected
                || ui.get_busy()
                || !ui.get_has_preview()
                || state.export_dialog
                || state.export_control.is_some()
                || state.batch_control.is_some()
                || state.batch_dialog
                || state.face_control.is_some()
                || state.face_dialog
            {
                return;
            }
            let Some(edit) = state.edits.current() else {
                return;
            };
            let source = match ExportSource::new(edit) {
                Ok(v) => v,
                Err(e) => {
                    ui.set_export_status(e.to_string().into());
                    return;
                }
            };
            flush_edits(&mut state, &ui);
            let (format, extension) = match ui.get_export_format() {
                1 => (Format::Png, "png"),
                2 => (Format::Tiff16, "tif"),
                _ => (Format::Jpeg, "jpg"),
            };
            let name = format!(
                "{}-z30.{extension}",
                source
                    .edit
                    .path
                    .file_stem()
                    .unwrap_or_default()
                    .to_string_lossy()
            );
            state.export_dialog = true;
            ui.set_export_busy(true);
            ui.set_export_status("选择新的导出文件名…".into());
            let destinations = destinations.clone();
            let chosen = export_override.borrow_mut().take();
            std::thread::spawn(move || {
                let path = chosen.or_else(|| {
                    rfd::FileDialog::new()
                        .set_title("导出调整后的照片（不覆盖已有文件）")
                        .add_filter(extension, &[extension])
                        .set_file_name(name)
                        .save_file()
                });
                let _ = destinations.send((source, format, path));
            });
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        ui.on_cancel_export(move || {
            if let Some(control) = &state.borrow().export_control {
                control.cancel();
            }
            if let Some(ui) = weak.upgrade() {
                ui.set_export_status("正在取消；等待当前处理阶段结束…".into());
            }
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        let batch_destinations = batch_destinations.clone();
        let batch_override = Rc::clone(&batch_override);
        ui.on_batch_export(move || {
            let Some(ui) = weak.upgrade() else { return };
            if ui.get_repair_busy(){ui.set_status("请等待取样完成后再批量导出".into());return;}
            let mut state = state.borrow_mut();
            if state.protected || state.batch_protected || state.batch_dialog
                || state.batch_control.is_some() || state.export_dialog
                || state.export_control.is_some() || state.ai_control.is_some() || state.face_control.is_some() || state.face_dialog
                || state.edits.session.photos.is_empty()
            { return; }
            flush_edits(&mut state, &ui);
            if state.dirty {
                ui.set_batch_status("请先解决草稿保存失败，再创建批量任务".into());
                return;
            }
            let mode = if ui.get_batch_mode() == 1 {
                let Some(request) = state.edits.current() else {
                    ui.set_batch_status("请先选择公共配方照片".into());
                    return;
                };
                RecipeMode::Shared(request.recipe)
            } else { RecipeMode::PerPhoto };
            let photos = state.edits.session.photos.clone();
            let (format, label) = match ui.get_export_format() {
                1 => (Format::Png, "PNG"),
                2 => (Format::Tiff16, "TIFF16"),
                _ => (Format::Jpeg, "JPEG"),
            };
            state.batch_dialog = true;
            refresh_batch(&state, &ui);
            ui.set_batch_status(format!("选择 {label} 批量输出目录…").into());
            let chosen = batch_override.borrow_mut().take();
            let sender = batch_destinations.clone();
            std::thread::spawn(move || {
                let path = chosen.or_else(|| rfd::FileDialog::new()
                    .set_title("选择批量输出目录（不会覆盖已有文件）")
                    .pick_folder());
                let _ = sender.send((photos, format, mode, path));
            });
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        ui.on_batch_pause_resume(move || {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            let result = change_batch(&mut state, &ui, |queue| {
                if queue.paused { queue.resume(); } else { queue.pause(); }
                Ok(())
            });
            if let Err(error) = result { ui.set_batch_status(format!("批量暂停/继续失败：{error}").into()); }
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        ui.on_batch_cancel(move || {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            if let Some((_, control)) = &state.batch_control { control.cancel(); }
            match change_batch(&mut state, &ui, |queue| Ok(queue.cancel_pending())) {
                Ok(count) => ui.set_batch_status(format!("已取消 {count} 个待处理任务；当前照片正在停止…").into()),
                Err(error) => ui.set_batch_status(format!("批量取消记录失败：{error}").into()),
            }
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        ui.on_batch_retry(move || {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            match change_batch(&mut state, &ui, |queue| {
                let count = queue.retry_failed();
                if count > 0 { queue.resume(); }
                Ok(count)
            }) {
                Ok(0) => ui.set_batch_status("无可重试任务；已有目标不会覆盖，请查看明细".into()),
                Ok(_) => {},
                Err(error) => ui.set_batch_status(format!("重试失败：{error}").into()),
            }
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        ui.on_batch_clear(move || {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            if let Err(error) = change_batch(&mut state, &ui, |queue| Ok(queue.clear_finished())) {
                ui.set_batch_status(format!("清理记录失败：{error}").into());
            }
            if state.batch.jobs.is_empty() { ui.set_batch_report_visible(false); }
        });
    }
    {
        let weak=ui.as_weak();let state=Rc::clone(&state);let sender=face_directories.clone();let chooser=Rc::clone(&face_override);
        ui.on_choose_face_assets(move || {
            let Some(ui)=weak.upgrade() else{return};let mut state=state.borrow_mut();
            if state.face_control.is_some() || state.face_dialog || state.export_dialog || state.batch_dialog {return;}
            state.face_dialog=true;ui.set_face_busy(true);ui.set_face_status("选择包含固定模型和运行库的人像组件目录…".into());
            let selected=chooser.borrow_mut().take();let sender=sender.clone();
            std::thread::spawn(move || {let path=selected.or_else(||rfd::FileDialog::new().set_title("选择本机人像组件目录").pick_folder());let _=sender.send(path);});
        });
    }
    {
        let weak=ui.as_weak();let state=Rc::clone(&state);let sender=face_results.clone();
        let compute=Arc::clone(&compute);let engine=Arc::clone(&engine);let models=Arc::clone(&face_engine);
        let cache=Arc::clone(&full_cache);let dll=raw_dll.clone();
        ui.on_detect_face(move || {
            let Some(ui)=weak.upgrade() else{return};let mut state=state.borrow_mut();
            if state.protected || state.face_control.is_some() || state.face_dialog || state.ai_control.is_some()
                || state.batch_control.is_some() || state.export_control.is_some() || state.export_dialog || ui.get_busy() {return;}
            let Some(assets)=state.face_assets.clone() else {ui.set_face_status("请先选择本机人像组件目录".into());return;};
            flush_edits(&mut state,&ui);
            let Some(request)=state.edits.current() else{return};
            let source=match ExportSource::new(request.clone()) {Ok(value)=>value,Err(error)=>{ui.set_face_status(error.to_string().into());return;}};
            let revision=state.edit_revision;let cancel=Arc::new(AtomicBool::new(false));state.face_control=Some(Arc::clone(&cancel));
            ui.set_face_busy(true);ui.set_face_status("本机 CPU 正在定位面部；照片不会发送到云端…".into());
            let (sender,compute,engine,models,cache,dll)=(sender.clone(),Arc::clone(&compute),Arc::clone(&engine),Arc::clone(&models),Arc::clone(&cache),dll.clone());
            std::thread::spawn(move || {
                let result=std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| -> z30_core::Result<(Vec<Face>,Binding)> {
                    let _guard=compute.lock().unwrap_or_else(|e|e.into_inner());
                    if cancel.load(Ordering::Acquire) {return Err(z30_core::Error::Policy("人像定位已取消".into()));}
                    source.unchanged()?;
                    cache.lock().unwrap_or_else(|e|e.into_inner()).discard_other_source(&request.path,request.recipe.white_balance,dll.as_deref());
                    let neutral_recipe=z30_core::recipe::Recipe{white_balance:request.recipe.white_balance,..Default::default()};
                    let preview=engine.lock().unwrap_or_else(|e|e.into_inner()).preview(&request.path,neutral_recipe)?;
                    let (width,height)=(preview.source_width,preview.source_height);let neutral=Arc::clone(&preview.neutral);drop(preview);
                    let mut models=models.lock().unwrap_or_else(|e|e.into_inner());
                    if models.as_ref().is_none_or(|(path,_)|path!=&assets) {*models=Some((assets.clone(),z30_vision::detector::Detector::open(&assets)?));}
                    let detected=models.as_mut().unwrap().1.detect(&neutral,&cancel)?;
                    let binding=Binding::capture(&request.path,width,height)?;source.unchanged()?;
                    if cancel.load(Ordering::Acquire) {return Err(z30_core::Error::Policy("人像定位已取消".into()));}
                    Ok((detected.faces,binding))
                })).unwrap_or_else(|_|Err(z30_core::Error::Policy("本机定位异常；未改动照片配方".into())));
                let _=sender.send(FaceOutcome{request,source,revision,result});
            });
        });
    }
    {
        let weak=ui.as_weak();let state=Rc::clone(&state);
        ui.on_cancel_face(move || {let state=state.borrow();if let Some(control)=&state.face_control {control.store(true,Ordering::Release);}if let Some(ui)=weak.upgrade(){ui.set_face_status(if state.face_dialog{"请在目录窗口取消选择"}else{"正在停止本机定位；不会应用旧结果…"}.into());}});
    }
    {
        let weak=ui.as_weak();let state=Rc::clone(&state);let worker=Rc::clone(&worker);
        ui.on_next_face(move || {
            let Some(ui)=weak.upgrade() else{return};let mut state=state.borrow_mut();if state.protected || state.face_control.is_some(){return;}
            let Some(current)=state.edits.current() else{return};
            let Some(set)=state.face_candidates.as_ref().filter(|set|set.path==current.path && !set.faces.is_empty()) else{return};
            let index=(set.selected+1)%set.faces.len();let portrait=Portrait{face:Some(set.faces[index]),binding:Some(set.binding),smooth:0.};
            match apply_portrait_edit(&mut state,&ui,portrait) {Ok(Some(request))=>{if let Some(set)=&mut state.face_candidates{set.selected=index;}sync_recipe(&state,&ui);worker.submit(request);ui.set_face_status("已切换面部，柔化强度从 0 开始；只处理当前面部".into());},Ok(None)=>{},Err(error)=>ui.set_face_status(error.to_string().into())}
        });
    }
    {
        let weak=ui.as_weak();let state=Rc::clone(&state);let worker=Rc::clone(&worker);
        ui.on_clear_portrait(move || {
            let Some(ui)=weak.upgrade() else{return};let mut state=state.borrow_mut();if state.protected{return;}
            state.face_candidates=None;
            match apply_portrait_edit(&mut state,&ui,Portrait::default()) {Ok(Some(request))=>{worker.submit(request);ui.set_face_status("已清除人像编辑，可撤销".into());},Ok(None)=>sync_recipe(&state,&ui),Err(error)=>ui.set_face_status(error.to_string().into())}
        });
    }
    let timer = slint::Timer::default();
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        let rows = Rc::clone(&rows);
        let worker = Rc::clone(&worker);
        timer.start(
            slint::TimerMode::Repeated,
            Duration::from_millis(50),
            move || {
                let Some(ui) = weak.upgrade() else { return };
                match qa.poll(launched) {
                    Ok(true) => {
                        if prepare_exit(&mut state.borrow_mut(), &ui, false) {
                            let _ = slint::quit_event_loop(); return;
                        }
                    }
                    Err(e) => ui.set_status(format!("QA 标记写入失败：{e}").into()),
                    _ => {}
                }
                for paths in files.try_iter() {
                    let mut state = state.borrow_mut();
                    let mut added = 0;
                    let mut errors = vec![];
                    for path in paths {
                        match state.edits.session.add(&path) {
                            Ok(true) => {
                                rows.push(photo_row(state.edits.session.photos.last().unwrap()));
                                added += 1
                            }
                            Ok(false) => {}
                            Err(e) => errors.push(e.to_string()),
                        }
                    }
                    ui.set_can_import(!state.protected);
                    save_state(&mut state, &ui);
                    let selection = state.edits.session.selected;
                    drop(state);
                    if let Some(i) = selection {
                        ui.invoke_select_photo(i as i32);
                    }
                    if !errors.is_empty() {
                        ui.set_status(format!("导入 {added} 张；{}", errors.join("；")).into());
                    } else if added == 0 {
                        ui.set_status("没有新增照片".into());
                    }
                }
                {
                    let mut state = state.borrow_mut();
                    if state.last_edit.is_some_and(|time| time.elapsed() >= Duration::from_millis(120)) {
                        if let Some(request) = state.pending_preview.take() { worker.submit(request); }
                    }
                    if state.last_edit.is_some_and(|time| time.elapsed() >= Duration::from_millis(450)) {
                        flush_edits(&mut state, &ui);
                    }
                }
                for (source, format, destination) in selected_destinations.try_iter() {
                    let mut state = state.borrow_mut(); state.export_dialog = false;
                    if state.close_after_export || destination.is_none() {
                        ui.set_export_busy(false); ui.set_export_status("已取消选择导出位置".into());
                    } else {
                        let control = Arc::new(ExportControl::default());
                        state.export_control = Some(Arc::clone(&control));
                        ui.set_export_cancellable(true);
                        let compute = Arc::clone(&compute); let exports = exports.clone(); let dll = raw_dll.clone();
                        let cache = Arc::clone(&full_cache);
                        std::thread::spawn(move || {
                            let _guard = compute.lock().unwrap_or_else(|e| e.into_inner());
                            let mut cache = cache.lock().unwrap_or_else(|e| e.into_inner());
                            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(||
                                source.run_with_cache(&destination.unwrap(), format, dll.as_deref(), &control, &mut cache)
                            )).unwrap_or_else(|_| Err(z30_core::Error::Policy("导出异常，原片保持不变".into())));
                            let _ = exports.send(result);
                        });
                    }
                }
                for directory in picked_face_directories.try_iter() {
                    let mut state=state.borrow_mut();state.face_dialog=false;ui.set_face_busy(state.face_control.is_some());
                    if state.close_after_face {continue;}
                    if let Some(directory)=directory {
                        if directory.is_absolute() && directory.join(z30_vision::detector::MODEL_NAME).is_file() && directory.join("onnxruntime.dll").is_file() {
                            state.face_assets=Some(directory);ui.set_face_assets_ready(true);ui.set_face_status("已选择组件；检测时会校验模型与 DLL，没有发送照片".into());
                        } else {ui.set_face_status("目录缺少固定模型或 onnxruntime.dll；请检查组件".into());}
                    }
                }
                skin.poll(&ui);
                repairs.poll(&ui);
                warps.sync(&ui);
                for outcome in completed_faces.try_iter() {
                    let mut state=state.borrow_mut();let cancelled=state.face_control.as_ref().is_some_and(|control|control.load(Ordering::Acquire));state.face_control=None;ui.set_face_busy(false);
                    if state.close_after_face {continue;}
                    if cancelled || state.edit_revision!=outcome.revision || state.edits.current().as_ref()!=Some(&outcome.request) {
                        ui.set_face_status("照片或参数已变化；旧定位结果未应用，可重新检测".into());continue;
                    }
                    if let Err(error)=outcome.source.unchanged(){ui.set_face_status(format!("原片已变化；定位结果未应用：{error}").into());continue;}
                    match outcome.result {
                        Ok((faces,binding)) if !faces.is_empty()=>{
                            let count=faces.len();let portrait=Portrait{face:Some(faces[0]),binding:Some(binding),smooth:0.};
                            state.face_candidates=Some(FaceCandidates{path:outcome.request.path,binding,faces,selected:0});
                            match apply_portrait_edit(&mut state,&ui,portrait) {
                                Ok(Some(request))=>{worker.submit(request);ui.set_show_face_box(true);ui.set_face_status(format!("已定位 {count} 张脸；选择当前面部后调整柔化强度，区域为几何估计").into());},
                                Ok(None)=>{sync_recipe(&state,&ui);ui.set_show_face_box(true);ui.set_face_status(format!("已重新定位 {count} 张脸，当前参数未改变").into());},Err(error)=>ui.set_face_status(error.to_string().into()),
                            }
                        },
                        Ok(_)=>ui.set_face_status("未找到达到置信度要求的面部，原配方保留".into()),
                        Err(error)=>ui.set_face_status(error.to_string().into()),
                    }
                }
                for result in completed_exports.try_iter() {
                    state.borrow_mut().export_control = None;
                    ui.set_export_busy(false); ui.set_export_cancellable(false);
                    ui.set_export_status(match result {
                        Ok(v) => format!("已导出 {} × {} · {:.1} 秒（解码 {:.1} / 调色 {:.1} / 编码 {:.1}{}）· {}", v.width, v.height,
                            v.seconds, v.decode_seconds, v.render_seconds, v.encode_seconds,
                            if v.reused_decode { "，复用全尺寸缓存" } else { "" }, v.destination.display()),
                        Err(e) => format!("导出未完成：{e}"),
                    }.into());
                }
                for (photos, format, mode, directory) in selected_batch_destinations.try_iter() {
                    let mut state = state.borrow_mut();
                    state.batch_dialog = false;
                    if state.close_after_batch || directory.is_none() {
                        refresh_batch(&state, &ui);
                        ui.set_batch_status("已取消选择批量输出目录".into());
                        continue;
                    }
                    let directory = directory.unwrap();
                    match change_batch(&mut state, &ui, |queue| {
                        let added = queue.enqueue(&photos, &directory, format, mode)?;
                        queue.resume();
                        Ok(added)
                    }) {
                        Ok(added) => ui.set_batch_status(format!("已保存 {added} 个批量任务，开始顺序导出…").into()),
                        Err(error) => ui.set_batch_status(format!("未创建批量任务：{error}").into()),
                    }
                }
                for (id, cancelled, result) in completed_batch.try_iter() {
                    let mut state = state.borrow_mut();
                    if state.batch_control.as_ref().is_none_or(|(active, _)| *active != id) {
                        ui.set_batch_status("收到不匹配的批量结果；队列已停止".into());
                        state.batch_protected = true;
                        continue;
                    }
                    state.batch_control = None;
                    match change_batch(&mut state, &ui, |queue| queue.finish(id, &result, cancelled)) {
                        Ok(()) => {
                            if let Err(error) = result {
                                ui.set_batch_status(format!("第 {id} 张导出未完成：{error}；详情见任务明细").into());
                            }
                        }
                        Err(error) => ui.set_batch_status(format!("批量结果保存失败；重启后会标为中断：{error}").into()),
                    }
                }
                {
                    let mut state = state.borrow_mut();
                    if !state.batch.paused && !state.batch_protected
                        && state.batch.counts().queued > 0
                        && state.batch_control.is_none() && !state.batch_dialog
                        && state.export_control.is_none() && !state.export_dialog
                        && state.ai_control.is_none() && state.face_control.is_none() && !state.face_dialog && !state.close_after_batch
                        && !state.close_after_export && !state.close_after_ai
                    {
                        match change_batch(&mut state, &ui, |queue| queue.start_next()) {
                            Ok(Some(job)) => {
                                let control = Arc::new(ExportControl::default());
                                state.batch_control = Some((job.id, Arc::clone(&control)));
                                refresh_batch(&state, &ui);
                                let compute = Arc::clone(&compute);
                                let sender = batch_results.clone();
                                let dll = raw_dll.clone();
                                let cache = Arc::clone(&full_cache);
                                std::thread::spawn(move || {
                                    let _guard = compute.lock().unwrap_or_else(|e| e.into_inner());
                                    let mut cache = cache.lock().unwrap_or_else(|e| e.into_inner());
                                    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(||
                                        job.run_reusing_cache(dll.as_deref(), &control, &mut cache)
                                    )).unwrap_or_else(|_| Err(z30_core::Error::Policy("批量显影异常；原片未修改".into())));
                                    let cancelled = control.cancelled.load(Ordering::Acquire);
                                    let _ = sender.send((job.id, cancelled, result));
                                });
                            }
                            Ok(None) => {},
                            Err(error) => ui.set_batch_status(format!("批量启动失败：{error}").into()),
                        }
                    }
                    if let Some((id, control)) = &state.batch_control {
                        ui.set_batch_status(format!("批量第 {id} 张 · {} · {}", control.stage(),
                            if state.batch.paused { "本张结束后暂停" } else { "可继续编辑照片" }).into());
                    }
                }
                for outcome in completed_ai.try_iter() {
                    let mut state=state.borrow_mut();
                    let cancelled=state.ai_control.as_ref().is_some_and(|v|v.load(Ordering::Acquire));
                    state.ai_control=None;
                    ui.set_ai_busy(false);
                    if state.close_after_ai {
                        state.ai_suggestion=None;
                        ui.set_ai_suggestion_ready(false);
                        if let Some(control)=&state.export_control {
                            control.cancel(); state.close_after_export=true;
                        }
                        if state.export_dialog {state.close_after_export=true;}
                        if !state.export_dialog && state.export_control.is_none()
                            && !state.batch_dialog && state.batch_control.is_none() && state.face_control.is_none() && !state.face_dialog && !state.dirty {
                            let _=slint::quit_event_loop();return;
                        }
                        continue;
                    }
                    if cancelled {
                        ui.set_ai_status("AI 调色已取消；未应用参数，可手动重试".into());
                        continue;
                    }
                    if state.edit_revision!=outcome.revision || state.edits.current().as_ref()!=Some(&outcome.request) {
                        ui.set_ai_status("照片或参数已变化；旧 AI 结果已丢弃，可手动重试".into());
                        continue;
                    }
                    if let Err(e)=outcome.source.unchanged() {
                        ui.set_ai_status(format!("原片已变化；AI 结果已丢弃：{e}").into());
                        continue;
                    }
                    match outcome.result {
                        Ok(product) if outcome.auto_apply => {
                            ui.set_ai_report(ai_report(&product).into());
                            apply_ai_product(&mut state,&ui,&rows,product)
                        },
                        Ok(product) => {
                            ui.set_ai_report(ai_report(&product).into());
                            ui.set_ai_status(format!("AI 建议已就绪 · 强度 {}% · {} · {}。点击应用建议或关闭。",product.used_strength,product.advice.title,product.quality_note).into());
                            state.ai_suggestion=Some(AiSuggestion{request:outcome.request,source:outcome.source,revision:outcome.revision,product});
                            ui.set_ai_suggestion_ready(true);
                        }
                        Err(e) => ui.set_ai_status(format!("AI 调色失败：{e}；可手动重试").into()),
                    }
                }
                {
                    let state = state.borrow();
                    if let Some(control) = &state.export_control {
                        let message = if control.cancelled.load(std::sync::atomic::Ordering::Acquire) {
                            "正在取消；等待当前处理阶段结束…".to_owned()
                        } else { format!("{} · 全尺寸 sRGB · 可继续编辑照片", control.stage()) };
                        ui.set_export_status(message.into());
                    }
                    if (state.close_after_export || state.close_after_batch || state.close_after_ai || state.close_after_face)
                        && !state.export_dialog && state.export_control.is_none()
                        && !state.batch_dialog && state.batch_control.is_none()
                        && state.ai_control.is_none() && state.face_control.is_none() && !state.face_dialog && !state.dirty {
                        let _ = slint::quit_event_loop(); return;
                    }
                }
                if let Ok(mut cache) = full_cache.try_lock() { cache.evict_if_idle(); }
                while let Some((revision, (request, result))) = worker.take_result() {
                    if !worker.current(revision) || state.borrow().edits.current().as_ref() != Some(&request) {
                        continue;
                    }
                    ui.set_busy(false);
                    match result {
                        Ok(preview) => {
                            state.borrow_mut().displayed=Some(Displayed{request:request.clone(),binding:preview.binding,size:(preview.width,preview.height)});
                            ui.set_preview_aspect(preview.width as f32 / preview.height as f32);
                            if let Err(e) = qa.preview_ready(
                                launched,
                                preview.source_width,
                                preview.source_height,
                            ) {
                                ui.set_status(format!("QA 预览记录失败：{e}").into());
                            }
                            ui.set_preview(slint_image(
                                &preview.rgb,
                                preview.width,
                                preview.height,
                            ));
                            ui.set_original_preview(slint_image(&preview.neutral, preview.width, preview.height));
                            ui.set_preview_fingerprint(fingerprint(&preview.rgb).into());
                            ui.set_neutral_fingerprint(fingerprint(&preview.neutral).into());
                            ui.set_has_preview(true);
                            ui.set_engine_ready(!state.borrow().protected);
                            ui.set_photo_info(
                                format!(
                                    "{} × {}  ·  预览 {} × {}",
                                    preview.source_width,
                                    preview.source_height,
                                    preview.width,
                                    preview.height
                                )
                                .into(),
                            );
                            ui.set_colour_info(preview.policy.into());
                            ui.set_status(format!("预览已更新 · {} · 显影 {:.0} ms", if preview.reused { "复用解码缓存" } else { "已读取原片" }, preview.render_ms).into());
                            if let Some(i) = state.borrow().edits.session.selected {
                                if let Some(mut row) = rows.row_data(i) {
                                    row.thumbnail = slint_image(
                                        &preview.thumbnail,
                                        preview.thumb_width,
                                        preview.thumb_height,
                                    );
                                    rows.set_row_data(i, row);
                                }
                            }
                        }
                        Err(error) => {
                            state.borrow_mut().displayed=None;
                            ui.set_has_preview(false);
                            ui.set_engine_ready(false);
                            ui.set_status(error.to_string().into());
                            ui.set_colour_info(
                                "未将不支持的色彩/RAW 输入静默当作已正确显影".into(),
                            );
                        }
                    }
                }
                // Derive candidate readiness from this tick's completed preview,
                // face and AI state; never publish "preview ready" with stale gates.
                blemishes.poll(&ui);
                match qa.command() {
                    Ok(Some((id, command))) => {
                        match command {
                            qa::Command::Snapshot => {}
                            qa::Command::BlemishFind=>ui.invoke_find_blemishes(),
                            qa::Command::BlemishCancel=>ui.invoke_cancel_blemishes(),
                            qa::Command::BlemishSelect{index,selected}=>ui.invoke_select_blemish(index,selected),
                            qa::Command::BlemishApply=>ui.invoke_apply_blemishes(),
                            qa::Command::BlemishStrength{value}=>{if value.is_finite()&&(0.0..=100.0).contains(&value){ui.set_blemish_strength(value);}},
                            qa::Command::WarpTool{enabled}=>ui.invoke_warp_tool(enabled),
                            qa::Command::WarpPointer{phase,x,y}=>ui.invoke_warp_pointer(phase,x,y),
                            qa::Command::WarpRadius{value}=>{if value.is_finite()&&(0.2..=20.0).contains(&value){ui.set_warp_radius(value);}},
                            qa::Command::RepairTool{mode}=>ui.invoke_repair_tool(mode),
                            qa::Command::RepairCanvas{x,y}=>ui.invoke_repair_canvas_click(x,y),
                            qa::Command::RepairSelect{index}=>ui.invoke_select_repair(index),
                            qa::Command::RepairAdjust{parameter,value}=>ui.invoke_adjust_repair(parameter.into(),value),
                            qa::Command::RepairPick=>ui.invoke_pick_repair_source(),
                            qa::Command::RepairSuggest=>ui.invoke_suggest_repair_source(),
                            qa::Command::RepairRemove=>ui.invoke_remove_repair(),
                            qa::Command::RepairClear=>ui.invoke_clear_repairs(),
                            qa::Command::RepairCancel=>ui.invoke_cancel_repair(),
                            qa::Command::RepairMarks{visible}=>ui.set_show_repair_marks(visible),
                            qa::Command::About{visible}=>ui.set_about_visible(visible),
                            qa::Command::View{zoom,pan_x,pan_y}=>{if zoom.is_finite()&&(1.0..=4.0).contains(&zoom)&&pan_x.is_finite()&&pan_y.is_finite(){ui.set_zoom(zoom);ui.set_pan_x(pan_x);ui.set_pan_y(pan_y);}},
                            qa::Command::Adjust { parameter, value } => ui.invoke_adjust(parameter.into(), value),
                            qa::Command::Select { index } => ui.invoke_select_photo(index),
                            qa::Command::Undo => ui.invoke_undo_edit(),
                            qa::Command::Redo => ui.invoke_redo_edit(),
                            qa::Command::Reset => ui.invoke_reset_edits(),
                            qa::Command::CancelPreview => ui.invoke_cancel_preview(),
                            qa::Command::RetryPreview => ui.invoke_retry_preview(),
                            qa::Command::CancelExport => ui.invoke_cancel_export(),
                            qa::Command::BatchStart { directory, format, mode } => {
                                if directory.is_absolute() && (0..=2).contains(&format) && (0..=1).contains(&mode) {
                                    *batch_override.borrow_mut() = Some(directory);
                                    ui.set_export_format(format);
                                    ui.set_batch_mode(mode);
                                    ui.invoke_batch_export();
                                    batch_override.borrow_mut().take();
                                }
                            }
                            qa::Command::BatchPauseResume => ui.invoke_batch_pause_resume(),
                            qa::Command::BatchCancel => ui.invoke_batch_cancel(),
                            qa::Command::BatchRetry => ui.invoke_batch_retry(),
                            qa::Command::BatchClear => ui.invoke_batch_clear(),
                            qa::Command::FaceAssets {directory} => {
                                if directory.is_absolute() {*face_override.borrow_mut()=Some(directory);ui.invoke_choose_face_assets();face_override.borrow_mut().take();}
                            }
                            qa::Command::SkinAssets { directory }=>{
                                if directory.is_absolute() {*skin.chooser.borrow_mut()=Some(directory);ui.invoke_choose_skin_assets();skin.chooser.borrow_mut().take();}
                            },
                            qa::Command::SegmentSkin=>ui.invoke_segment_skin(),
                            qa::Command::ShowSkinMask{visible}=>ui.set_show_skin_mask(visible),
                            qa::Command::DetectFace=>ui.invoke_detect_face(),
                            qa::Command::CancelFace=>ui.invoke_cancel_face(),
                            qa::Command::NextFace=>ui.invoke_next_face(),
                            qa::Command::ClearPortrait=>ui.invoke_clear_portrait(),
                            qa::Command::AiStart { mode, strength, goal, provider, base_url, model, key } => {
                                if let Some(v)=mode {ui.set_ai_mode(v);}
                                if let Some(v)=strength {ui.set_ai_strength(v);}
                                if let Some(v)=goal {ui.set_ai_goal(v.into());}
                                if let Some(v)=provider {ui.set_ai_provider(v);}
                                if let Some(v)=base_url {ui.set_ai_api_base(v.into());}
                                if let Some(v)=model {ui.set_ai_model(v.into());}
                                if let Some(v)=key {ui.set_ai_api_key(v.into());}
                                ui.invoke_auto_grade();
                            }
                            qa::Command::AiCancel => ui.invoke_cancel_ai(),
                            qa::Command::AiApply => ui.invoke_apply_ai_suggestion(),
                            qa::Command::AiDismiss => ui.invoke_dismiss_ai_suggestion(),
                            qa::Command::Compare { enabled } => { if ui.get_has_preview() { ui.set_compare(enabled); } }
                            qa::Command::Export { destination, format } => {
                                if destination.is_absolute() && (0..=2).contains(&format) {
                                    *export_override.borrow_mut() = Some(destination);
                                    ui.set_export_format(format); ui.invoke_export_photo();
                                    export_override.borrow_mut().take();
                                }
                            }
                        }
                        let s = state.borrow();
                        let response = serde_json::json!({"id":id,"session":s.edits.session,
                            "selected":ui.get_selected_index(),"busy":ui.get_busy(),"preview":ui.get_has_preview(),
                            "engine_ready":ui.get_engine_ready(),"undo":ui.get_can_undo(),"redo":ui.get_can_redo(),
                            "compare":ui.get_compare(),"preview_fingerprint":ui.get_preview_fingerprint().as_str(),
                            "neutral_fingerprint":ui.get_neutral_fingerprint().as_str(),"exposure":ui.get_exposure(),
                            "temperature":ui.get_temperature(),"status":ui.get_status().as_str(),
                            "draft_status":ui.get_draft_status().as_str(),"export_busy":ui.get_export_busy(),
                            "export_status":ui.get_export_status().as_str(),"exit_warning":ui.get_exit_warning(),
                            "ai_busy":ui.get_ai_busy(),"ai_status":ui.get_ai_status().as_str(),
                            "ai_suggestion_ready":ui.get_ai_suggestion_ready()});
                        let response = if let Some(object) = response.as_object() {
                            let mut object = object.clone();
                            object.insert("batch".into(), serde_json::to_value(&s.batch).unwrap_or_default());
                            object.insert("batch_status".into(), ui.get_batch_status().as_str().into());
                            object.insert("batch_paused".into(), ui.get_batch_paused().into());
                            object.insert("batch_protected".into(), ui.get_batch_protected().into());
                            object.insert("full_cache_bytes".into(), serde_json::to_value(full_cache.try_lock().ok().map(|cache| cache.bytes())).unwrap_or_default());
                            object.insert("face_busy".into(),ui.get_face_busy().into());
                            object.insert("face_assets_ready".into(),ui.get_face_assets_ready().into());
                            object.insert("has_face".into(),ui.get_has_face().into());
                            object.insert("face_status".into(),ui.get_face_status().as_str().into());
                            object.insert("skin_smooth".into(),ui.get_skin_smooth().into());
                            object.insert("repair".into(),serde_json::json!({"mode":ui.get_repair_mode(),"choice":ui.get_repair_choice(),"busy":ui.get_repair_busy(),"ready":ui.get_repair_ready(),"count":ui.get_repair_count(),"selected":ui.get_repair_selected(),"picking":ui.get_repair_picking(),"status":ui.get_repair_status().as_str(),"radius":ui.get_repair_radius(),"opacity":ui.get_repair_opacity(),"feather":ui.get_repair_feather(),"has_source":ui.get_repair_has_source(),"source_uv":[ui.get_repair_source_u(),ui.get_repair_source_v()],"marks":ui.get_repair_marks().iter().map(|m|serde_json::json!([m.u,m.v,m.rx,m.ry])).collect::<Vec<_>>()}));
                            object.insert("view".into(),serde_json::json!({"zoom":ui.get_zoom(),"pan":[ui.get_pan_x(),ui.get_pan_y()],"canvas":[ui.get_canvas_width(),ui.get_canvas_height()],"image":[ui.get_image_left(),ui.get_image_top(),ui.get_image_width(),ui.get_image_height()],"pixels":[ui.get_displayed_width(),ui.get_displayed_height()]}));
                            object.insert("warp_active".into(),ui.get_has_warp().into());
                            object.insert("blemish".into(),serde_json::json!({"ready":ui.get_blemish_ready(),"busy":ui.get_blemish_busy(),"count":ui.get_blemish_count(),"selected":ui.get_blemish_selected(),"strength":ui.get_blemish_strength(),"status":ui.get_blemish_status().as_str(),"marks":ui.get_blemish_marks().iter().map(|m|serde_json::json!([m.u,m.v,m.rx,m.ry,m.chosen])).collect::<Vec<_>>()}));
                            object.insert("warp_tool".into(),serde_json::json!({"mode":ui.get_warp_mode(),"ready":ui.get_warp_ready(),"dragging":ui.get_warp_dragging(),"count":ui.get_warp_count(),"radius":ui.get_warp_radius(),"status":ui.get_warp_status().as_str()}));
                            object.insert("has_skin_mask".into(),ui.get_has_skin_mask().into());
                            object.insert("skin_uniformity".into(),ui.get_skin_uniformity().into());object.insert("skin_lighten".into(),ui.get_skin_lighten().into());
                            object.insert("skin_assets_ready".into(),ui.get_skin_assets_ready().into());
                            object.insert("show_skin_mask".into(),ui.get_show_skin_mask().into());
                            object.insert("portrait_ready".into(),ui.get_portrait_ready().into());
                            object.insert("face_selection".into(),ui.get_face_selection().as_str().into());
                            object.insert("preview_aspect".into(),ui.get_preview_aspect().into());
                            serde_json::Value::Object(object)
                        } else { response };
                        if let Err(e) = qa.respond(id, response) { ui.set_status(format!("QA response: {e}").into()); }
                    }
                    Err(e) => ui.set_status(format!("QA command: {e}").into()),
                    _ => {}
                }
            },
        );
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        ui.window().on_close_requested(move || {
            let Some(ui) = weak.upgrade() else {
                return slint::CloseRequestResponse::HideWindow;
            };
            if prepare_exit(&mut state.borrow_mut(), &ui, false) {
                slint::CloseRequestResponse::HideWindow
            } else {
                slint::CloseRequestResponse::KeepWindowShown
            }
        });
    }
    {
        let weak = ui.as_weak();
        let state = Rc::clone(&state);
        ui.on_confirm_exit(move || {
            if let Some(ui) = weak.upgrade() {
                if prepare_exit(&mut state.borrow_mut(), &ui, true) {
                    let _ = slint::quit_event_loop();
                }
            }
        });
    }
    ui.set_status(
        format!(
            "原生界面就绪 · 初始化 {:.0} ms",
            launched.elapsed().as_secs_f64() * 1000.0
        )
        .into(),
    );
    let restored_selection = state.borrow().edits.session.selected;
    if let Some(i) = restored_selection {
        ui.invoke_select_photo(i as i32);
    }
    ui.run()?;
    worker.cancel();
    Ok(())
}
