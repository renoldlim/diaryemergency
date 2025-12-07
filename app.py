import streamlit as st
import pandas as pd
import re
from urllib.parse import quote

# === CONFIG: Google Sheet ===
SHEET_ID = "1KeLHH2u_BmPsvaPMX2oxw0cbeHLV8CdMiphHtFvOfTY"
GID = "0"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"


# === DATA LOADER ===
@st.cache_data(ttl=300)
def load_data() -> pd.DataFrame:
    # Read sheet as CSV, no header yet
    raw = pd.read_csv(SHEET_CSV_URL, header=None)

    # Find the header row (first row whose first cell is "No")
    header_row_candidates = raw.index[raw.iloc[:, 0].astype(str).str.strip() == "No"]
    if len(header_row_candidates) == 0:
        st.error("Tidak menemukan baris header dengan kolom pertama 'No'. Mohon cek format Google Sheet.")
        st.stop()
    header_row = int(header_row_candidates[0])

    # Use that row as column names
    headers = raw.iloc[header_row].tolist()

    # De-duplicate headers like "No WA", "No WA" -> "No WA", "No WA (1)"
    seen = {}
    cleaned_headers = []
    for h in headers:
        h = str(h)
        if h not in seen:
            seen[h] = 0
            cleaned_headers.append(h)
        else:
            seen[h] += 1
            cleaned_headers.append(f"{h} ({seen[h]})")

    # Data starts after header_row
    data = raw.iloc[header_row + 1:].copy()
    data.columns = cleaned_headers

    # Drop fully empty rows
    data = data.dropna(how="all")

    # Drop contoh row (No == 0) if present
    if "No" in data.columns:
        data = data[data["No"].astype(str).str.strip() != "0"]

    # Normalize strings
    for col in ["Provinsi", "Kabupaten"]:
        if col in data.columns:
            data[col] = data[col].astype(str).str.strip()

    return data


# === HELPERS ===
def normalize_phone(phone) -> str | None:
    """Convert phone to WhatsApp-ready format: 62xxxxxxxxx"""
    if phone is None:
        return None
    s = str(phone)
    if not s or s.lower() == "nan":
        return None
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if digits.startswith("0"):
        digits = "62" + digits[1:]
    elif not digits.startswith("62"):
        # If other style, just leave it
        pass
    return digits


def get_update_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if str(c).strip().startswith("Update")]


def compute_last_update(row: pd.Series, update_cols: list[str]) -> str:
    """Ambil update terakhir dari kolom Update 1..19 (kanan ke kiri)."""
    for col in reversed(update_cols):
        val = row.get(col, None)
        if pd.notna(val) and str(val).strip():
            return f"{col}: {val}"
    return ""


def build_whatsapp_message(
    row: pd.Series,
    wa_pusat_col: str | None,
    wa_korlap_col: str | None,
    last_update: str,
) -> tuple[str, str]:
    prov = str(row.get("Provinsi", "")).strip()
    kab = str(row.get("Kabupaten", "")).strip()
    posko = str(row.get("Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia", "")).strip()
    needs = str(row.get("List Kebutuhan Mendesak", "")).strip()
    dapur_umum = str(row.get("Apakah Ada Dapur Umum?", "")).strip()
    dukungan = str(row.get("Dukungan yang bisa di offer ke sesama jaringan", "")).strip()
    gmap = str(row.get("Link Google Map", "")).strip()
    foto = str(row.get("Link Foto / Sosmed / Google Drive", "")).strip()

    pusat_name = str(row.get("Nama Relawan Koordinator Pusat - Posisi Standby", "")).strip()
    korlap_name = str(row.get("Nama Relawan Koordinator Lapangan", "")).strip()

    wa_pusat_raw = row.get(wa_pusat_col, "") if wa_pusat_col else ""
    wa_korlap_raw = row.get(wa_korlap_col, "") if wa_korlap_col else ""

    wa_pusat_norm = normalize_phone(wa_pusat_raw)
    wa_korlap_norm = normalize_phone(wa_korlap_raw)

    wa_pusat_pretty = str(wa_pusat_raw).strip()
    wa_korlap_pretty = str(wa_korlap_raw).strip()

    title = f"*[Koordinasi Bantuan – {prov} / {kab}]*".strip()

    parts = [
        title,
        "",
        "*Lokasi / Posko*",
        posko or "-",
        "",
        "*Provinsi*: " + (prov or "-"),
        "*Kab/Kota*: " + (kab or "-"),
        "",
        "*PIC Lapangan*: " + (korlap_name or "-") + (f" ({wa_korlap_pretty})" if wa_korlap_pretty else ""),
        "*PIC Pusat*: " + (pusat_name or "-") + (f" ({wa_pusat_pretty})" if wa_pusat_pretty else ""),
        "",
        "*List Kebutuhan Mendesak*:",
        needs or "-",
    ]
    if dapur_umum:
        parts += ["", "*Dapur Umum*: " + dapur_umum]
    if dukungan:
        parts += ["", "*Dukungan dari jaringan*: " + dukungan]
    if last_update:
        parts += ["", "*Update Terakhir*: ", last_update]

    if gmap:
        parts += ["", "📍 Map: " + gmap]
    if foto:
        parts += ["", "🖼 Dokumentasi: " + foto]

    body = "\n".join(parts).strip()

    # Link WA: default kirim ke WA (tanpa nomor spesifik) atau langsung ke PIC lapangan jika nomornya ada
    wa_link_all = f"https://wa.me/?text={quote(body)}"
    wa_link_korlap = None
    if wa_korlap_norm:
        wa_link_korlap = f"https://wa.me/{wa_korlap_norm}?text={quote(body)}"

    return body, wa_link_all if wa_link_korlap is None else wa_link_korlap


