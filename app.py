import streamlit as st
import pandas as pd
import re
from urllib.parse import quote
from datetime import datetime
from zoneinfo import ZoneInfo

# === CONFIG: Google Sheet ===
SHEET_ID = "1KeLHH2u_BmPsvaPMX2oxw0cbeHLV8CdMiphHtFvOfTY"
GID = "0"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"

# === UTIL: format waktu WIB ===
MONTH_ID = {
    1: "Januari",
    2: "Februari",
    3: "Maret",
    4: "April",
    5: "Mei",
    6: "Juni",
    7: "Juli",
    8: "Agustus",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Desember",
}


def now_wib_str():
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    return f"{now.day:02d} {MONTH_ID[now.month]} {now.year}, {now:%H:%M:%S} WIB"


# === DATA LOADER ===
def clean_region_name(val):
    """Bersihkan nama provinsi/kabupaten: strip, rapikan spasi, title-case + beberapa pengecualian."""
    if pd.isna(val):
        return ""
    s = str(val).strip()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)  # rapikan spasi
    low = s.lower()

    # Pengecualian umum
    special = {
        "dki jakarta": "DKI Jakarta",
        "di yogyakarta": "DI Yogyakarta",
    }
    if low in special:
        return special[low]

    return s.title()


@st.cache_data(ttl=300)
def load_raw_data() -> pd.DataFrame:
    # Read sheet as CSV, no header yet
    raw = pd.read_csv(SHEET_CSV_URL, header=None)

    # Cari baris header (kolom pertama == "No")
    header_row_candidates = raw.index[raw.iloc[:, 0].astype(str).str.strip() == "No"]
    if len(header_row_candidates) == 0:
        st.error("Tidak menemukan baris header dengan kolom pertama 'No'. Mohon cek format Google Sheet.")
        st.stop()
    header_row = int(header_row_candidates[0])

    headers = raw.iloc[header_row].tolist()

    # De-dupe header
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

    data = raw.iloc[header_row + 1:].copy()
    data.columns = cleaned_headers
    data = data.dropna(how="all")

    # Drop contoh "No = 0"
    if "No" in data.columns:
        data = data[data["No"].astype(str).str.strip() != "0"]

    # Bersihkan provinsi & kabupaten
    for col in ["Provinsi", "Kabupaten"]:
        if col in data.columns:
            data[col] = data[col].apply(clean_region_name)

    return data


