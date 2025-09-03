import io
import math
import zipfile
from io import BytesIO

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
import qrcode
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader  # needed for ReportLab image input

try:
    from streamlit_drawable_canvas import st_canvas
    HAS_CANVAS = True
except Exception:
    HAS_CANVAS = False

st.set_page_config(page_title="Hexmodal QR Sticker Generator", page_icon="🔳", layout="centered")

st.title("🔳 Hexmodal QR Sticker Generator")
st.caption("Upload a CSV of Serial/URL and an optional black Hexmodal logo — get per-serial PDF + high-res PNG stickers with a hexagonal logo cutout.")

# Sidebar parameters
st.sidebar.header("Sticker Settings")
sticker_size_cm = st.sidebar.number_input("Sticker size (cm)", min_value=2.0, max_value=20.0, value=8.0, step=0.5)
logo_scale = st.sidebar.slider("Logo size (% of QR width)", min_value=10, max_value=40, value=25, step=1)
cutout_padding = st.sidebar.slider("Logo cutout padding (×)", min_value=100, max_value=160, value=120, step=5) / 100.0
serial_width_pct = st.sidebar.slider("Serial width (% of QR width)", min_value=20, max_value=80, value=50, step=5)
dpi = st.sidebar.selectbox("PNG Export DPI", options=[300, 450, 600, 900], index=2)

st.sidebar.header("QR Settings")
ec_level = st.sidebar.selectbox("Error Correction", options=["L","M","Q","H"], index=3)
box_size = st.sidebar.slider("QR Box Size (pixels per module)", min_value=10, max_value=40, value=20)

st.sidebar.caption("Tip: Higher DPI & larger box size → crisper PNGs (bigger files).")

# Preview & positioning controls
st.sidebar.header("Preview & Positioning")
enable_preview = st.sidebar.checkbox("Enable live preview", value=True)
qr_x_offset_pct = st.sidebar.slider("QR X offset (% of canvas width)", -25, 25, 0, step=1)
qr_y_offset_pct = st.sidebar.slider("QR Y offset (% of canvas height)", -25, 25, 0, step=1)
serial_y_offset_pct = st.sidebar.slider("Serial Y offset (% of canvas height)", -15, 15, 0, step=1)

drag_mode = st.sidebar.checkbox("Drag/resize on canvas (experimental)", value=False, help="Draw/drag rectangles to position the QR and a text box for the serial.")
serial_font_name = st.sidebar.selectbox("Serial font", options=["Helvetica-Bold", "Arial", "DejaVuSans-Bold", "DejaVuSans"], index=0)
serial_font_px = st.sidebar.slider("Serial font size (px, for PNG)", min_value=10, max_value=300, value=0, step=2, help="0 = auto-fit to target width")

st.subheader("1) Upload Inputs")
csv_file = st.file_uploader("CSV with columns: Serial, URL", type=["csv"])
logo_file = st.file_uploader("Black Hexmodal logo (PNG) — optional", type=["png"])
bg_file = st.file_uploader("Background template (PNG/JPG) — optional", type=["png","jpg","jpeg"])