# === MAIN APP ===
def main():
    st.set_page_config(
        page_title="Koordinasi Bantuan Emergency",
        layout="wide",
    )

    st.title("📊 Update Harian Koordinasi Bantuan Emergency")
    st.caption("Data ditarik langsung dari Google Sheet. Gunakan filter untuk mencari lokasi yang siap dibantu.")

    df = load_data()
    if df.empty:
        st.warning("Belum ada data di Google Sheet.")
        st.stop()

    # Identify WA columns based on position:
    # [No, Nama Pusat, No WA (Pusat), Nama Lapangan, No WA (Lapangan), ...]
    wa_pusat_col = None
    wa_korlap_col = None
    cols = list(df.columns)

    if "Nama Relawan Koordinator Pusat - Posisi Standby" in df.columns:
        idx = cols.index("Nama Relawan Koordinator Pusat - Posisi Standby")
        if idx + 1 < len(cols):
            wa_pusat_col = cols[idx + 1]

    if "Nama Relawan Koordinator Lapangan" in df.columns:
        idx = cols.index("Nama Relawan Koordinator Lapangan")
        if idx - 1 >= 0:
            wa_korlap_col = cols[idx - 1]

    # Compute last update text
    update_cols = get_update_columns(df)
    if update_cols:
        df["Last Update"] = df.apply(lambda r: compute_last_update(r, update_cols), axis=1)
    else:
        df["Last Update"] = ""

    # Flag "siap dibantu": punya kebutuhan + punya kabupaten + punya WA korlap
    def is_ready(row):
        has_needs = bool(str(row.get("List Kebutuhan Mendesak", "")).strip())
        has_kab = bool(str(row.get("Kabupaten", "")).strip())
        wa_raw = row.get(wa_korlap_col, "") if wa_korlap_col else ""
        has_wa = normalize_phone(wa_raw) is not None
        return has_needs and has_kab and has_wa

    df["Siap Dibantu"] = df.apply(is_ready, axis=1)

    # === SIDEBAR FILTERS ===
    with st.sidebar:
        st.header("Filter")

        provinsi_list = sorted(
            [p for p in df["Provinsi"].dropna().astype(str).unique() if p and p.lower() != "nan"]
        )
        prov_sel = st.multiselect("Provinsi", provinsi_list, default=provinsi_list)

        kabupaten_list = sorted(
            [k for k in df["Kabupaten"].dropna().astype(str).unique() if k and k.lower() != "nan"]
        )
        kab_sel = st.multiselect("Kabupaten", kabupaten_list)

        ready_filter = st.radio(
            "Status",
            options=["Semua", "Hanya yang siap dibantu"],
            index=1,
        )

        search_text = st.text_input("Cari kata kunci (posko / kebutuhan / PIC)")

    filtered = df.copy()
    if prov_sel:
        filtered = filtered[filtered["Provinsi"].isin(prov_sel)]
    if kab_sel:
        filtered = filtered[filtered["Kabupaten"].isin(kab_sel)]
    if ready_filter == "Hanya yang siap dibantu":
        filtered = filtered[filtered["Siap Dibantu"]]

    if search_text:
        pattern = search_text.lower()

        def matches(row):
            cols_to_search = [
                "Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia",
                "List Kebutuhan Mendesak",
                "Nama Relawan Koordinator Lapangan",
                "Nama Relawan Koordinator Pusat - Posisi Standby",
                "Last Update",
            ]
            for c in cols_to_search:
                if c in row.index:
                    val = str(row.get(c, "")).lower()
                    if pattern in val:
                        return True
            return False

        filtered = filtered[filtered.apply(matches, axis=1)]

    # === SUMMARY METRICS ===
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Lokasi", len(df))
    with col2:
        st.metric("Lokasi Siap Dibantu", int(df["Siap Dibantu"].sum()))
    with col3:
        st.metric("Lokasi Terfilter Saat Ini", len(filtered))

    # === TABLE VIEW ===
    st.markdown("### 📍 Daftar Lokasi")

    show_cols = [
        "No",
        "Provinsi",
        "Kabupaten",
        "Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia",
        "List Kebutuhan Mendesak",
        "Nama Relawan Koordinator Lapangan",
        "Last Update",
        "Siap Dibantu",
    ]
    show_cols = [c for c in show_cols if c in filtered.columns]

    st.dataframe(
        filtered[show_cols],
        use_container_width=True,
        hide_index=True,
        height=400,
    )

    # === DETAIL + WHATSAPP ===
    st.markdown("---")
    st.markdown("### 📲 Detail Lokasi & Kirim ke WhatsApp")

    if filtered.empty:
        st.info("Tidak ada lokasi yang cocok dengan filter.")
        return

    # Pilih baris
    options = []
    for idx, row in filtered.iterrows():
        no = row.get("No", idx)
        prov = str(row.get("Provinsi", "")).strip()
        kab = str(row.get("Kabupaten", "")).strip()
        posko = str(
            row.get("Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia", "")
        ).strip()
        label = f"{no} – {prov} / {kab} – {posko[:60]}{'…' if len(posko) > 60 else ''}"
        options.append((label, idx))

    labels = [o[0] for o in options]
    selected_label = st.selectbox("Pilih lokasi", labels)
    selected_idx = dict(options)[selected_label]
    selected_row = filtered.loc[selected_idx]

    last_update = selected_row.get("Last Update", "")
    body, wa_link = build_whatsapp_message(selected_row, wa_pusat_col, wa_korlap_col, last_update)

    st.markdown("#### Ringkasan Lokasi")
    cols_detail = st.columns(2)
    with cols_detail[0]:
        st.write("**Provinsi**:", selected_row.get("Provinsi", ""))
        st.write("**Kabupaten**:", selected_row.get("Kabupaten", ""))
        st.write("**Posko / Lokasi**:")
        st.write(
            selected_row.get(
                "Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia",
                "",
            )
        )
    with cols_detail[1]:
        st.write("**PIC Lapangan**:", selected_row.get("Nama Relawan Koordinator Lapangan", ""))
        if wa_korlap_col:
            st.write("**No WA Lapangan**:", selected_row.get(wa_korlap_col, ""))
        st.write(
            "**PIC Pusat**:",
            selected_row.get("Nama Relawan Koordinator Pusat - Posisi Standby", ""),
        )
        if wa_pusat_col:
            st.write("**No WA Pusat**:", selected_row.get(wa_pusat_col, ""))
        if selected_row.get("Link Google Map", ""):
            st.write("**Link GMaps**:", selected_row.get("Link Google Map", ""))
        if selected_row.get("Link Foto / Sosmed / Google Drive", ""):
            st.write(
                "**Link Dokumentasi**:",
                selected_row.get("Link Foto / Sosmed / Google Drive", ""),
            )

    st.markdown("#### Pesan WhatsApp (bisa di-copy)")
    st.text_area("Body WA", value=body, height=260)

    st.markdown(
        f"[🔗 Buka WhatsApp dengan pesan ini]({wa_link})",
        help="Klik untuk membuka WhatsApp Web / aplikasi dengan pesan yang sudah terisi.",
    )


if __name__ == "__main__":
    main()