def add_lat_lon_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Cari kolom Lat/Long & buat kolom 'lat' dan 'lon' untuk peta."""
    lat_col, lon_col = None, None

    # Case: ada kolom Lat & Long / Latitude & Longitude
    for c in df.columns:
        cl = c.lower()
        if cl in ("lat", "latitude"):
            lat_col = c
        if cl in ("long", "longitude", "lng"):
            lon_col = c

    if lat_col and lon_col:
        df["lat"] = pd.to_numeric(df[lat_col], errors="coerce")
        df["lon"] = pd.to_numeric(df[lon_col], errors="coerce")
        return df

    # Case: ada 1 kolom gabungan (misal: "Lat Long")
    combo_col = None
    for c in df.columns:
        cl = c.lower()
        if "lat" in cl and "long" in cl:
            combo_col = c
            break

    if combo_col:
        def parse_ll(v):
            if pd.isna(v):
                return (None, None)
            s = str(v).strip()
            if not s:
                return (None, None)
            parts = re.split(r"[ ,;]+", s)
            if len(parts) < 2:
                return (None, None)
            lat_s, lon_s = parts[0], parts[1]
            try:
                lat = float(lat_s.replace(",", "."))
                lon = float(lon_s.replace(",", "."))
                return (lat, lon)
            except Exception:
                return (None, None)

        lat_vals, lon_vals = [], []
        for v in df[combo_col]:
            lat, lon = parse_ll(v)
            lat_vals.append(lat)
            lon_vals.append(lon)
        df["lat"] = lat_vals
        df["lon"] = lon_vals

    return df


# === HELPERS ===
def normalize_phone(phone):
    """Convert phone to WhatsApp-ready format: 62xxxxxxxxx."""
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
        # kalau format lain, biarkan
        pass
    return digits


def get_update_columns(df: pd.DataFrame):
    return [c for c in df.columns if str(c).strip().startswith("Update")]


def compute_last_update(row: pd.Series, update_cols):
    """Ambil update terakhir dari kolom Update 1..N (kanan ke kiri)."""
    for col in reversed(update_cols):
        val = row.get(col, None)
        if pd.notna(val) and str(val).strip():
            return f"{col}: {val}"
    return ""


def build_whatsapp_message(
    row: pd.Series,
    wa_pusat_col,
    wa_korlap_col,
    last_update,
    include_footer=True,
):
    prov = str(row.get("Provinsi", "")).strip()
    kab = str(row.get("Kabupaten", "")).strip()
    posko = str(
        row.get("Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia", "")
    ).strip()
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
        parts += ["", "*Update Terakhir*:", last_update]

    if gmap:
        parts += ["", "📍 Map: " + gmap]
    if foto:
        parts += ["", "🖼 Dokumentasi: " + foto]

    if include_footer:
        parts += [
            "",
            "👉 Mohon update perkembangan terakhir di form bersama:",
            "http://tiny.cc/updaterelawan",
            "",
            "_Satu sumber data jaringan relawan_",
        ]

    body = "\n".join(parts).strip()

    # Default WA link: kirim ke korlap kalau ada, kalau tidak broadcast kosong
    if wa_korlap_norm:
        wa_link = f"https://wa.me/{wa_korlap_norm}?text={quote(body)}"
    else:
        wa_link = f"https://wa.me/?text={quote(body)}"

    return body, wa_link


# === MAIN APP ===
def main():
    st.set_page_config(
        page_title="Koordinasi Bantuan Emergency",
        layout="wide",
    )

    st.title("📊 Update Harian Koordinasi Bantuan Emergency")
    st.caption(f"Waktu saat ini (WIB): {now_wib_str()}")

    # Banner ajakan update relawan
    st.info(
        "Untuk update perkembangan posko & jaringan relawan, "
        "gunakan form bersama: **http://tiny.cc/updaterelawan**\n\n"
        "_Satu sumber data jaringan relawan_"
    )

    df = load_raw_data()
    df = add_lat_lon_columns(df)

    if df.empty:
        st.warning("Belum ada data di Google Sheet.")
        st.stop()

    # Identify WA columns by posisi relatif
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

    # Last Update
    update_cols = get_update_columns(df)
    if update_cols:
        df["Last Update"] = df.apply(lambda r: compute_last_update(r, update_cols), axis=1)
    else:
        df["Last Update"] = ""

    # Flag siap dibantu (dipakai di metric, bukan filter)
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

        dukungan_only = st.checkbox(
            "Hanya yang punya 'Dukungan yang bisa di offer ke sesama jaringan'",
            value=False,
        )

        kebutuhan_filter = st.text_input("Filter 'List Kebutuhan Mendesak' (kata kunci)")

        search_text = st.text_input("Cari kata kunci umum (posko / PIC / update)")

    # Terapkan filter
    filtered = df.copy()
    if prov_sel:
        filtered = filtered[filtered["Provinsi"].isin(prov_sel)]
    if kab_sel:
        filtered = filtered[filtered["Kabupaten"].isin(kab_sel)]

    if dukungan_only and "Dukungan yang bisa di offer ke sesama jaringan" in filtered.columns:
        col_dukung = "Dukungan yang bisa di offer ke sesama jaringan"
        filtered = filtered[
            filtered[col_dukung].astype(str).str.strip().ne("")
            & filtered[col_dukung].notna()
        ]

    if kebutuhan_filter and "List Kebutuhan Mendesak" in filtered.columns:
        pattern2 = kebutuhan_filter.lower()
        filtered = filtered[
            filtered["List Kebutuhan Mendesak"]
            .astype(str)
            .str.lower()
            .str.contains(pattern2)
        ]

    if search_text:
        pattern = search_text.lower()

        def matches(row):
            cols_to_search = [
                "Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia",
                "List Kebutuhan Mendesak",
                "Nama Relawan Koordinator Lapangan",
                "Nama Relawan Koordinator Pusat - Posisi Standby",
                "Last Update",
                "Dukungan yang bisa di offer ke sesama jaringan",
            ]
            for c in cols_to_search:
                if c in row.index:
                    val = str(row.get(c, "")).lower()
                    if pattern in val:
                        return True
            return False

        filtered = filtered[filtered.apply(matches, axis=1)]

    # === HEADER DASHBOARD (prov-kab, jumlah jaringan, kebutuhan mendesak) ===
    prov_text = ", ".join(prov_sel) if prov_sel else "Semua Provinsi"
    kab_text = ", ".join(kab_sel) if kab_sel else "Semua Kabupaten/Kota"

    st.subheader(f"Dashboard – {prov_text} – {kab_text}")
    st.markdown(f"**Jumlah jaringan (lokasi terfilter)**: {len(filtered)}")

    if "List Kebutuhan Mendesak" in filtered.columns:
        needs_series = (
            filtered["List Kebutuhan Mendesak"]
            .dropna()
            .astype(str)
            .str.strip()
        )
        unique_needs = [n for n in needs_series.unique() if n]
        if unique_needs:
            st.markdown("**Ringkasan kebutuhan mendesak (contoh):**")
            for n in unique_needs[:5]:
                st.markdown(f"- {n}")

    # === SUMMARY METRICS ===
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Lokasi (semua data)", len(df))
    with col2:
        st.metric("Lokasi Siap Dibantu (logika internal)", int(df["Siap Dibantu"].sum()))
    with col3:
        st.metric("Lokasi Terfilter Saat Ini", len(filtered))

    # === MAP VIEW ===
    st.markdown("### 🗺️ Peta Lokasi Terfilter")
    if "lat" in filtered.columns and "lon" in filtered.columns:
        map_data = filtered[["lat", "lon"]].dropna()
        if not map_data.empty:
            st.map(map_data, latitude="lat", longitude="lon", size=60)
        else:
            st.caption("Tidak ada koordinat lat/long yang bisa ditampilkan.")
    else:
        st.caption("Kolom koordinat (lat/long) belum tersedia di data.")

    # === TABLE VIEW ===
    st.markdown("### 📍 Daftar Lokasi")

    if filtered.empty:
        st.info("Tidak ada lokasi yang cocok dengan filter.")
        return

    show_cols = [
        "No",
        "Provinsi",
        "Kabupaten",
        "Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia",
        "List Kebutuhan Mendesak",
        "Dukungan yang bisa di offer ke sesama jaringan",
        "Nama Relawan Koordinator Lapangan",
        "Last Update",
        "Siap Dibantu",
    ]
    show_cols = [c for c in show_cols if c in filtered.columns]

    st.dataframe(
        filtered[show_cols],
        use_container_width=True,
        hide_index=True,
        height=380,
    )

    # === OPSI PILIH LOKASI (untuk WA) ===
    # Build label list
    options = []
    for idx, row in filtered.iterrows():
        no = row.get("No", idx)
        prov = str(row.get("Provinsi", "")).strip()
        kab = str(row.get("Kabupaten", "")).strip()
        posko = str(
            row.get(
                "Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia",
                "",
            )
        ).strip()
        label = f"{no} – {prov} / {kab} – {posko[:60]}{'…' if len(posko) > 60 else ''}"
        options.append((label, idx))

    labels = [o[0] for o in options]
    label_to_idx = {lbl: idx for lbl, idx in options}

    st.markdown("---")
    st.markdown("### 📲 Detail Lokasi & Kirim ke WhatsApp (single)")

    selected_label = st.selectbox("Pilih satu lokasi", labels)
    selected_idx = label_to_idx[selected_label]
    selected_row = filtered.loc[selected_idx]
    last_update = selected_row.get("Last Update", "")

    body_single, wa_link_single = build_whatsapp_message(
        selected_row, wa_pusat_col, wa_korlap_col, last_update, include_footer=True
    )

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

    st.markdown("#### Pesan WhatsApp (single, bisa di-copy)")
    st.text_area("Body WA (single)", value=body_single, height=260, key="wa_single_body")

    st.markdown(
        f"[🔗 Buka WhatsApp dengan pesan ini (single)]({wa_link_single})",
        help="Klik untuk membuka WhatsApp Web / aplikasi dengan pesan yang sudah terisi.",
    )

    # === MULTI-SELECT UNTUK WA GABUNGAN ===
    st.markdown("---")
    st.markdown("### ✅ Pilih beberapa lokasi untuk update WhatsApp gabungan")

    multi_selected_labels = st.multiselect(
        "Pilih beberapa lokasi",
        labels,
        key="multi_select_locations",
    )

    if multi_selected_labels:
        bodies = []
        for lbl in multi_selected_labels:
            idx = label_to_idx[lbl]
            row = filtered.loc[idx]
            lu = row.get("Last Update", "")
            body_partial, _ = build_whatsapp_message(
                row, wa_pusat_col, wa_korlap_col, lu, include_footer=False
            )
            bodies.append(body_partial)

        combined_body = "\n\n——————————\n\n".join(bodies)
        combined_body += (
            "\n\n👉 Mohon update perkembangan terakhir di form bersama:\n"
            "http://tiny.cc/updaterelawan\n\n"
            "_Satu sumber data jaringan relawan_"
        )

        wa_link_multi = f"https://wa.me/?text={quote(combined_body)}"

        st.markdown("#### Pesan WhatsApp (gabungan, bisa di-copy)")
        st.text_area(
            "Body WA (multi)",
            value=combined_body,
            height=320,
            key="wa_multi_body",
        )
        st.markdown(
            f"[🔗 Buka WhatsApp dengan pesan ini (multi)]({wa_link_multi})",
            help="Klik untuk membuka WhatsApp Web / aplikasi dengan pesan gabungan.",
        )
    else:
        st.caption("Pilih satu atau lebih lokasi di atas untuk membuat pesan WhatsApp gabungan.")


if __name__ == "__main__":
    main()
