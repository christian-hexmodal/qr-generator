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
from reportlab.lib.utils import ImageReader  # <-- added

st.set_page_config(page_title="Hexmodal QR Sticker Generator", page_icon="🔳", layout="centered")

st.title("🔳 Hexmodal QR Sticker Generator")
st.caption("Upload a CSV of Serial/URL and a black Hexmodal logo — get per-serial PDF + high-res PNG stickers with a hexagonal logo cutout.")

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

st.subheader("1) Upload Inputs")
csv_file = st.file_uploader("CSV with columns: Serial, URL", type=["csv"])
logo_file = st.file_uploader("Black Hexmodal logo (PNG)", type=["png"])

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

def compose_sticker(serial, qr_img, sticker_cm=8.0, serial_width_ratio=0.5, dpi=600):
    """Return PNG bytes and PDF bytes of a sticker with large serial over the QR."""
    # Pixel canvas from physical size & DPI
    px = int(sticker_cm / 2.54 * dpi)
    canvas_img = Image.new("RGBA", (px, px), (255,255,255,255))

    # Layout parameters
    side_margin = int(0.1 * px)           # 10% margin
    text_area = int(0.18 * px)            # top area for serial
    gap = int(0.02 * px)                  # gap between text & QR
    qr_max = px - 2*side_margin
    qr_max_h = px - text_area - gap - side_margin
    qr_draw = min(qr_max, qr_max_h)

    # Resize QR to fit
    qr_resized = qr_img.resize((qr_draw, qr_draw), Image.NEAREST)  # keep crisp edges

    # Serial font sizing by target width
    draw = ImageDraw.Draw(canvas_img)
    # Try bold font; fallback to default
    font = None
    for fname in ["arialbd.ttf", "Helvetica-Bold", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"]:
        try:
            font = ImageFont.truetype(fname, size=10)
            break
        except:
            continue
    if font is None:
        font = ImageFont.load_default()

    # Increase font size until width ≈ serial_width_ratio * qr_draw
    target_w = int(serial_width_ratio * qr_draw)
    size = 10
    while size < 1000:
        try:
            f = ImageFont.truetype(font.path if hasattr(font, "path") else "DejaVuSans-Bold.ttf", size=size)
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

    # Final font
    font_final = f if 'f' in locals() else font
    bbox = draw.textbbox((0, 0), serial, font=font_final)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    # Paste QR
    qr_x = (px - qr_draw) // 2
    qr_y = px - side_margin - qr_draw
    canvas_img.paste(qr_resized, (qr_x, qr_y))

    # Draw serial centered in top area
    text_x = (px - w) // 2
    text_y = (text_area - h) // 2
    draw.text((text_x, text_y), serial, fill="black", font=font_final)

    # Export PNG
    png_bytes = BytesIO()
    canvas_img.save(png_bytes, format="PNG", dpi=(dpi, dpi))
    png_bytes.seek(0)

    # Export PDF
    pdf_bytes = BytesIO()
    page = (sticker_cm * cm, sticker_cm * cm)
    c = canvas.Canvas(pdf_bytes, pagesize=page)

    # Serial in PDF
    from reportlab.pdfbase.pdfmetrics import stringWidth
    unit_w = stringWidth(serial, "Helvetica-Bold", 1)
    font_sz = target_w / (dpi / 72.0) / unit_w if unit_w > 0 else 10  # convert pixel width target to points
    # Cap by text area height in points
    text_area_pt = (text_area / dpi) * 72.0
    font_sz = min(font_sz, text_area_pt * 0.9)
    c.setFont("Helvetica-Bold", font_sz)
    c.drawCentredString(page[0]/2, page[1] - text_area_pt + (text_area_pt - font_sz)/2, serial)

    # Draw QR image into PDF (wrap bytes with ImageReader to avoid PIL image TypeError)
    qr_png = BytesIO()
    qr_resized.save(qr_png, format="PNG")
    qr_png.seek(0)
    qr_draw_pt = (qr_draw / dpi) * 72.0
    qr_x_pt = (page[0] - qr_draw_pt)/2
    qr_y_pt = side_margin / dpi * 72.0
    c.drawImage(ImageReader(qr_png), qr_x_pt, qr_y_pt, width=qr_draw_pt, height=qr_draw_pt, mask='auto')
    c.showPage()
    c.save()
    pdf_bytes.seek(0)

    return png_bytes, pdf_bytes

st.subheader("2) Generate Stickers")
if st.button("Generate") and csv_file and logo_file:
    try:
        df = pd.read_csv(csv_file)
        # Normalize headers
        df.columns = [c.strip().title() for c in df.columns]
        if "Serial" not in df.columns or "Url" not in df.columns:
            st.error("CSV must contain columns: Serial, URL")
        else:
            logo_img = Image.open(logo_file).convert("RGBA")

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

                # Build QR with logo cutout
                qr = make_qr(url, ec_level, box_size=box_size, border=2)
                qr = paste_logo_hex(qr, logo_img, logo_frac=logo_scale/100.0, padding=cutout_padding)

                # Compose sticker
                png_bytes, pdf_bytes = compose_sticker(
                    serial, qr,
                    sticker_cm=sticker_size_cm,
                    serial_width_ratio=serial_width_pct/100.0,
                    dpi=dpi
                )

                # Write to zips
                png_zip.writestr(f"{serial}_sticker.png", png_bytes.getvalue())
                pdf_zip.writestr(f"{serial}_sticker.pdf", pdf_bytes.getvalue())

                # Preview the first three
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
    st.info("Upload your CSV and logo, then click **Generate**.")