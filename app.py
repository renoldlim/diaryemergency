import streamlit as st
import pandas as pd
import re
from urllib.parse import quote
from datetime import datetime
from zoneinfo import ZoneInfo
import pydeck as pdk

# === CONFIG: Google Sheet ===
SHEET_ID = "1KeLHH2u_BmPsvaPMX2oxw0cbeHLV8CdMiphHtFvOfTY"
GID = "0"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"

# === UTIL: waktu WIB ===
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
    if pd.isna(val):
        return ""
    s = str(val).strip()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    low = s.lower()
    special = {
        "dki jakarta": "DKI Jakarta",
        "di yogyakarta": "DI Yogyakarta",
    }
    if low in special:
        return special[low]
    return s.title()


@st.cache_data(ttl=180)
def load_raw_data() -> pd.DataFrame:
    raw = pd.read_csv(SHEET_CSV_URL, header=None)

    # cari baris header (kolom pertama "No")
    header_row_candidates = raw.index[raw.iloc[:, 0].astype(str).str.strip() == "No"]
    if len(header_row_candidates) == 0:
        st.error(
            "Tidak menemukan baris header dengan kolom pertama 'No'. "
            "Mohon cek format Google Sheet."
        )
        st.stop()
    header_row = int(header_row_candidates[0])

    headers = raw.iloc[header_row].tolist()

    # handle header duplikat
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

    data = raw.iloc[header_row + 1 :].copy()
    data.columns = cleaned_headers
    data = data.dropna(how="all")

    if "No" in data.columns:
        data = data[data["No"].astype(str).str.strip() != "0"]

    for col in ["Provinsi", "Kabupaten"]:
        if col in data.columns:
            data[col] = data[col].apply(clean_region_name)

    # simpan index baris asli (untuk query param detail)
    data["__row_index"] = data.index

    return data


def add_lat_lon_columns(df: pd.DataFrame) -> pd.DataFrame:
    lat_col, lon_col = None, None
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
def clean_optional(val: object) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""
    if s.lower() in ("nan", "none", "-"):
        return ""
    return s


def normalize_phone(phone):
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
        # biarkan saja (misal sudah dalam format internasional lain)
        pass
    return digits


def get_ordered_update_columns(df: pd.DataFrame, latest_first: bool = True):
    """
    Ambil semua kolom yang namanya mulai dengan 'Update'
    dan urutkan berdasarkan angka di namanya.
    Contoh: Update 1..Update 20 -> kalau latest_first=True: 20..1
    """
    found = []
    for col in df.columns:
        name = str(col).strip()
        if not name.lower().startswith("update"):
            continue
        nums = re.findall(r"(\d+)", name)
        if not nums:
            continue
        # ambil angka TERAKHIR di nama, biasanya nomor update
        n = int(nums[-1])
        found.append((n, col))

    # kalau tidak ada kolom Update, return []
    if not found:
        return []

    found.sort(key=lambda x: x[0], reverse=latest_first)
    return [col for _, col in found]


def compute_last_update(row: pd.Series, update_cols_desc):
    """
    Ambil isi update paling baru (kolom nomor terbesar) yang terisi.
    update_cols_desc sudah diurutkan dari terbesar → terkecil.
    """
    for col in update_cols_desc:
        val = row.get(col, None)
        if pd.notna(val) and str(val).strip():
            return f"{col}: {val}"
    return ""


def compute_update_level(row: pd.Series, update_cols_all):
    """Level update tertinggi (misal punya Update 1..5 → 5)."""
    max_n = 0
    for col in update_cols_all:
        val = row.get(col, None)
        if pd.notna(val) and str(val).strip():
            nums = re.findall(r"(\d+)", str(col))
            if nums:
                n = int(nums[-1])
                if n > max_n:
                    max_n = n
    return max_n


