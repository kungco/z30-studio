#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use slint::{ComponentHandle, Model, ModelRc, Rgb8Pixel, SharedPixelBuffer, VecModel};
use std::{
    cell::RefCell,
    path::PathBuf,
    rc::Rc,
    sync::{Arc, Mutex, mpsc},
    time::{Duration, Instant},
};
use z30_core::{
    editing::{EditRequest, EditingSession},
    editor::PreviewEngine,
    export::Format,
    export_job::{ExportControl, ExportReceipt, ExportSource},
    session::{Session, SessionStore},
    worker::LatestWorker,
};
slint::include_modules!();
mod qa;

struct State {
    edits: EditingSession,
    last_edit: Option<Instant>,
    pending_preview: Option<EditRequest>,
    export_control: Option<Arc<ExportControl>>,
    export_dialog: bool,
    close_after_export: bool,
    store: SessionStore,
    protected: bool,
    dirty: bool,
}

struct DisplayPreview {
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
fn prepare_exit(state: &mut State, ui: &AppWindow, discard: bool) -> bool {
    flush_edits(state, ui);
    if state.dirty && !discard {
        ui.set_exit_warning(true);
        return false;
    }
    if discard {
        state.dirty = false;
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
    true
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
        export_control: None,
        export_dialog: false,
        close_after_export: false,
        store,
        protected,
        dirty: initial_dirty,
    }));
    let ui = AppWindow::new()?;
    ui.set_photos(ModelRc::from(Rc::clone(&rows)));
    ui.set_can_import(!protected);
    ui.set_draft_status(restore_status.into());
    let raw_dll = z30_core::runtime::raw_library(
        &std::env::current_exe()?, std::env::var_os("Z30_LIBRAW_DLL").as_deref(),
    )?;
    let mut engine = PreviewEngine::new(raw_dll.clone())?;
    // Serialize full-image computation so export + a new selection cannot hold
    // two full RAW decodes at once. The event loop remains independent.
    let compute = Arc::new(Mutex::new(()));
    let preview_compute = Arc::clone(&compute);
    let worker = Rc::new(LatestWorker::new(move |request: EditRequest| {
        let _guard = preview_compute.lock().unwrap_or_else(|e| e.into_inner());
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            engine
                .preview(&request.path, request.recipe)
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
    // Only the explicitly enabled local QA harness can inject a save-dialog
    // choice. The same production callback captures and starts the job.
    let export_override = Rc::new(RefCell::new(None::<PathBuf>));
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
            state.borrow_mut().pending_preview = None;
            if let Some(ui) = weak.upgrade() {
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
            if state.protected || !ui.get_engine_ready() {
                return;
            }
            match state.edits.adjust(name.as_str(), value) {
                Ok(true) => {
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
        let export_override = Rc::clone(&export_override);
        ui.on_export_photo(move || {
            let Some(ui) = weak.upgrade() else { return };
            let mut state = state.borrow_mut();
            if state.protected
                || ui.get_busy()
                || !ui.get_has_preview()
                || state.export_dialog
                || state.export_control.is_some()
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
                        std::thread::spawn(move || {
                            let _guard = compute.lock().unwrap_or_else(|e| e.into_inner());
                            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(||
                                source.run(&destination.unwrap(), format, dll.as_deref(), &control)
                            )).unwrap_or_else(|_| Err(z30_core::Error::Policy("导出异常，原片保持不变".into())));
                            let _ = exports.send(result);
                        });
                    }
                }
                for result in completed_exports.try_iter() {
                    state.borrow_mut().export_control = None;
                    ui.set_export_busy(false); ui.set_export_cancellable(false);
                    ui.set_export_status(match result {
                        Ok(v) => format!("已导出 {} × {} · {:.1} 秒 · {}", v.width, v.height, v.seconds, v.destination.display()),
                        Err(e) => format!("导出未完成：{e}"),
                    }.into());
                }
                {
                    let state = state.borrow();
                    if let Some(control) = &state.export_control {
                        let message = if control.cancelled.load(std::sync::atomic::Ordering::Acquire) {
                            "正在取消；等待当前处理阶段结束…".to_owned()
                        } else { format!("{} · 全尺寸 sRGB · 可继续编辑照片", control.stage()) };
                        ui.set_export_status(message.into());
                    }
                    if state.close_after_export && !state.export_dialog && state.export_control.is_none() && !state.dirty {
                        let _ = slint::quit_event_loop(); return;
                    }
                }
                while let Some((revision, (request, result))) = worker.take_result() {
                    if !worker.current(revision) || state.borrow().edits.current().as_ref() != Some(&request) {
                        continue;
                    }
                    ui.set_busy(false);
                    match result {
                        Ok(preview) => {
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
                            ui.set_has_preview(false);
                            ui.set_engine_ready(false);
                            ui.set_status(error.to_string().into());
                            ui.set_colour_info(
                                "未将不支持的色彩/RAW 输入静默当作已正确显影".into(),
                            );
                        }
                    }
                }
                match qa.command() {
                    Ok(Some((id, command))) => {
                        match command {
                            qa::Command::Snapshot => {}
                            qa::Command::Adjust { parameter, value } => ui.invoke_adjust(parameter.into(), value),
                            qa::Command::Select { index } => ui.invoke_select_photo(index),
                            qa::Command::Undo => ui.invoke_undo_edit(),
                            qa::Command::Redo => ui.invoke_redo_edit(),
                            qa::Command::Reset => ui.invoke_reset_edits(),
                            qa::Command::CancelPreview => ui.invoke_cancel_preview(),
                            qa::Command::RetryPreview => ui.invoke_retry_preview(),
                            qa::Command::CancelExport => ui.invoke_cancel_export(),
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
                            "export_status":ui.get_export_status().as_str(),"exit_warning":ui.get_exit_warning()});
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
