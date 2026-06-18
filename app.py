import streamlit as st
import tempfile
from pathlib import Path
from pdf_to_word import load_songs, setup_document, write_song, convert_pdf_to_pdf, DEFAULT_FONT_SIZE, DEFAULT_N_COLS

st.set_page_config(page_title="Pjesme → Word/PDF", page_icon="🎵", layout="centered")

st.title("🎵 Pjesmarica konverter")
st.write("Uploadaj PDF s tekstovima pjesama — dobiješ formatiran Word i PDF.")

uploaded = st.file_uploader("Odaberi PDF", type="pdf")

# ── Postavke ──────────────────────────────────────────────────────────────────
st.subheader("Postavke")

col_a, col_b = st.columns(2)
with col_a:
    font_size = st.slider(
        "Veličina teksta (pt)",
        min_value=7,
        max_value=14,
        value=DEFAULT_FONT_SIZE,
        step=1,
    )
with col_b:
    n_cols = st.radio(
        "Broj stupaca",
        options=[2, 3],
        index=DEFAULT_N_COLS - 2,
        horizontal=True,
    )

# ── Konverzija ────────────────────────────────────────────────────────────────
if uploaded:
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_in = Path(tmpdir) / "ulaz.pdf"
        pdf_in.write_bytes(uploaded.getvalue())

        with st.spinner("Učitavam pjesme..."):
            songs = load_songs(str(pdf_in))

        if not songs:
            st.error("Nije pronađena nijedna pjesma u PDF-u.")
        else:
            st.success(f"Pronađeno {len(songs)} pjesama.")

            with st.spinner("Generiram dokumente..."):
                # Generiraj DOCX
                docx_out = Path(tmpdir) / "izlaz.docx"
                doc = setup_document(font_size=font_size, n_cols=n_cols)
                for idx, blocks in enumerate(songs):
                    write_song(doc, blocks, first=(idx == 0), font_size=font_size)
                doc.save(str(docx_out))

                # Generiraj PDF
                pdf_out = Path(tmpdir) / "izlaz.pdf"
                convert_pdf_to_pdf(songs, str(pdf_out), font_size=font_size, n_cols=n_cols)

            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    "⬇️ Preuzmi Word (.docx)",
                    data=docx_out.read_bytes(),
                    file_name=Path(uploaded.name).stem + ".docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                )
            with col2:
                st.download_button(
                    "⬇️ Preuzmi PDF",
                    data=pdf_out.read_bytes(),
                    file_name=Path(uploaded.name).stem + "_out.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )

            st.caption(f"Postavke: {font_size}pt · {n_cols} stupca")