def build_whatsapp_body_for_row(row: pd.Series, update_cols_desc, wa_korlap_col):
    """Pesan WA format rapi untuk satu lokasi (dipakai di gabungan)."""
    prov = clean_optional(row.get("Provinsi", ""))
    kab = clean_optional(row.get("Kabupaten", ""))
    posko = clean_optional(
        row.get("Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia", "")
    )
    kebutuhan = clean_optional(row.get("List Kebutuhan Mendesak", ""))
    dukungan = clean_optional(row.get("Dukungan yang bisa di offer ke sesama jaringan", ""))
    gmap = clean_optional(row.get("Link Google Map", ""))
    foto = clean_optional(row.get("Link Foto / Sosmed / Google Drive", ""))

    korlap_name = clean_optional(row.get("Nama Relawan Koordinator Lapangan", ""))
    wa_raw = row.get(wa_korlap_col, "") if wa_korlap_col else ""
    wa_pretty = clean_optional(wa_raw)

    parts = [
        f"*[Koordinasi Bantuan – {prov or '-'} / {kab or '-'}]*",
        "",
        "*Posko*",
        posko or "-",
        "",
        "*PIC Lapangan*: " + (korlap_name or "-") + (f" ({wa_pretty})" if wa_pretty else ""),
        "",
    ]

    # timeline update (di WA selalu terbaru → lama)
    has_update = False
    for col in update_cols_desc:
        val = clean_optional(row.get(col, ""))
        if val:
            if not has_update:
                parts.append("*🕒 Timeline Update*")
                has_update = True
            parts.append(f"- *{col}*: {val}")
    if not has_update:
        parts.append("_Belum ada update tertulis._")

    if kebutuhan:
        parts += ["", "*List Kebutuhan Mendesak*:", kebutuhan]
    if dukungan:
        parts += ["", "*Dukungan dari jaringan*:", dukungan]
    if gmap:
        parts += ["", "📍 Map: " + gmap]
    if foto:
        parts += ["", "🖼 Dokumentasi: " + foto]

    return "\n".join(parts).strip()