# Helpers
def hex_points(size):
    r = size / 2
    cx, cy = r, r
    pts = [
        (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
        for a in range(0, 360, 60)
    ]
    return pts

def make_qr(data, error_correction, box_size=20, border=2):
    ec_map = {"L": qrcode.constants.ERROR_CORRECT_L,
              "M": qrcode.constants.ERROR_CORRECT_M,
              "Q": qrcode.constants.ERROR_CORRECT_Q,
              "H": qrcode.constants.ERROR_CORRECT_H}
    qr = qrcode.QRCode(
        version=None,
        error_correction=ec_map[error_correction],
        box_size=box_size,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGBA")

def paste_logo_hex(qr_img, logo_img, logo_frac=0.25, padding=1.2):
    """Clear a hexagonal area and paste the logo inside it."""
    W, H = qr_img.size
    logo_size = int(W * logo_frac)
    cutout_size = int(logo_size * padding)

    # Resize logo
    logo = logo_img.convert("RGBA").resize((logo_size, logo_size))

    # Build hex masks
    hex_mask_cutout = Image.new("L", (cutout_size, cutout_size), 0)
    draw_cutout = ImageDraw.Draw(hex_mask_cutout)
    draw_cutout.polygon(hex_points(cutout_size), fill=255)

    hex_mask_logo = Image.new("L", (logo_size, logo_size), 0)
    draw_logo = ImageDraw.Draw(hex_mask_logo)
    draw_logo.polygon(hex_points(logo_size), fill=255)

    # Clear QR center
    center = ((W - cutout_size)//2, (H - cutout_size)//2)
    white_hex = Image.new("RGBA", (cutout_size, cutout_size), (255,255,255,255))
    qr_img.paste(white_hex, center, mask=hex_mask_cutout)

    # Paste logo in center
    pos = ((W - logo_size)//2, (H - logo_size)//2)
    logo_hex = Image.new("RGBA", (logo_size, logo_size), (0,0,0,0))
    logo_hex.paste(logo, (0,0), mask=hex_mask_logo)
    qr_img.paste(logo_hex, pos, mask=logo_hex)
    return qr_img

def compose_sticker(serial, qr_img, sticker_cm=8.0, serial_width_ratio=0.5, dpi=600, background_img=None,
                    qr_x_offset_pct=0, qr_y_offset_pct=0, serial_y_offset_pct=0,
                    qr_abs_xy_px=None, qr_draw_override_px=None,
                    serial_abs_xy_px=None, serial_font_name="Helvetica-Bold", serial_font_px=0):
    """Return PNG bytes and PDF bytes of a sticker with large serial over the QR, optionally on a background template."""
    # Pixel canvas from physical size & DPI
    px = int(sticker_cm / 2.54 * dpi)

    # Offsets in pixels (percent of full canvas size)
    qr_x_offset_px = int((qr_x_offset_pct / 100.0) * px)
    qr_y_offset_px = int((qr_y_offset_pct / 100.0) * px)
    serial_y_offset_px = int((serial_y_offset_pct / 100.0) * px)

    # Base canvas (background if provided, else white)
    if background_img is not None:
        # Ensure RGBA and fit to canvas
        bg_rgba = background_img.convert("RGBA").resize((px, px), Image.LANCZOS)
        canvas_img = Image.new("RGBA", (px, px), (255, 255, 255, 255))
        canvas_img.alpha_composite(bg_rgba, (0, 0))
    else:
        canvas_img = Image.new("RGBA", (px, px), (255,255,255,255))

    # Layout parameters
    side_margin = int(0.1 * px)           # 10% margin
    text_area = int(0.18 * px)            # top area for serial
    gap = int(0.02 * px)                  # gap between text & QR
    qr_max = px - 2*side_margin
    qr_max_h = px - text_area - gap - side_margin
    qr_draw = min(qr_max, qr_max_h)

    if isinstance(qr_draw_override_px, int) and qr_draw_override_px > 0:
        qr_draw = min(qr_draw_override_px, px)  # clamp

    # Resize QR to fit (keep crisp edges)
    qr_resized = qr_img.resize((qr_draw, qr_draw), Image.NEAREST)

    # Serial font sizing by target width / override
    draw = ImageDraw.Draw(canvas_img)
    # Try to load requested font family; fall back through a few common names
    font_candidates = []
    if serial_font_name == "Helvetica-Bold":
        font_candidates = ["Helvetica-Bold", "Arial Bold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]
    elif serial_font_name == "Arial":
        font_candidates = ["Arial.ttf", "Arial Bold.ttf", "arialbd.ttf", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"]
    elif serial_font_name == "DejaVuSans-Bold":
        font_candidates = ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arialbd.ttf", "Arial Bold.ttf"]
    else:
        font_candidates = ["DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "Arial.ttf", "arialbd.ttf"]
    font = None
    for fname in font_candidates:
        try:
            font = ImageFont.truetype(fname, size= max(serial_font_px, 10) if serial_font_px else 10)
            font.loaded_name = fname  # stash for reuse
            break
        except:
            continue
    if font is None:
        font = ImageFont.load_default()

    target_w = int(serial_width_ratio * qr_draw)
    if serial_font_px and serial_font_px > 0:
        font_final = font  # already sized
    else:
        size = 10
        chosen_font_name = getattr(font, "loaded_name", "DejaVuSans-Bold.ttf")
        while size < 1000:
            try:
                f = ImageFont.truetype(chosen_font_name, size=size)
            except:
                try:
                    f = ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
                except:
                    f = ImageFont.load_default()
                    break
            bbox = draw.textbbox((0, 0), serial, font=f)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if w >= target_w or h > text_area*0.9:
                break
            size += 2
        font_final = f if 'f' in locals() else font

    bbox = draw.textbbox((0, 0), serial, font=font_final)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    # Paste QR (centered below the serial band)
    if qr_abs_xy_px is not None:
        qr_x, qr_y = int(qr_abs_xy_px[0]), int(qr_abs_xy_px[1])
    else:
        qr_x = (px - qr_draw) // 2 + qr_x_offset_px
        qr_y = px - side_margin - qr_draw + qr_y_offset_px
    canvas_img.paste(qr_resized, (qr_x, qr_y))

    # Draw serial (centered in the top band)
    if serial_abs_xy_px is not None:
        text_x, text_y = int(serial_abs_xy_px[0]), int(serial_abs_xy_px[1])
    else:
        text_x = (px - w) // 2
        text_y = (text_area - h) // 2 + serial_y_offset_px
    draw.text((text_x, text_y), serial, fill="black", font=font_final)

    # Export PNG
    png_bytes = BytesIO()
    canvas_img.save(png_bytes, format="PNG", dpi=(dpi, dpi))
    png_bytes.seek(0)

    # Export PDF
    pdf_bytes = BytesIO()
    page = (sticker_cm * cm, sticker_cm * cm)
    c = canvas.Canvas(pdf_bytes, pagesize=page)

    # Convert px offsets to points for PDF (72 pt/in)
    px_to_pt = 72.0 / dpi
    qr_x_offset_pt = qr_x_offset_px * px_to_pt
    qr_y_offset_pt = qr_y_offset_px * px_to_pt
    serial_y_offset_pt = serial_y_offset_px * px_to_pt

    # Draw background on PDF if supplied
    if background_img is not None:
        bg_tmp = BytesIO()
        background_img.convert("RGBA").resize((px, px), Image.LANCZOS).save(bg_tmp, format="PNG")
        bg_tmp.seek(0)
        c.drawImage(ImageReader(bg_tmp), 0, 0, width=page[0], height=page[1], mask='auto')

    # Serial on PDF (scaled to target width and text band)
    from reportlab.pdfbase.pdfmetrics import stringWidth
    unit_w = stringWidth(serial, serial_font_name if serial_font_name else "Helvetica-Bold", 1)
    target_w_pt = (target_w / dpi) * 72.0
    if serial_font_px and serial_font_px > 0:
        # Convert PNG px to points (approximate 1 px at given DPI to points)
        font_sz = serial_font_px * px_to_pt
    else:
        font_sz = target_w_pt / unit_w if unit_w > 0 else 10
        text_area_pt = (text_area / dpi) * 72.0
        font_sz = min(font_sz, text_area_pt * 0.9)
    c.setFont(serial_font_name if serial_font_name else "Helvetica-Bold", font_sz)

    if serial_abs_xy_px is not None:
        sx_pt = serial_abs_xy_px[0] * px_to_pt
        sy_pt = (px - serial_abs_xy_px[1]) * px_to_pt  # convert from top-left origin (PNG) to PDF bottom-left origin
        c.drawString(sx_pt, sy_pt, serial)
    else:
        text_area_pt = (text_area / dpi) * 72.0
        c.drawCentredString(page[0]/2, page[1] - text_area_pt + (text_area_pt - font_sz)/2 + serial_y_offset_pt, serial)

    # QR on PDF
    qr_png = BytesIO()
    qr_resized.save(qr_png, format="PNG")
    qr_png.seek(0)
    qr_draw_pt = (qr_draw / dpi) * 72.0
    if qr_abs_xy_px is not None:
        qr_x_pt = qr_abs_xy_px[0] * px_to_pt
        # Convert from PNG's top-left origin to PDF's bottom-left origin
        qr_y_pt = (px - (qr_abs_xy_px[1] + qr_draw)) * px_to_pt
    else:
        qr_x_pt = (page[0] - qr_draw_pt)/2 + qr_x_offset_pt
        qr_y_pt = side_margin / dpi * 72.0 + qr_y_offset_pt
    c.drawImage(ImageReader(qr_png), qr_x_pt, qr_y_pt, width=qr_draw_pt, height=qr_draw_pt, mask='auto')

    c.showPage()
    c.save()
    pdf_bytes.seek(0)

    return png_bytes, pdf_bytes

# ---------- Live Preview ----------
if enable_preview and drag_mode and HAS_CANVAS:
    st.subheader("Preview (drag/resize)")
    # Build a background to show on the canvas
    px_preview = int(sticker_size_cm / 2.54 * dpi)
    bg_for_canvas = Image.new("RGBA", (px_preview, px_preview), (255,255,255,255))
    if bg_file:
        try:
            _bg_canvas = Image.open(bg_file).convert("RGBA").resize((px_preview, px_preview), Image.LANCZOS)
            bg_for_canvas.alpha_composite(_bg_canvas)
        except Exception:
            pass
    # instructions
    st.markdown("Draw **one rectangle for the QR** and **one rectangle for the Serial text area**. You can drag/resize them. The largest rectangle will be treated as the QR.")
    canvas_result = st_canvas(
        fill_color="rgba(0,0,0,0)",
        stroke_width=2,
        background_image=bg_for_canvas,
        update_streamlit=True,
        height=px_preview,
        width=px_preview,
        drawing_mode="rect",
        key="layout_canvas",
    )
    qr_abs_xy_px = None
    qr_draw_override_px = None
    serial_abs_xy_px = None
    if canvas_result.json_data is not None and "objects" in canvas_result.json_data:
        rects = [o for o in canvas_result.json_data["objects"] if o.get("type") == "rect"]
        if rects:
            # Determine which rectangle is QR (largest area), the other is serial
            rects_sorted = sorted(rects, key=lambda r: r.get("width",0)*r.get("height",0), reverse=True)
            qr_rect = rects_sorted[0]
            qr_abs_xy_px = (int(qr_rect.get("left",0)), int(qr_rect.get("top",0)))
            qr_draw_override_px = int(min(qr_rect.get("width",0), qr_rect.get("height",0)))
            if len(rects_sorted) > 1:
                serial_rect = rects_sorted[1]
                # place serial text near center of second rectangle
                serial_abs_xy_px = (int(serial_rect.get("left",0)), int(serial_rect.get("top",0)))
    # Now render a preview using these placements if available
    preview_serial = "PREVIEW001"
    preview_url = "https://hexmodal.com"
    if csv_file:
        try:
            _df_preview = pd.read_csv(csv_file)
            _df_preview.columns = [c.strip().title() for c in _df_preview.columns]
            for _, _row in _df_preview.iterrows():
                s = str(_row.get("Serial", "")).strip()
                u = str(_row.get("Url", "")).strip()
                if s and u:
                    preview_serial, preview_url = s, u
                    break
        except Exception:
            pass
    _qr = make_qr(preview_url, ec_level, box_size=box_size, border=2)
    if logo_file:
        try:
            _logo_img = Image.open(logo_file).convert("RGBA")
            _qr = paste_logo_hex(_qr, _logo_img, logo_frac=logo_scale/100.0, padding=cutout_padding)
        except Exception:
            pass
    _bg_img = None
    if bg_file:
        try:
            _bg_img = Image.open(bg_file).convert("RGBA")
        except Exception:
            _bg_img = None
    _png_bytes, _ = compose_sticker(
        preview_serial, _qr,
        sticker_cm=sticker_size_cm,
        serial_width_ratio=serial_width_pct/100.0,
        dpi=dpi,
        background_img=_bg_img,
        qr_abs_xy_px=qr_abs_xy_px,
        qr_draw_override_px=qr_draw_override_px,
        serial_abs_xy_px=serial_abs_xy_px,
        serial_font_name=serial_font_name,
        serial_font_px=serial_font_px
    )
    st.image(_png_bytes.getvalue(), caption="Live preview (drag mode)", use_container_width=True)
    st.stop()

if enable_preview:
    st.subheader("Preview")
    # Try to use the first valid row in the CSV; otherwise allow manual inputs
    preview_serial = None
    preview_url = None
    if csv_file:
        try:
            _df_preview = pd.read_csv(csv_file)
            _df_preview.columns = [c.strip().title() for c in _df_preview.columns]
            for _, _row in _df_preview.iterrows():
                s = str(_row.get("Serial", "")).strip()
                u = str(_row.get("Url", "")).strip()
                if s and u:
                    preview_serial, preview_url = s, u
                    break
        except Exception:
            pass
    if preview_serial is None:
        col1, col2 = st.columns(2)
        with col1:
            preview_serial = st.text_input("Preview serial", value="PREVIEW001")
        with col2:
            preview_url = st.text_input("Preview URL", value="https://hexmodal.com")
    # Build preview assets
    if preview_serial and preview_url:
        _qr = make_qr(preview_url, ec_level, box_size=box_size, border=2)
        if logo_file:
            try:
                _logo_img = Image.open(logo_file).convert("RGBA")
                _qr = paste_logo_hex(_qr, _logo_img, logo_frac=logo_scale/100.0, padding=cutout_padding)
            except Exception:
                pass
        _bg_img = None
        if bg_file:
            try:
                _bg_img = Image.open(bg_file).convert("RGBA")
            except Exception:
                _bg_img = None
        _png_bytes, _ = compose_sticker(
            preview_serial, _qr,
            sticker_cm=sticker_size_cm,
            serial_width_ratio=serial_width_pct/100.0,
            dpi=dpi,
            background_img=_bg_img,
            qr_x_offset_pct=qr_x_offset_pct,
            qr_y_offset_pct=qr_y_offset_pct,
            serial_y_offset_pct=serial_y_offset_pct,
            serial_font_name=serial_font_name,
            serial_font_px=serial_font_px
        )
        st.image(_png_bytes.getvalue(), caption="Live preview (PNG)", use_container_width=True)
# ---------- End Preview ----------

st.subheader("2) Generate Stickers")
if st.button("Generate") and csv_file:
    try:
        df = pd.read_csv(csv_file)
        df.columns = [c.strip().title() for c in df.columns]
        if "Serial" not in df.columns or "Url" not in df.columns:
            st.error("CSV must contain columns: Serial, URL")
        else:
            logo_img = Image.open(logo_file).convert("RGBA") if logo_file else None
            background_img = Image.open(bg_file).convert("RGBA") if bg_file else None

            png_zip_mem = BytesIO()
            pdf_zip_mem = BytesIO()
            png_zip = zipfile.ZipFile(png_zip_mem, mode="w", compression=zipfile.ZIP_DEFLATED)
            pdf_zip = zipfile.ZipFile(pdf_zip_mem, mode="w", compression=zipfile.ZIP_DEFLATED)

            preview_cols = st.columns(3)

            for i, row in df.iterrows():
                serial = str(row["Serial"]).strip()
                url = str(row["Url"]).strip()
                if not serial or not url:
                    continue

                qr = make_qr(url, ec_level, box_size=box_size, border=2)
                if logo_img:
                    qr = paste_logo_hex(qr, logo_img, logo_frac=logo_scale/100.0, padding=cutout_padding)

                png_bytes, pdf_bytes = compose_sticker(
                    serial, qr,
                    sticker_cm=sticker_size_cm,
                    serial_width_ratio=serial_width_pct/100.0,
                    dpi=dpi,
                    background_img=background_img,
                    qr_x_offset_pct=qr_x_offset_pct,
                    qr_y_offset_pct=qr_y_offset_pct,
                    serial_y_offset_pct=serial_y_offset_pct,
                    serial_font_name=serial_font_name,
                    serial_font_px=serial_font_px
                )

                png_zip.writestr(f"{serial}_sticker.png", png_bytes.getvalue())
                pdf_zip.writestr(f"{serial}_sticker.pdf", pdf_bytes.getvalue())

                if i < 3:
                    with preview_cols[i % 3]:
                        st.image(png_bytes.getvalue(), caption=serial, use_container_width=True)

            png_zip.close()
            pdf_zip.close()

            st.success("Done! Download your files below.")
            st.download_button("📦 Download PNGs ZIP", data=png_zip_mem.getvalue(), file_name="hexmodal_stickers_png.zip", mime="application/zip")
            st.download_button("📦 Download PDFs ZIP", data=pdf_zip_mem.getvalue(), file_name="hexmodal_stickers_pdf.zip", mime="application/zip")

    except Exception as e:
        st.exception(e)
else:
    st.info("Upload your CSV (and logo if desired), then click **Generate**.")
