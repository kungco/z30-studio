#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use slint::{ComponentHandle, Model, ModelRc, Rgb8Pixel, SharedPixelBuffer, VecModel};
use std::{cell::RefCell, path::PathBuf, rc::Rc, sync::mpsc, time::{Duration,Instant}};
use z30_core::{preview, session::{Session,SessionStore}, worker::LatestWorker};
slint::include_modules!();
mod qa;

struct State { session:Session, store:SessionStore, protected:bool, dirty:bool }

fn save_state(state:&mut State,ui:&AppWindow) {
    if state.protected {return;}
    state.dirty=true;
    match state.store.save(&state.session) {
        Ok(())=>{state.dirty=false;ui.set_draft_status("选择与评分已保存到本机".into());},
        Err(error)=>ui.set_draft_status(format!("草稿保存失败，请勿直接退出：{error}").into()),
    }
}

fn photo_row(photo:&z30_core::session::Photo)->PhotoView {
    PhotoView { name:photo.name().into(),detail:photo.path.extension().unwrap_or_default().to_string_lossy().to_uppercase().into(),
        stars:"★".repeat(photo.rating.into()).into(),thumbnail:Default::default() }
}
fn slint_image(bytes:&[u8],w:u32,h:u32)->slint::Image {
    slint::Image::from_rgb8(SharedPixelBuffer::<Rgb8Pixel>::clone_from_slice(bytes,w,h))
}
fn data_root()->PathBuf {
    std::env::var_os("Z30_NATIVE_DATA_DIR").map(PathBuf::from).unwrap_or_else(|| {
        std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(std::env::temp_dir).join("Z30StudioNative")
    })
}

