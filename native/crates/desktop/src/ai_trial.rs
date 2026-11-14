//! Transient local proofing: never mutates EditingSession or calls a provider.
use super::*;

struct Job {
    request: EditRequest,
    source: Arc<ExportSource>,
    revision: u64,
    advice: Advice,
    strength: f32,
    reference: ai::Metrics,
}
pub struct Workflow {
    state: Rc<RefCell<State>>,
    jobs: LatestWorker<Job, (Job, z30_core::Result<AiProduct>)>,
}

pub fn goal(ui: &AppWindow) -> z30_core::Result<String> {
    let text = ui.get_ai_goal();
    if text.chars().count() > 350 {
        return Err(z30_core::Error::Policy(
            "补充目标最多350字；没有发送照片".into(),
        ));
    }
    let style = match ui.get_ai_style() {
        1 => "柔和人像，保持自然肤色和面部层次，不增加生硬对比或过度饱和。",
        2 => "温暖但克制的胶片氛围，柔和高光，保留暗部层次，不声称复刻特定胶片。",
        3 => "产品与静物，保持物体颜色可信、背景干净，避免强制把暖光白色变成纯白。",
        4 => "保留夜景与低调氛围，控制灯光溢出，不把夜景整体变成白天，不承诺降噪。",
        _ => "自然通透，保持现场氛围与真实色彩。",
    };
    Ok(format!(
        "{} 风格偏好：{} 用户补充风格数据：{}",
        ai::AUTO_GOAL,
        style,
        text
    ))
}

