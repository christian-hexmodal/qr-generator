
# Hexmodal QR Sticker Generator (Streamlit)

Generate branded QR stickers from a CSV with **Serial** and **URL**:
- Hexagonal cutout in center of QR with your **black Hexmodal logo**
- Big, bold serial centered above QR
- Per-serial **PDF** (vector page at exact size) and **high‑res PNG**
- One-click ZIP downloads

## Run locally
```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## CSV format
```csv
Serial,URL
M00B,https://console.hexmodal.com/redirect/?s=M00B
M00C,https://console.hexmodal.com/redirect/?s=M00C
```

## Notes
- PNG DPI and sticker size are configurable in the sidebar.
- PDF is generated with ReportLab at the exact physical size you choose.
- No Poppler required.
