import streamlit as st
import os
import json
import zipfile
import tempfile
import time
import re
import google.generativeai as genai
from pdf2image import convert_from_path
from PIL import Image
from difflib import SequenceMatcher
from io import BytesIO

# ----------------------------- Yardımcı Fonksiyonlar -----------------------------

def benzerlik_orani(a, b):
    return SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()

def dosya_adi_duzenle(isim):
    yasakli_karakterler = '<>:"/\\|?*'
    for harf in yasakli_karakterler:
        isim = isim.replace(harf, '')
    return isim.strip().title()

def create_zip(source_dir, output_zip):
    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, source_dir)
                zipf.write(file_path, arcname)

def json_ayikla(text):
    text = text.replace("```json", "").replace("```", "").strip()
    eslesme = re.search(r'\{.*\}', text, re.DOTALL)
    if eslesme:
        text = eslesme.group(0)
    return json.loads(text)

def ai1_isim_alani_bul(model, pil_image):
    prompt = """Bu bir İş Sağlığı ve Güvenliği sınav/eğitim formudur.
Formun üst kısmında yer alan "ADI SOYADI:" etiketinin HEMEN YANINDAKİ el yazısı ile yazılmış
isim ve soyisimin bulunduğu alanın konumunu bul.

Yanıtı SADECE aşağıdaki JSON formatında ver, başka hiçbir açıklama ekleme:
{"box_2d": [y_min, x_min, y_max, x_max], "bulundu": true}

Koordinatlar 0-1000 arası normalize edilmiş olmalı (görselin sol üstü 0,0 - sağ altı 1000,1000).
Eğer alanı bulamazsan {"bulundu": false} yaz."""

    try:
        response = model.generate_content([prompt, pil_image])
        veri = json_ayikla(response.text)
        if not veri.get("bulundu", False):
            return None
        y_min, x_min, y_max, x_max = veri["box_2d"]
        w, h = pil_image.size

        pad_x = (x_max - x_min) * 0.15
        pad_y = (y_max - y_min) * 0.5

        left = max(0, int((x_min - pad_x) / 1000 * w))
        right = min(w, int((x_max + pad_x) / 1000 * w))
        top = max(0, int((y_min - pad_y) / 1000 * h))
        bottom = min(h, int((y_max + pad_y) / 1000 * h))

        if right <= left or bottom <= top:
            return None

        kirpilmis = pil_image.crop((left, top, right, bottom))

        hedef_genislik = 900
        oran = hedef_genislik / kirpilmis.width
        yeni_boyut = (hedef_genislik, max(1, int(kirpilmis.height * oran)))
        kirpilmis = kirpilmis.resize(yeni_boyut, Image.LANCZOS)

        return kirpilmis
    except Exception:
        return None

def ai2_isim_oku(model, kirpilmis_gorsel):
    prompt = """Bu görselde el yazısı ile yazılmış bir AD SOYAD bulunuyor.
Sadece bu ismi oku ve yaz. Başka hiçbir açıklama, etiket veya işaret ekleme.
Emin değilsen en yakın tahminini yaz. Tamamen okunamıyorsa "Bilinmeyen_Kisi" yaz."""

    try:
        response = model.generate_content([prompt, kirpilmis_gorsel])
        isim = response.text.strip().strip('"').strip("'")
        return dosya_adi_duzenle(isim) if isim else "Bilinmeyen_Kisi"
    except Exception:
        return "Bilinmeyen_Kisi"

def ai_tc_oku(model, pil_image):
    prompt = """Bu bir İş Sağlığı ve Güvenliği formudur. "T.C. KİMLİK NO:" yazısının yanındaki
11 haneli rakamı bul. Yanıtını SADECE şu JSON formatında ver:
{"tc": "12345678901"}
Okunmuyorsa {"tc": "BilinmeyenTC"} yaz."""
    try:
        response = model.generate_content([prompt, pil_image])
        veri = json_ayikla(response.text)
        tc = str(veri.get("tc", "BilinmeyenTC"))
        return dosya_adi_duzenle(tc) if tc else "BilinmeyenTC"
    except Exception:
        return "BilinmeyenTC"

def pil_to_bytes(img):
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ----------------------------- Streamlit Arayüzü -----------------------------

st.set_page_config(page_title="İSG Belge Ayrıştırıcı", page_icon="📄", layout="centered")