# === MAIN APP ===
def main():
    st.set_page_config(
        page_title="Card Dashboard Koordinasi Bantuan",
        layout="wide",
    )

    st.title("📇 Card Dashboard Lokasi & Update")
    st.caption(f"Waktu saat ini (WIB): {now_wib_str()}")

    # --- load data ---
    df = load_raw_data()
    df = add_lat_lon_columns(df)

    if df.empty:
        st.warning("Belum ada data di Google Sheet.")
        st.stop()

    # --- update columns, dengan urutan numeric ---
    update_cols_desc = get_ordered_update_columns(df, latest_first=True)   # besar → kecil
    update_cols_asc = list(reversed(update_cols_desc))                    # kecil → besar

    if update_cols_desc:
        df["Last Update (full)"] = df.apply(
            lambda r: compute_last_update(r, update_cols_desc), axis=1
        )
        df["Update_Level"] = df.apply(
            lambda r: compute_update_level(r, update_cols_desc), axis=1
        )
    else:
        df["Last Update (full)"] = ""
        df["Update_Level"] = 0

    # identify WA kolom untuk korlap & pusat
    wa_korlap_col = None
    wa_pusat_col = None
    cols = list(df.columns)

    if "Nama Relawan Koordinator Lapangan" in df.columns:
        idx = cols.index("Nama Relawan Koordinator Lapangan")
        if idx - 1 >= 0:
            wa_korlap_col = cols[idx - 1]

    if "Nama Relawan Koordinator Pusat - Posisi Standby" in df.columns:
        idx = cols.index("Nama Relawan Koordinator Pusat - Posisi Standby")
        # asumsi: kolom WA pusat ada tepat setelah nama pusat
        if idx + 1 < len(cols):
            wa_pusat_col = cols[idx + 1]

    # --- detail mode via query param ?row= ---
    query_params = st.experimental_get_query_params()
    detail_row_param = query_params.get("row", [None])[0]

    if detail_row_param is not None:
        try:
            detail_idx = int(detail_row_param)
        except ValueError:
            detail_idx = None
        if detail_idx is not None and detail_idx in df["__row_index"].values:
            st.markdown("## 🔎 Detail Lokasi")
            row = df[df["__row_index"] == detail_idx].iloc[0]

            prov = clean_optional(row.get("Provinsi", ""))
            kab = clean_optional(row.get("Kabupaten", ""))
            posko = clean_optional(
                row.get("Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia", "")
            )
            kebutuhan = clean_optional(row.get("List Kebutuhan Mendesak", ""))
            dukungan = clean_optional(
                row.get("Dukungan yang bisa di offer ke sesama jaringan", "")
            )
            gmap = clean_optional(row.get("Link Google Map", ""))
            foto = clean_optional(row.get("Link Foto / Sosmed / Google Drive", ""))
            lat = row.get("lat", None)
            lon = row.get("lon", None)

            korlap_name = clean_optional(
                row.get("Nama Relawan Koordinator Lapangan", "")
            )
            wa_korlap_raw = row.get(wa_korlap_col, "") if wa_korlap_col else ""
            wa_korlap_norm = normalize_phone(wa_korlap_raw)
            wa_korlap_pretty = clean_optional(wa_korlap_raw)

            pusat_name = clean_optional(
                row.get("Nama Relawan Koordinator Pusat - Posisi Standby", "")
            )
            wa_pusat_raw = row.get(wa_pusat_col, "") if wa_pusat_col else ""
            wa_pusat_pretty = clean_optional(wa_pusat_raw)

            c1, c2 = st.columns([2.5, 1.2])
            with c1:
                st.markdown(f"### {prov or '-'} / {kab or '-'}")
                if posko:
                    st.markdown(f"**Posko:** {posko}")
                if kebutuhan:
                    st.markdown(f"**Kebutuhan mendesak:** {kebutuhan}")
                if dukungan:
                    st.markdown(f"**Dukungan dari jaringan:** {dukungan}")

                if update_cols_desc:
                    st.markdown("**🕒 Timeline Update (terbaru → lama):**")
                    has_update = False
                    for col in update_cols_desc:
                        val = clean_optional(row.get(col, ""))
                        if val:
                            has_update = True
                            st.markdown(f"- **{col}** – {val}")
                    if not has_update:
                        st.markdown("_Belum ada update tertulis._")

            with c2:
                st.markdown("**PIC Lapangan**")
                if korlap_name or wa_korlap_pretty:
                    st.markdown(
                        f"{korlap_name or '-'}"
                        + (f"<br/>📱 {wa_korlap_pretty}" if wa_korlap_pretty else ""),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown("-")

                if wa_korlap_norm:
                    msg = f"Halo {korlap_name or ''}, saya melihat update posko {kab or prov}."
                    wa_link = f"https://wa.me/{wa_korlap_norm}?text={quote(msg)}"
                    st.markdown(f"[🔗 Chat WA]({wa_link})")

                st.markdown("---")
                st.markdown("**PIC Pusat**")
                if pusat_name or wa_pusat_pretty:
                    st.markdown(
                        f"{pusat_name or '-'}"
                        + (f"<br/>📱 {wa_pusat_pretty}" if wa_pusat_pretty else ""),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown("-")

                st.markdown("---")
                if gmap:
                    st.markdown(f"[📍 Buka Google Maps]({gmap})")
                if lat and lon and not pd.isna(lat) and not pd.isna(lon):
                    st.caption(f"Lat: {lat:.4f}, Lon: {lon:.4f}")
                if foto:
                    st.markdown(f"[🖼 Dokumentasi]({foto})")

            st.markdown("---")

    # === FILTER GLOBAL DI ATAS ===
    st.markdown("### 🔍 Filter")

    col_f1, col_f2, col_f3 = st.columns([1.2, 1.2, 1.4])

    with col_f1:
        provinsi_list = sorted(
            [p for p in df["Provinsi"].dropna().astype(str).unique() if p and p.lower() != "nan"]
        )
        prov_sel = st.multiselect("Provinsi", provinsi_list, default=provinsi_list)

    with col_f2:
        name_col = "Nama Relawan Koordinator Lapangan"
        if name_col in df.columns:
            name_list = (
                df[name_col]
                .dropna()
                .astype(str)
                .map(str.strip)
            )
            uniq_names = sorted({n for n in name_list if n and n.lower() != "nan"})
        else:
            uniq_names = []
        name_sel = st.multiselect("Nama PIC Lapangan", uniq_names)

    with col_f3:
        search_text = st.text_input(
            "Cari kata kunci (posko, kabupaten, update, kebutuhan)"
        )

    filtered = df.copy()
    if prov_sel:
        filtered = filtered[filtered["Provinsi"].isin(prov_sel)]
    if name_sel and "Nama Relawan Koordinator Lapangan" in filtered.columns:
        filtered = filtered[
            filtered["Nama Relawan Koordinator Lapangan"].isin(name_sel)
        ]

    if search_text:
        pattern = search_text.lower()

        def matches(row):
            cols_to_search = [
                "Kabupaten",
                "Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia",
                "List Kebutuhan Mendesak",
                "Last Update (full)",
            ]
            for c in cols_to_search:
                if c in row.index:
                    val = str(row.get(c, "")).lower()
                    if pattern in val:
                        return True
            return False

        filtered = filtered[filtered.apply(matches, axis=1)]

    # === SORT CONTROL ===
    st.markdown("### ⚙️ Pengurutan")
    sort_option = st.radio(
        "Urutkan berdasarkan:",
        ("Entry terbaru (default)", "Update paling tinggi", "Urutan input"),
        index=0,  # default: Entry terbaru
        horizontal=True,
    )

    if sort_option.startswith("Entry terbaru"):
        # Entry / baris terbaru, pakai No terbesar kalau ada
        if "No" in filtered.columns:
            filtered = filtered.sort_values("No", ascending=False)
        else:
            filtered = filtered.sort_values("__row_index", ascending=False)

    elif sort_option == "Update paling tinggi":
        # Lokasi dengan level update tertinggi (Update_Level) di atas
        filtered = filtered.sort_values(
            ["Update_Level", "__row_index"], ascending=[False, False]
        )

    elif sort_option == "Urutan input":
        # Kembali ke urutan input asli
        filtered = filtered.sort_values("__row_index", ascending=True)

    # === TIMELINE ORDER TOGGLE (UNTUK CARD) ===
    st.markdown("### 🕒 Pengaturan Timeline (Card)")
    timeline_oldest_first = st.checkbox(
        "Timeline di kartu: tampilkan dari update paling lama dulu (Update 1 → ...)",
        value=False,  # default = terbaru → lama
    )
    # kalau checkbox OFF → latest_first=True → besar → kecil
    timeline_latest_first = not timeline_oldest_first

    st.markdown(
        f"**Lokasi terfilter:** {len(filtered)} dari total {len(df)} lokasi."
    )

    if filtered.empty:
        st.info("Tidak ada lokasi yang cocok dengan filter. Silakan atur ulang filter di atas.")
        return

    # === CARD DASHBOARD ===
    st.markdown("### 🧩 Daftar Lokasi (Card View)")

    selected_indices = []

    for _, row in filtered.iterrows():
        idx = int(row["__row_index"])

        prov = clean_optional(row.get("Provinsi", ""))
        kab = clean_optional(row.get("Kabupaten", ""))
        posko = clean_optional(
            row.get("Posko & Penjelasan Jumlah Orang, Berdasarkan Jenis Kelamin dan Usia", "")
        )
        kebutuhan = clean_optional(row.get("List Kebutuhan Mendesak", ""))
        dukungan = clean_optional(
            row.get("Dukungan yang bisa di offer ke sesama jaringan", "")
        )
        gmap = clean_optional(row.get("Link Google Map", ""))
        lat = row.get("lat", None)
        lon = row.get("lon", None)

        korlap_name = clean_optional(
            row.get("Nama Relawan Koordinator Lapangan", "")
        )
        wa_korlap_raw = row.get(wa_korlap_col, "") if wa_korlap_col else ""
        wa_korlap_norm = normalize_phone(wa_korlap_raw)
        wa_korlap_pretty = clean_optional(wa_korlap_raw)

        pusat_name = clean_optional(
            row.get("Nama Relawan Koordinator Pusat - Posisi Standby", "")
        )
        wa_pusat_raw = row.get(wa_pusat_col, "") if wa_pusat_col else ""
        wa_pusat_pretty = clean_optional(wa_pusat_raw)

        # timeline update untuk CARD (ikut toggle)
        timeline_items = []
        if update_cols_desc:
            cols_for_timeline = update_cols_desc if timeline_latest_first else update_cols_asc
            for col in cols_for_timeline:
                val = clean_optional(row.get(col, ""))
                if val:
                    timeline_items.append(f"**{col}** – {val}")

        with st.container():
            st.markdown("---")
            c1, c2 = st.columns([3, 1.1])

            with c1:
                st.markdown(
                    f"#### {prov or '-'} / {kab or '-'}"
                )
                if posko:
                    st.markdown(f"**Posko:** {posko}")
                if kebutuhan:
                    st.markdown(f"**Kebutuhan mendesak:** {kebutuhan}")
                if dukungan:
                    st.markdown(f"**Dukungan dari jaringan:** {dukungan}")

                if timeline_items:
                    if timeline_latest_first:
                        st.markdown("**🕒 Timeline Update (terbaru → lama):**")
                    else:
                        st.markdown("**🕒 Timeline Update (lama → terbaru):**")
                    for item in timeline_items:
                        st.markdown(f"- {item}")
                else:
                    st.markdown("_Belum ada update tertulis._")

            with c2:
                st.markdown("**PIC Lapangan**")
                if korlap_name or wa_korlap_pretty:
                    st.markdown(
                        f"{korlap_name or '-'}"
                        + (f"<br/>📱 {wa_korlap_pretty}" if wa_korlap_pretty else ""),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown("-")

                if wa_korlap_norm:
                    msg = f"Halo {korlap_name or ''}, saya melihat update posko {kab or prov}."
                    wa_link = f"https://wa.me/{wa_korlap_norm}?text={quote(msg)}"
                    st.markdown(f"[🔗 Chat WA]({wa_link})")

                st.markdown("---")
                st.markdown("**PIC Pusat**")
                if pusat_name or wa_pusat_pretty:
                    st.markdown(
                        f"{pusat_name or '-'}"
                        + (f"<br/>📱 {wa_pusat_pretty}" if wa_pusat_pretty else ""),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown("-")

                st.markdown("---")
                if gmap:
                    st.markdown(f"[📍 Buka Google Maps]({gmap})")
                if lat and lon and not pd.isna(lat) and not pd.isna(lon):
                    st.caption(f"Lat: {lat:.4f}, Lon: {lon:.4f}")

                detail_url = f"?row={idx}"
                st.markdown(f"[🔎 Lihat detail lengkap]({detail_url})")

                # baris: checkbox + tombol scroll ke WA
                cb_col, btn_col = st.columns([1, 1.4])

                with cb_col:
                    selected = st.checkbox("Pilih lokasi ini", key=f"select_{idx}")
                    if selected:
                        selected_indices.append(idx)

                with btn_col:
                    st.markdown(
                        """
                        <a href="#wa-section">
                            <button style="
                                padding:4px 10px;
                                border-radius:6px;
                                border:1px solid #999;
                                background-color:white;
                                cursor:pointer;
                                font-size:0.9rem;
                            ">
                                ⬇️ Ke bagian WhatsApp
                            </button>
                        </a>
                        """,
                        unsafe_allow_html=True,
                    )

    # === WHATSAPP GABUNGAN UNTUK CARD TERPILIH ===
    st.markdown("---")

    # anchor untuk scroll
    st.markdown('<a id="wa-section"></a>', unsafe_allow_html=True)

    st.markdown("### ✅ Lokasi terpilih – Pesan WhatsApp gabungan")

    if selected_indices:
        st.caption(f"Jumlah lokasi terpilih: {len(selected_indices)}")

        selected_rows = [df[df["__row_index"] == i].iloc[0] for i in selected_indices]

        bodies = []
        for i, row in enumerate(selected_rows, start=1):
            body = build_whatsapp_body_for_row(row, update_cols_desc, wa_korlap_col)
            prov = clean_optional(row.get("Provinsi", ""))
            kab = clean_optional(row.get("Kabupaten", ""))
            header = f"*Lokasi {i} – {prov or '-'} / {kab or '-'}*"
            bodies.append(header + "\n" + body)

        combined_body = "\n\n——————————\n\n".join(bodies)

        # tombol copy ke clipboard (HTML + JS sederhana)
        js_body = (
            combined_body.replace("\\", "\\\\")
            .replace("`", "\\`")
        )
        st.markdown(
            f"""
            <button onclick="navigator.clipboard.writeText(`{js_body}`)"
                    style="padding:6px 12px;border-radius:6px;border:1px solid #999;cursor:pointer;">
                Copy ke clipboard
            </button>
            """,
            unsafe_allow_html=True,
        )

        st.text_area(
            "Body WhatsApp (gabungan, siap di-paste)",
            value=combined_body,
            height=260,
        )

        wa_link_multi = f"https://wa.me/?text={quote(combined_body)}"
        st.markdown(
            f"[🔗 Buka WhatsApp dengan pesan ini]({wa_link_multi})",
            help="Klik untuk membuka WhatsApp Web / aplikasi dengan pesan gabungan.",
        )
    else:
        st.caption("Belum ada card yang dipilih. Centang 'Pilih lokasi ini' di kartu yang relevan.")

    # === MAP DI BAWAH KARTU ===
    st.markdown("---")
    st.markdown("### 🗺️ Peta Lokasi Terfilter")

    if "lat" in filtered.columns and "lon" in filtered.columns:
        map_data = filtered.dropna(subset=["lat", "lon"]).copy()
        if not map_data.empty:
            map_data["Provinsi_display"] = map_data["Provinsi"].astype(str)
            map_data["Kabupaten_display"] = map_data["Kabupaten"].astype(str)
            korlap_name_col = "Nama Relawan Koordinator Lapangan"
            map_data["Korlap_display"] = map_data.get(korlap_name_col, "").astype(str)
            if wa_korlap_col:
                map_data["WA_Korlap_display"] = map_data[wa_korlap_col].astype(str)
            else:
                map_data["WA_Korlap_display"] = ""

            tooltip = {
                "html": "<b>{Provinsi_display}</b> – {Kabupaten_display}<br/>"
                        "Korlap: {Korlap_display}<br/>"
                        "WA: {WA_Korlap_display}",
                "style": {"backgroundColor": "white", "color": "black"},
            }

            layer = pdk.Layer(
                "ScatterplotLayer",
                data=map_data,
                get_position="[lon, lat]",
                get_radius=4000,
                get_fill_color=[255, 0, 0, 160],
                pickable=True,
            )

            view_state = pdk.ViewState(
                latitude=map_data["lat"].mean(),
                longitude=map_data["lon"].mean(),
                zoom=6,
            )

            deck = pdk.Deck(
                layers=[layer],
                initial_view_state=view_state,
                tooltip=tooltip,
            )
            st.pydeck_chart(deck)
        else:
            st.caption("Tidak ada koordinat lat/long yang bisa ditampilkan.")
    else:
        st.caption("Kolom koordinat (lat/long) belum tersedia di data.")


if __name__ == "__main__":
    main()
