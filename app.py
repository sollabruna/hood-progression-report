"""Streamlit GUI for the Hood Glaucoma Progression Report.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import io
import sys
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hood_progression import ProgressionReport, ReportInputs, ReportOptions, __version__  # noqa: E402
from hood_progression import batch, changemap, hexml  # noqa: E402

st.set_page_config(page_title="Glaucoma Progression Report", layout="wide")
st.title("Glaucoma Progression Report generator")
st.sidebar.caption(f"Version {__version__} · updates are applied automatically at launch · "
                   f"[changelog](https://github.com/sollabruna/hood-progression-report/releases)")


# --------------------------------------------------------------------------- helpers
def _save_upload(up, folder: Path) -> Path | None:
    if up is None:
        return None
    p = folder / up.name
    p.write_bytes(up.getbuffer())
    return p


def _by_date(paths: list[Path], kind: str) -> list[Path]:
    """Keep XMLs of the given kind ('circle' / 'volume'), ordered by exam date."""
    rows = []
    for p in paths:
        k = hexml.guess_export_kind(p)
        if k != kind:
            st.warning(f"{p.name} is not a {'circle (ONH-RC)' if kind == 'circle' else 'posterior-pole volume'} export; ignored.")
            continue
        rows.append((hexml.read_patient_info(p).exam_date, p))
    rows.sort(key=lambda t: (t[0] is None, t[0]))
    return [p for _, p in rows]


def _options_panel(key: str) -> ReportOptions:
    map_mode = st.radio("Maps from the Hood report", ["probability", "thickness"], key=f"{key}_mode",
                        format_func=lambda m: "Deviation / probability maps (Field view)" if m == "probability" else "Thickness maps (Retina view)")
    with st.expander("Advanced"):
        scale = st.select_slider("Render scale (1 = 2250 x 1653 px)", options=[1.0, 1.5, 2.0, 3.0], value=2.0, key=f"{key}_scale")
        circle = st.selectbox("Circle for cpRNFL", ["small", "medium", "large"], index=0, key=f"{key}_circle",
                              format_func=lambda c: {"small": "3.5 mm (small)", "medium": "4.1 mm", "large": "4.7 mm"}[c])
        ftd_auto = st.checkbox("FoDi angle from posterior-pole XML (−TiltAngle for OD, +TiltAngle for OS)", value=True, key=f"{key}_ftd")
        ftd_manual = st.number_input("Manual FoDi angle (°)", value=-7.7, step=0.1, disabled=ftd_auto, key=f"{key}_ftdv")
        st.markdown("**Thickness change maps**")
        smooth = st.slider("Smoothing (mm)", 0.0, 0.5, 0.15, 0.05, key=f"{key}_smooth")
        fill = st.checkbox("Fill missing data (unsegmented A-scans / missing B-scans)", value=True, key=f"{key}_fill")
        mdisc = st.checkbox("Leave the optic disc grey (unfilled)", value=False, key=f"{key}_disc")
        lim_rnfl = st.number_input("RNFL limit (± µm)", value=float(changemap.DEFAULT_LIMITS["rnfl"]), step=5.0, key=f"{key}_lr")
        lim_gcl = st.number_input("GCL limit (± µm)", value=float(changemap.DEFAULT_LIMITS["gcl"]), step=5.0, key=f"{key}_lg")
        palette = st.selectbox("Colour scale", ["heidelberg", "diverging"], index=0, key=f"{key}_pal",
                               format_func=lambda k: {"heidelberg": "HEYEX style (green = thinning, black, red = thickening)",
                                                      "diverging": "Red = thinning, white = none, blue = thickening"}[k])
        rotate = st.checkbox("Rotate to the fovea-to-disc angle", value=True, key=f"{key}_rot")
        clip = st.checkbox("Clip to the same frame as the HEYEX maps (uncheck to show the whole scan)", value=True, key=f"{key}_clip")
        etdrs = st.checkbox("Draw ETDRS grid", value=False, key=f"{key}_etdrs")
        view = st.selectbox("Orientation", ["auto", "field", "retina"], index=0, key=f"{key}_view",
                            format_func=lambda v: {"auto": "Auto (field view with probability maps, retina view with thickness maps)",
                                                   "field": "Field view (inferior retina at top)", "retina": "Retina view"}[v])
        st.markdown("**Other**")
        bscan_top, bscan_bot = st.slider("B-scan vertical crop (fraction kept)", 0.0, 1.0, (0.04, 0.60), 0.01, key=f"{key}_bs")
    return ReportOptions(
        map_mode=map_mode, scale=float(scale), circle=circle,
        ftd_angle=None if ftd_auto else float(ftd_manual),
        change_limits={"rnfl": float(lim_rnfl), "gcl": float(lim_gcl)},
        change_smooth_mm=float(smooth), fill_missing=fill, mask_disc=mdisc,
        change_palette=palette, rotate_change_maps=rotate, clip_to_frame=clip, draw_etdrs=etdrs,
        view=None if view == "auto" else view, bscan_crop_frac=(bscan_top, bscan_bot),
    )


tab_single, tab_batch = st.tabs(["Single report", "Batch"])

# =========================================================================== single
with tab_single:
    with st.sidebar:
        st.header("Single report inputs")
        st.subheader("1. Hood reports (PDF)")
        pdf_base = st.file_uploader("Baseline Hood report", type=["pdf"], key="pdf1")
        pdf_fup = st.file_uploader("Follow-up Hood report", type=["pdf"], key="pdf2")
        st.subheader("2. Circle (ONH-RC) XMLs")
        circ_up = st.file_uploader("Baseline + follow-up circle exports", type=["xml"], accept_multiple_files=True, key="circ",
                                   help="Both files; they are ordered by exam date automatically.")
        st.subheader("3. Posterior-pole XMLs")
        vol_up = st.file_uploader("Baseline + follow-up posterior-pole exports", type=["xml"], accept_multiple_files=True, key="vol",
                                  help="Both files; they are ordered by exam date automatically.")
        st.subheader("4. Options")
        opts = _options_panel("s")

    if not (pdf_base and pdf_fup):
        st.info("Upload both Hood report PDFs (left sidebar) to begin. The XMLs add the cpRNFL plot and the change maps.")
    else:
        tmp = Path(tempfile.mkdtemp(prefix="hood_report_"))
        p1, p2 = _save_upload(pdf_base, tmp), _save_upload(pdf_fup, tmp)
        circles = _by_date([_save_upload(u, tmp) for u in (circ_up or [])], "circle")
        volumes = _by_date([_save_upload(u, tmp) for u in (vol_up or [])], "volume")

        col_a, col_b = st.columns([1, 1])
        with col_a:
            st.subheader("Detected inputs")
            for label, lst in (("Circle XMLs", circles), ("Posterior-pole XMLs", volumes)):
                desc = ", ".join(f"{hexml.read_patient_info(p).patient_id} {hexml.read_patient_info(p).eye} {hexml.read_patient_info(p).exam_date}" for p in lst)
                st.write(f"**{label}:** {len(lst)} — {desc}")
            if len(circles) < 2:
                st.warning("Need two circle XMLs for the cpRNFL difference plot (it will be left blank).")
            if len(volumes) < 2:
                st.warning("Need two posterior-pole XMLs for the RNFL/GCL change maps (they will be left blank).")

        inputs = ReportInputs(
            hood_baseline=p1, hood_followup=p2,
            circle_baseline=circles[0] if len(circles) > 1 else None, circle_followup=circles[1] if len(circles) > 1 else None,
            volume_baseline=volumes[0] if len(volumes) > 1 else None, volume_followup=volumes[1] if len(volumes) > 1 else None,
        )
        try:
            report = ProgressionReport(inputs, opts)
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not read the inputs: {e}")
            st.stop()

        with col_b:
            st.subheader("Header (editable)")
            h = report.header
            c1, c2, c3 = st.columns(3)
            h.patient = c1.text_input("Patient", h.patient)
            h.patient_id = c1.text_input("Patient ID", h.patient_id)
            h.diagnosis = c1.text_input("Diagnosis", h.diagnosis)
            h.dob = c2.text_input("DOB", h.dob)
            h.exam1 = c2.text_input("Exam 1", h.exam1)
            h.exam2 = c2.text_input("Exam 2", h.exam2)
            h.sex = c3.text_input("Sex", h.sex)
            h.eye = c3.text_input("Eye", h.eye)
            h.title = c3.text_input("Title", h.title)
        for w in report.warnings:
            st.warning(w)

        if st.button("Generate report", type="primary"):
            with st.spinner("Rendering..."):
                page = report.render()
            st.session_state["page"] = page
            st.session_state["name"] = report.suggested_filename("png")
            st.session_state["name_pdf"] = report.suggested_filename("pdf")
            st.session_state["scale"] = opts.scale

        if "page" in st.session_state:
            page = st.session_state["page"]
            st.image(page, width="stretch")
            png = io.BytesIO()
            page.save(png, "PNG", dpi=(int(2250 * st.session_state["scale"] / 11.0),) * 2)
            pdf = io.BytesIO()
            page.save(pdf, "PDF", resolution=2250 * st.session_state["scale"] / 11.0)
            d1, d2 = st.columns(2)
            d1.download_button("Download PNG", png.getvalue(), file_name=st.session_state["name"], mime="image/png")
            d2.download_button("Download PDF", pdf.getvalue(), file_name=st.session_state["name_pdf"], mime="application/pdf")

# =========================================================================== batch
with tab_batch:
    st.markdown(
        "Generate many reports at once. Point at the folders that hold the Hood PDFs, the circle XMLs and the "
        "posterior-pole XMLs (any number of folders; subfolders are searched). Files are grouped by **patient ID, "
        "eye and exam date**, so each visit needs its PDF and XMLs to carry the same ID and date."
    )
    c1, c2 = st.columns([1, 1])
    with c1:
        folders_txt = st.text_area("Input folders (one per line)", height=110,
                                   placeholder="/Users/me/Hood reports\n/Users/me/HEYEX exports/circle\n/Users/me/HEYEX exports/posterior pole")
        uploads = st.file_uploader("…or drop files here (PDFs and XMLs)", type=["pdf", "xml"], accept_multiple_files=True, key="batch_up")
        out_dir = st.text_input("Output folder", value=str(Path.home() / "Desktop" / "progression_reports"))
    with c2:
        pair_mode = st.selectbox("Which pairs", batch.PAIR_MODES, index=0, format_func=lambda m: {
            "latest": "Baseline → latest visit (one report per eye)",
            "all": "Baseline → every later visit",
            "consecutive": "Each visit → the next visit"}[m])
        formats = st.multiselect("Output formats", ["png", "pdf"], default=["png", "pdf"])
        require_xml = st.checkbox("Skip visits that lack a circle or posterior-pole XML", value=False)
        bopts = _options_panel("b")

    sources = [l.strip() for l in folders_txt.splitlines() if l.strip()]
    tmp_b = Path(tempfile.mkdtemp(prefix="hood_batch_"))
    files = batch.collect_files(sources) + [p for p in (_save_upload(u, tmp_b) for u in (uploads or [])) if p]
    bad = [s for s in sources if not Path(s).expanduser().exists()]
    for s_ in bad:
        st.error(f"Folder not found: {s_}")

    if files:
        log: list[str] = []
        with st.spinner(f"Indexing {len(files)} files..."):
            visits = batch.index_visits(files, log.append)
        items = batch.plan_pairs(visits, pair_mode, require_xml)
        st.subheader(f"Found {len(visits)} visits → {sum(it.status == 'pending' for it in items)} reports to generate")
        st.dataframe(
            [{"patient": v.patient_id, "eye": v.eye, "date": v.date.isoformat(),
              "Hood PDF": v.hood_pdf.name if v.hood_pdf else "—",
              "circle XML": v.circle_xml.name if v.circle_xml else "—",
              "posterior-pole XML": v.volume_xml.name if v.volume_xml else "—"}
             for v in sorted(visits.values(), key=lambda v: v.key)],
            width="stretch", hide_index=True,
        )
        if log:
            with st.expander(f"{len(log)} files skipped or noted"):
                st.text("\n".join(log))

        if st.button("Generate all reports", type="primary", disabled=not any(it.status == "pending" for it in items)):
            bar = st.progress(0.0, text="Starting...")

            def _prog(i, n, it):
                bar.progress(i / n, text=f"{i}/{n}: {it.patient_id} {it.eye} {it.baseline.date} → {it.followup.date}")

            items = batch.run_batch(items, out_dir, bopts, tuple(formats), _prog)
            bar.progress(1.0, text="Done")
            st.session_state["batch_items"] = items
            st.session_state["batch_out"] = out_dir

        if "batch_items" in st.session_state:
            items = st.session_state["batch_items"]
            n_ok = sum(it.status == "ok" for it in items)
            st.success(f"{n_ok} report(s) written to {st.session_state['batch_out']}")
            st.dataframe(batch.summary_rows(items), width="stretch", hide_index=True)
            outs = [p for it in items for p in it.outputs if p.exists()]
            if outs:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for p in outs:
                        z.write(p, p.name)
                st.download_button("Download all as ZIP", buf.getvalue(), file_name="progression_reports.zip", mime="application/zip")
    else:
        st.info("Enter at least one folder or drop files to see what would be generated.")