for key, default in [
    ("asama", "yukleme"),
    ("bloklar", []),
    ("zip_data", None),
    ("islem_mesaji", ""),
    ("temp_dir", None),
    ("sinav_sayfa", 2),
    ("talimat_sayfa", 4),
]:
    if key not in st.session_state:
        st.session_state[key] = default

st.title("📄 İSG Belge Ayrıştırıcı")
st.caption("Tarama → Bloklara Ayırma → AI-1 Kırpma → AI-2 Okuma → Kullanıcı Onayı → Klasörleme")

# --------------------------------------------------------------------------
# AŞAMA 1: PDF Yükleme ve İşleme Ayarları
# --------------------------------------------------------------------------
if st.session_state.asama == "yukleme":

    api_key = st.text_input("Gemini API Anahtarınızı Girin:", type="password", autocomplete="current-password")

    st.markdown("### ⚙️ Belge Dizilimi (Blok Ayarları)")
    col1, col2 = st.columns(2)
    with col1:
        sinav_sayfa = st.number_input("Sınav Sayfa Sayısı:", min_value=0, value=st.session_state.sinav_sayfa, step=1)
    with col2:
        talimat_sayfa = st.number_input("Talimat Sayfa Sayısı:", min_value=0, value=st.session_state.talimat_sayfa, step=1)

    blok_boyutu = sinav_sayfa + talimat_sayfa

    uploaded_file = st.file_uploader("Lütfen tarama yapılmış PDF dosyasını yükleyin", type="pdf")

    if st.button("1️⃣ PDF'i İşle ve İsimleri Oku", type="primary"):
        if not api_key:
            st.error("Lütfen bir Gemini API anahtarı girin!")
        elif not uploaded_file:
            st.error("Lütfen işlenecek PDF dosyasını yükleyin!")
        elif blok_boyutu == 0:
            st.error("Toplam sayfa sayısı 0 olamaz.")
        else:
            try:
                genai.configure(api_key=api_key)
                aktif_modeller = [
                    m.name for m in genai.list_models()
                    if 'generateContent' in m.supported_generation_methods
                ]
                oncelikli_modeller = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash']
                uygun_model = None
                for oncelik in oncelikli_modeller:
                    for ad in aktif_modeller:
                        if oncelik in ad and 'preview' not in ad and 'lite' not in ad:
                            uygun_model = ad
                            break
                    if uygun_model:
                        break
                if not uygun_model:
                    for ad in aktif_modeller:
                        if 'flash' in ad:
                            uygun_model = ad
                            break
                if not uygun_model:
                    st.error(f"Görsel işleyebilen model bulunamadı! Mevcutlar: {aktif_modeller}")
                    st.stop()

                model = genai.GenerativeModel(uygun_model)
                st.toast(f"Başarılı: {uygun_model} modeline bağlanıldı!", icon="✅")
            except Exception as e:
                st.error(f"API Yapılandırma Hatası: {e}")
                st.stop()

            temp_dir = tempfile.mkdtemp()
            st.session_state.temp_dir = temp_dir
            st.session_state.sinav_sayfa = sinav_sayfa
            st.session_state.talimat_sayfa = talimat_sayfa

            pdf_path = os.path.join(temp_dir, "yuklenen_dosya.pdf")
            orijinal_klasor = os.path.join(temp_dir, "Orijinal_Sayfalar")
            os.makedirs(orijinal_klasor, exist_ok=True)

            with open(pdf_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            st.info("PDF okunuyor, 300 DPI ve LZW sıkıştırma ile kalitesi bozulmadan kaydediliyor...")
            try:
                temp_images = convert_from_path(pdf_path, dpi=300)
                sayfa_yollari = []
                for idx, img in enumerate(temp_images):
                    sayfa_yolu = os.path.join(orijinal_klasor, f"page_{idx + 1}.tiff")
                    img.save(sayfa_yolu, format="TIFF", compression="tiff_lzw")
                    sayfa_yollari.append(sayfa_yolu)
            except Exception as e:
                st.error("PDF parçalanamadı: " + str(e))
                st.stop()

            toplam_sayfa = len(sayfa_yollari)
            bloklar = [sayfa_yollari[i:i + blok_boyutu] for i in range(0, toplam_sayfa, blok_boyutu)]

            progress_bar = st.progress(0)
            status_text = st.empty()

            blok_sonuclari = []

            for blok_no, blok_sayfalari in enumerate(bloklar):
                status_text.text(f"Blok {blok_no + 1}/{len(bloklar)} işleniyor (isim aranıyor)...")

                blok_kayit = {
                    "blok_no": blok_no,
                    "sayfa_yollari": blok_sayfalari,
                    "onerilen_isim": "Bilinmeyen_Kisi",
                    "onerilen_tc": "BilinmeyenTC",
                    "kirpilar": [],
                    "onaylandi": False,
                }

                incelenecek_sayfalar = blok_sayfalari[:max(sinav_sayfa, 1)]

                bulunan_isimler = []
                tc_bulundu = "BilinmeyenTC"

                for s_idx, sayfa_yolu in enumerate(incelenecek_sayfalar):
                    with Image.open(sayfa_yolu) as img:
                        tam_gorsel = img.convert('RGB').copy()

                    kirpilmis = ai1_isim_alani_bul(model, tam_gorsel)

                    if kirpilmis is not None:
                        okunan_isim = ai2_isim_oku(model, kirpilmis)
                        blok_kayit["kirpilar"].append(
                            (s_idx + 1, pil_to_bytes(kirpilmis), okunan_isim)
                        )
                        if okunan_isim and okunan_isim != "Bilinmeyen_Kisi":
                            bulunan_isimler.append(okunan_isim)

                    if tc_bulundu == "BilinmeyenTC":
                        tc_bulundu = ai_tc_oku(model, tam_gorsel)

                    time.sleep(2)

                if bulunan_isimler:
                    if len(bulunan_isimler) > 1 and benzerlik_orani(bulunan_isimler[0], bulunan_isimler[1]) < 0.6:
                        onerilen = max(bulunan_isimler, key=len)
                    else:
                        onerilen = bulunan_isimler[0]
                    blok_kayit["onerilen_isim"] = onerilen

                blok_kayit["onerilen_tc"] = tc_bulundu
                blok_sonuclari.append(blok_kayit)
                progress_bar.progress((blok_no + 1) / len(bloklar))

            st.session_state.bloklar = blok_sonuclari
            st.session_state.asama = "onay"
            status_text.text("Ön okuma tamamlandı. Lütfen isimleri kontrol edip onaylayın.")
            st.rerun()

# --------------------------------------------------------------------------
# AŞAMA 2: Kullanıcı Onayı
# --------------------------------------------------------------------------
elif st.session_state.asama == "onay":

    st.markdown("### ✅ Okunan İsimleri Kontrol Edin")
    st.write(
        "Her blok için AI-1'in kırptığı isim alanı ve AI-2'nin okuduğu isim aşağıda gösteriliyor. "
        "Yanlışsa düzelttikten sonra kutucuğu işaretleyin."
    )

    tumu_onayli = True

    for i, blok in enumerate(st.session_state.bloklar):
        with st.container(border=True):
            st.markdown(f"**Blok {blok['blok_no'] + 1}** — {len(blok['sayfa_yollari'])} sayfa")

            if blok["kirpilar"]:
                kol_sayisi = len(blok["kirpilar"])
                kolonlar = st.columns(kol_sayisi)
                for k, (sayfa_no, img_bytes, ai_isim) in enumerate(blok["kirpilar"]):
                    with kolonlar[k]:
                        st.image(img_bytes, caption=f"Sınav {sayfa_no} — İsim Alanı")
                        st.caption(f"AI Okuması: _{ai_isim}_")
            else:
                st.warning("İsim alanı otomatik bulunamadı, lütfen manuel giriniz.")

            col_a, col_b = st.columns(2)
            with col_a:
                yeni_isim = st.text_input(
                    "Kişi Adı Soyadı", value=blok["onerilen_isim"], key=f"isim_{i}"
                )
            with col_b:
                yeni_tc = st.text_input(
                    "T.C. Kimlik No", value=blok["onerilen_tc"], key=f"tc_{i}"
                )

            onay = st.checkbox("Bu ismi onaylıyorum", value=blok["onaylandi"], key=f"onay_{i}")

            st.session_state.bloklar[i]["onerilen_isim"] = yeni_isim
            st.session_state.bloklar[i]["onerilen_tc"] = yeni_tc
            st.session_state.bloklar[i]["onaylandi"] = onay

            if not onay:
                tumu_onayli = False

    st.divider()

    col_geri, col_ileri = st.columns(2)
    with col_geri:
        if st.button("↩️ Baştan Başla"):
            if st.session_state.temp_dir and os.path.exists(st.session_state.temp_dir):
                shutil.rmtree(st.session_state.temp_dir, ignore_errors=True)
            st.session_state.asama = "yukleme"
            st.session_state.bloklar = []
            st.session_state.temp_dir = None
            st.rerun()

    with col_ileri:
        buton_devre_disi = not tumu_onayli
        if st.button("2️⃣ Klasörleri Oluştur ve ZIP'le", type="primary", disabled=buton_devre_disi):
            temp_dir = st.session_state.temp_dir
            sinav_sayfa = st.session_state.sinav_sayfa
            talimat_sayfa = st.session_state.talimat_sayfa

            ayrilmis_klasor_yolu = os.path.join(temp_dir, "Ayrilmis_Dosyalar")
            zip_yolu = os.path.join(temp_dir, "ISG_Dosyalari.zip")
            os.makedirs(ayrilmis_klasor_yolu, exist_ok=True)

            toplam_sayfa = sum(len(b["sayfa_yollari"]) for b in st.session_state.bloklar)
            islenen_sayfa = 0
            progress_bar = st.progress(0)

            for blok in st.session_state.bloklar:
                kisi = dosya_adi_duzenle(blok["onerilen_isim"]) or "Bilinmeyen_Kisi"
                tc = dosya_adi_duzenle(blok["onerilen_tc"]) or "BilinmeyenTC"
                klasor_adi = f"{kisi}_{tc}"
                hedef_klasor = os.path.join(ayrilmis_klasor_yolu, klasor_adi)

                sayac = 1
                orijinal_klasor_adi = klasor_adi
                while os.path.exists(hedef_klasor):
                    klasor_adi = f"{orijinal_klasor_adi}_{sayac}"
                    hedef_klasor = os.path.join(ayrilmis_klasor_yolu, klasor_adi)
                    sayac += 1
                os.makedirs(hedef_klasor, exist_ok=True)

                for idx, sayfa_yolu in enumerate(blok["sayfa_yollari"]):
                    islenen_sayfa += 1
                    if not os.path.exists(sayfa_yolu):
                        continue

                    if idx < sinav_sayfa:
                        dosya_adi = f"Sinav_{idx + 1}.tiff"
                    elif idx < (sinav_sayfa + talimat_sayfa):
                        dosya_adi = f"Talimat_{idx - sinav_sayfa + 1}.tiff"
                    else:
                        dosya_adi = f"Ekstra_Belge_{idx + 1}.tiff"

                    hedef_yol = os.path.join(hedef_klasor, dosya_adi)
                    sayac2 = 1
                    orijinal_isim = dosya_adi.replace(".tiff", "")
                    while os.path.exists(hedef_yol):
                        hedef_yol = os.path.join(hedef_klasor, f"{orijinal_isim}_{sayac2}.tiff")
                        sayac2 += 1

                    shutil.move(sayfa_yolu, hedef_yol)
                    progress_bar.progress(min(islenen_sayfa / toplam_sayfa, 1.0))

            create_zip(ayrilmis_klasor_yolu, zip_yolu)
            with open(zip_yolu, "rb") as f:
                st.session_state.zip_data = f.read()

            st.session_state.islem_mesaji = f"{len(st.session_state.bloklar)} kişi için klasörleme tamamlandı."
            st.session_state.asama = "tamamlandi"

            shutil.rmtree(temp_dir, ignore_errors=True)
            st.session_state.temp_dir = None
            st.rerun()

        if buton_devre_disi:
            st.caption("⚠️ Devam etmek için tüm blokları onaylamalısınız.")

# --------------------------------------------------------------------------
# AŞAMA 3: Tamamlandı
# --------------------------------------------------------------------------
elif st.session_state.asama == "tamamlandi":
    st.success(st.session_state.islem_mesaji)
    st.download_button(
        label="📦 Hazırlanan Klasörleri İndir (ZIP)",
        data=st.session_state.zip_data,
        file_name="ISG_Ayrilmis_Dosyalar.zip",
        mime="application/zip",
        type="primary",
    )
    if st.button("🔄 Yeni Dosya İşle"):
        st.session_state.asama = "yukleme"
        st.session_state.bloklar = []
        st.session_state.zip_data = None
        st.rerun()