fn main()->Result<(),Box<dyn std::error::Error>> {
    let launched=Instant::now();
    let (mut qa,initial_photos)=qa::Harness::arguments()?;
    slint::BackendSelector::new().backend_name("winit".into()).renderer_name("software".into()).select()?;
    let store=SessionStore::new(data_root());
    let (mut session,protected,mut restore_status)=match store.load() {
        Ok(s)=>(s,false,"本机草稿已读取".to_owned()),
        Err(error)=>(Session::default(),true,format!("草稿读取失败；已保留原文件，会话只读：{error}")),
    };
    let mut initial_dirty=false;
    if !protected && !initial_photos.is_empty() {
        for path in initial_photos {if let Err(e)=session.add(&path){restore_status=format!("导入失败：{e}");}}
        if let Err(e)=store.save(&session){initial_dirty=true;restore_status=format!("导入后草稿保存失败：{e}");}
    }
    let rows=Rc::new(VecModel::from(session.photos.iter().map(photo_row).collect::<Vec<_>>()));
    let state=Rc::new(RefCell::new(State {session,store,protected,dirty:initial_dirty}));
    let ui=AppWindow::new()?;
    ui.set_photos(ModelRc::from(Rc::clone(&rows)));
    ui.set_can_import(!protected);
    ui.set_draft_status(restore_status.into());
    let worker=Rc::new(LatestWorker::new(|path:PathBuf| {
        std::panic::catch_unwind(|| preview::decode(&path)).unwrap_or_else(|_|Err(z30_core::Error::Policy("图像解码异常；原片未修改".into())))
    }));
    let (picked,files)=mpsc::channel::<Vec<PathBuf>>();
    {
        let weak=ui.as_weak();
        ui.on_import_files(move || {
            let Some(ui)=weak.upgrade() else{return};ui.set_can_import(false);ui.set_status("请选择本机照片…".into());
            let picked=picked.clone();
            std::thread::spawn(move || {
                let paths=rfd::FileDialog::new().set_title("导入照片（原片保持只读）").add_filter("照片", &["nef","jpg","jpeg","png"]).pick_files().unwrap_or_default();
                let _=picked.send(paths);
            });
        });
    }
    {
        let weak=ui.as_weak();let state=Rc::clone(&state);let worker=Rc::clone(&worker);
        ui.on_select_photo(move |index| {
            let Some(ui)=weak.upgrade() else{return};let Ok(index)=usize::try_from(index) else{return};let mut state=state.borrow_mut();
            let Some(photo)=state.session.photos.get(index) else{return};
            ui.set_photo_name(photo.name().into());worker.submit(photo.path.clone());state.session.selected=Some(index);
            ui.set_selected_index(index as i32);ui.set_busy(true);ui.set_has_preview(false);ui.set_zoom(1.0);ui.set_status("后台读取照片…".into());
            save_state(&mut state,&ui);
        });
    }
    {
        let weak=ui.as_weak();let state=Rc::clone(&state);let rows=Rc::clone(&rows);
        ui.on_rate_photo(move |rating| {
            let Some(ui)=weak.upgrade() else{return};let mut state=state.borrow_mut();if state.protected || !(0..=5).contains(&rating) {return;}
            if let Some(i)=state.session.selected {state.session.photos[i].rating=rating as u8;
                let mut row=rows.row_data(i).unwrap();row.stars="★".repeat(rating as usize).into();rows.set_row_data(i,row);
                save_state(&mut state,&ui);
            }
        });
    }
    {
        let weak=ui.as_weak();let worker=Rc::clone(&worker);
        ui.on_cancel_preview(move || {worker.cancel();if let Some(ui)=weak.upgrade(){ui.set_busy(false);ui.set_status("已取消显示结果；原片未修改".into());}});
    }
    let timer=slint::Timer::default();
    {
        let weak=ui.as_weak();let state=Rc::clone(&state);let rows=Rc::clone(&rows);let worker=Rc::clone(&worker);
        timer.start(slint::TimerMode::Repeated,Duration::from_millis(50),move || {
            let Some(ui)=weak.upgrade() else{return};
            match qa.poll(launched) {
                Ok(true)=>{if state.borrow().dirty{ui.set_exit_warning(true);}else{let _=slint::quit_event_loop();}return;},
                Err(e)=>ui.set_status(format!("QA 标记写入失败：{e}").into()),
                _=>{},
            }
            for paths in files.try_iter() {
                let mut state=state.borrow_mut();let mut added=0;let mut errors=vec![];
                for path in paths {match state.session.add(&path){Ok(true)=>{rows.push(photo_row(state.session.photos.last().unwrap()));added+=1},Ok(false)=>{},Err(e)=>errors.push(e.to_string())}}
                ui.set_can_import(!state.protected);
                save_state(&mut state,&ui);
                let selection=state.session.selected;drop(state);
                if let Some(i)=selection {ui.invoke_select_photo(i as i32);}
                if !errors.is_empty(){ui.set_status(format!("导入 {added} 张；{}",errors.join("；")).into());}
                else if added==0 {ui.set_status("没有新增照片".into());}
            }
            while let Some((revision,result))=worker.take_result() {
                if !worker.current(revision) {continue;}ui.set_busy(false);
                match result {
                    Ok(preview)=>{
                        if let Err(e)=qa.preview_ready(launched,preview.source_width,preview.source_height){ui.set_status(format!("QA 预览记录失败：{e}").into());}
                        ui.set_preview(slint_image(&preview.rgb,preview.width,preview.height));ui.set_has_preview(true);
                        ui.set_photo_info(format!("{} × {}  ·  预览 {} × {}",preview.source_width,preview.source_height,preview.width,preview.height).into());
                        ui.set_colour_info(preview.policy.into());ui.set_status("照片已读取 · 原片保持只读".into());
                        if let Some(i)=state.borrow().session.selected {if let Some(mut row)=rows.row_data(i){row.thumbnail=slint_image(&preview.thumbnail,preview.thumb_width,preview.thumb_height);rows.set_row_data(i,row);}}
                    },
                    Err(error)=>{ui.set_has_preview(false);ui.set_status(error.to_string().into());ui.set_colour_info("未将不支持的色彩/RAW 输入静默当作已正确显影".into());},
                }
            }
        });
    }
    {
        let weak=ui.as_weak();let state=Rc::clone(&state);
        ui.window().on_close_requested(move || {
            if state.borrow().dirty {
                if let Some(ui)=weak.upgrade(){ui.set_exit_warning(true);}
                slint::CloseRequestResponse::KeepWindowShown
            } else {slint::CloseRequestResponse::HideWindow}
        });
        ui.on_confirm_exit(||{let _=slint::quit_event_loop();});
    }
    ui.set_status(format!("原生界面就绪 · 初始化 {:.0} ms",launched.elapsed().as_secs_f64()*1000.0).into());
    let restored_selection=state.borrow().session.selected;
    if let Some(i)=restored_selection {ui.invoke_select_photo(i as i32);}
    ui.run()?;
    worker.cancel();
    Ok(())
}
