"""采集模块接口契约测试 (DM-05..11)。importorskip 保证未建时 skip。

对应 docs/modules/05-11-*.md 的"产出接口契约"。
"""
import pytest


def test_dm05_browser_contract():
    m = pytest.importorskip("semilabs_hone.modules.collection.browser.cdp")
    for name in ["launch_real_chrome", "attach", "find_free_port"]:
        assert callable(getattr(m, name, None)), f"cdp 缺 {name}"


def test_dm06_anti_detect_contract():
    st = pytest.importorskip("semilabs_hone.modules.collection.anti_detect.stealth")
    # PRD zero-injection redline: inject_noise must exist as a callable no-op
    # (NOISE_ONLY_SCRIPT kept as empty sentinel for backward-compat imports)
    assert callable(getattr(st, "inject_noise", None))
    assert getattr(st, "NOISE_ONLY_SCRIPT", None) == ""
    hb = pytest.importorskip("semilabs_hone.modules.collection.anti_detect.human_behavior")
    for name in ["human_type", "human_click", "random_scroll", "random_browse", "generate_slide_track", "smart_wait"]:
        assert callable(getattr(hb, name, None)), f"human_behavior 缺 {name}"
    fp = pytest.importorskip("semilabs_hone.modules.collection.anti_detect.fingerprint")
    assert hasattr(fp, "Fingerprint")
    for name in ["assign_fingerprint", "load_fingerprint", "apply_fingerprint"]:
        assert callable(getattr(fp, name, None)), f"fingerprint 缺 {name}"
    ua = pytest.importorskip("semilabs_hone.modules.collection.anti_detect.ua_pool")
    assert callable(getattr(ua, "get_ua", None))


def test_dm07_scrapers_contract():
    base = pytest.importorskip("semilabs_hone.modules.collection.scrapers.base")
    assert hasattr(base, "BasePlatformScraper")
    spec = pytest.importorskip("semilabs_hone.modules.collection.scrapers.spec")
    for name in ["PlatformSpec", "Flow", "Step"]:
        assert hasattr(spec, name), f"spec 缺 {name}"
    fe = pytest.importorskip("semilabs_hone.modules.collection.scrapers.field_extract")
    for name in ["extract_api", "extract_dom", "render_template"]:
        assert callable(getattr(fe, name, None)), f"field_extract 缺 {name}"
    eng = pytest.importorskip("semilabs_hone.modules.collection.scrapers.engine")
    assert hasattr(eng, "GenericEngine")
    reg = pytest.importorskip("semilabs_hone.modules.collection.scrapers.registry")
    for name in ["load_registry", "list_platforms", "get"]:
        assert callable(getattr(reg, name, None)), f"registry 缺 {name}"


def test_dm08_recorder_contract():
    rec = pytest.importorskip("semilabs_hone.modules.collection.scrapers.recorder")
    assert hasattr(rec, "RecordingSession") or callable(getattr(rec, "record_platform", None))
    mp = pytest.importorskip("semilabs_hone.modules.collection.scrapers.llm_mapper")
    for name in ["map_group", "validate_map", "build_platform_yaml"]:
        assert callable(getattr(mp, name, None)), f"llm_mapper 缺 {name}"


def test_dm09_captcha_scheduler_contract():
    slv = pytest.importorskip("semilabs_hone.modules.collection.captcha.solver")
    assert callable(getattr(slv, "detect_and_solve", None))
    rh = pytest.importorskip("semilabs_hone.modules.collection.scheduler.rhythm")
    for name in ["check_quiet_hours", "check_daily_limit", "note_delay", "keyword_delay",
                 "should_pause_for_captcha", "is_quiet_hours", "seconds_until_wakeup",
                 "sleep_until_wakeup"]:
        assert callable(getattr(rh, name, None)), f"rhythm 缺 {name}"
    wu = pytest.importorskip("semilabs_hone.modules.collection.scheduler.warmup")
    assert callable(getattr(wu, "random_browse", None))


def test_dm10_export_image_contract():
    exp = pytest.importorskip("semilabs_hone.modules.collection.export.csv_exporter")
    assert callable(getattr(exp, "export_csv", None))
    img = pytest.importorskip("semilabs_hone.core.utils.image_downloader")
    for name in ["download_images", "check_disk"]:
        assert callable(getattr(img, name, None)), f"image_downloader 缺 {name}"


def test_dm11_integration_contract():
    h = pytest.importorskip("semilabs_hone.modules.collection.handlers")
    assert callable(getattr(h, "build_registry", None))