pub fn bind(
    ui: &AppWindow,
    state: Rc<RefCell<State>>,
    engine: Arc<Mutex<PreviewEngine>>,
    compute: Arc<Mutex<()>>,
) -> Rc<Workflow> {
    let jobs = LatestWorker::new_cancellable(move |job: Job, cancel| {
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            job.source.unchanged()?;
            let product = render_ai_advice(
                &job.request,
                &job.source,
                job.advice.clone(),
                Options {
                    strength: job.strength,
                    ..Default::default()
                },
                job.reference,
                &compute,
                &engine,
                || cancel.cancelled(),
            )?;
            job.source.unchanged()?;
            Ok(product)
        }))
        .unwrap_or_else(|_| Err(z30_core::Error::Policy("试片异常，原编辑保留".into())));
        (job, result)
    });
    let w = Rc::new(Workflow { state, jobs });
    {
        let w = Rc::clone(&w);
        let weak = ui.as_weak();
        ui.on_trial_ai(move |strength| {
            let Some(ui) = weak.upgrade() else { return };
            if !strength.is_finite()
                || !(0.0..=100.).contains(&strength)
                || ui.get_busy()
                || ui.get_ai_busy()
            {
                return;
            }
            let s = w.state.borrow();
            let Some(p) = &s.ai_suggestion else { return };
            if s.edit_revision != p.revision || s.edits.current().as_ref() != Some(&p.request) {
                return;
            }
            if let Err(e) = p.source.unchanged() {
                drop(s);
                w.discard(&ui, &e.to_string());
                return;
            }
            let Some(base) = ui.get_preview().to_rgb8() else {
                return;
            };
            let Ok(reference) = ai::metrics(base.as_bytes(), base.width(), base.height()) else {
                return;
            };
            w.jobs.submit(Job {
                request: p.request.clone(),
                source: Arc::clone(&p.source),
                revision: p.revision,
                advice: p.product.advice.clone(),
                strength,
                reference,
            });
            ui.set_ai_trial_strength(strength);
            ui.set_ai_trial_ready(false);
            ui.set_ai_trial_busy(true);
            ui.set_ai_status("正在本机更新试片；没有再次调用模型，原编辑未改变。".into());
        });
    }
    {
        let w = Rc::clone(&w);
        let weak = ui.as_weak();
        ui.on_review_ai(move || {
            let Some(ui)=weak.upgrade() else{return};
            if ui.get_busy() || ui.get_ai_busy() || ui.get_style_busy() || ui.get_style_running() || ui.get_ai_suggestion_ready() || !ui.get_has_preview() {return;}
            let result=(|| -> z30_core::Result<()> {
                let goal=goal(&ui)?;
                let s=w.state.borrow();
                if s.protected {return Err(z30_core::Error::Policy("当前草稿只读".into()));}
                let source=ExportSource::new(s.edits.current().ok_or_else(||z30_core::Error::Policy("请先选择照片".into()))?)?;
                source.unchanged()?;
                let destination=if ui.get_ai_provider()==0 {"Codex CLI（沿用本机登录）".to_owned()} else {
                    let config=ai_http::Config::from_selection(ui.get_ai_provider(),&ui.get_ai_api_base(),&ui.get_ai_model(),&ui.get_ai_api_key())?;
                    format!("兼容接口：{}\n模型：{}",config.endpoint,config.model)
                };
                for (image,neutral) in [(ui.get_original_preview(),true),(ui.get_preview(),false)] {
                    let rgb=image.to_rgb8().ok_or_else(||z30_core::Error::Policy("预览不可用".into()))?;
                    let bytes=ai_cli::encode_jpeg(&rgb)?;
                    let decoded=image::load_from_memory(&bytes).map_err(|e|z30_core::Error::Policy(e.to_string()))?.to_rgb8();
                    let picture=slint_image(decoded.as_raw(),decoded.width(),decoded.height());
                    if neutral {ui.set_ai_send_neutral(picture);}else{ui.set_ai_send_current(picture);}
                }
                let parameters=serde_json::to_string_pretty(&source.edit.recipe.parameters)?;
                let mode=if ui.get_ai_mode()==0 {"自动应用（可撤销）"}else{"先试片，确认后应用"};
                ui.set_ai_send_text(format!("发送到：{destination}\n处理方式：{mode}\n\n两张无EXIF/GPS的sRGB JPEG预览（最长边1024）、当前九参数、白平衡模式和以下目标；不发送原片或路径。\n\n{goal}\n\n当前参数：\n{parameters}\n白平衡：{:?}\n\n兼容服务可能收费；本次只提交一次分析，不自动重试。",source.edit.recipe.white_balance).into());
                ui.set_ai_send_visible(true);
                Ok(())
            })();
            if let Err(e)=result {ui.set_ai_status(e.to_string().into());}
        });
    }
    w
}
pub fn show(ui: &AppWindow, product: &AiProduct, base: &Parameters) {
    ui.set_ai_trial_preview(slint_image(
        &product.preview.rgb,
        product.preview.width,
        product.preview.height,
    ));
    ui.set_ai_trial_fingerprint(fingerprint(&product.preview.rgb).into());
    ui.set_ai_trial_ready(true);
    let p = product.parameters;
    ui.set_ai_trial_summary(format!("模型建议：{}\n{}\n\n曝光 {:.2}→{:.2} EV · 对比 {:.0}→{:.0}\n高光 {:.0}→{:.0} · 阴影 {:.0}→{:.0}\n色温 {:.0}→{:.0} · 色调 {:.0}→{:.0}\n饱和 {:.0}→{:.0} · 自然饱和 {:.0}→{:.0}\n锐化 {:.0}→{:.0}\n本机检测：{}（实际强度{}%）",product.advice.title,product.advice.summary,base.exposure,p.exposure,base.contrast,p.contrast,base.highlights,p.highlights,base.shadows,p.shadows,base.temperature,p.temperature,base.tint,p.tint,base.saturation,p.saturation,base.vibrance,p.vibrance,base.sharpen,p.sharpen,product.quality_note,product.used_strength).into());
}
impl Workflow {
    fn discard(&self, ui: &AppWindow, message: &str) {
        self.jobs.cancel();
        self.state.borrow_mut().ai_suggestion = None;
        ui.set_ai_suggestion_ready(false);
        ui.set_ai_trial_ready(false);
        ui.set_ai_trial_busy(false);
        ui.set_ai_status(format!("试片已丢弃：{message}；原编辑保留。").into());
    }
    pub fn poll(&self, ui: &AppWindow) {
        if !ui.get_ai_suggestion_ready() || ui.get_ai_busy() {
            self.jobs.cancel();
            ui.set_ai_trial_busy(false);
            return;
        }
        let stale = self
            .state
            .borrow()
            .ai_suggestion
            .as_ref()
            .is_some_and(|p| p.source.unchanged().is_err());
        if stale {
            self.discard(ui, "原片已变化");
            return;
        }
        while let Some((revision, (job, result))) = self.jobs.take_result() {
            if !self.jobs.current(revision) {
                continue;
            }
            let mut s = self.state.borrow_mut();
            let valid = s.edit_revision == job.revision
                && s.edits.current().as_ref() == Some(&job.request)
                && s.ai_suggestion
                    .as_ref()
                    .is_some_and(|p| Arc::ptr_eq(&p.source, &job.source));
            if !valid {
                continue;
            }
            ui.set_ai_trial_busy(false);
            match result {
                Ok(product) => {
                    show(ui, &product, &job.request.recipe.parameters);
                    ui.set_ai_report(ai_report(&product).into());
                    ui.set_ai_status("试片未保存；确认应用后再导出。强度调整仅在本机完成。".into());
                    s.ai_suggestion.as_mut().unwrap().product = product;
                }
                Err(e) => {
                    drop(s);
                    self.discard(ui, &e.to_string());
                }
            }
        }
    }
}
