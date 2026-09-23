import streamlit as st
import os
import json
import zipfile
import tempfile
import time
import shutil
import re

import numpy as np

from pdf2image import convert_from_path
from PIL import Image, ImageEnhance, ImageFilter

import google.generativeai as genai


# ============================================================
# AYARLAR
# ============================================================

DPI = 300

# Gemini'ye gönderilecek crop'u büyütme
CROP_SCALE = 3.0

# Gemini istekleri arasındaki bekleme
API_BEKLEME = 1.0


# ============================================================
# SAYFA AYARLARI
# ============================================================

st.set_page_config(
    page_title="İSG Belge Ayrıştırıcı",
    page_icon="📄",
    layout="wide"
)


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================

def dosya_adi_duzenle(isim):

    if not isim:
        return ""

    isim = str(isim)

    yasakli = '<>:"/\\|?*'

    for karakter in yasakli:
        isim = isim.replace(karakter, "")

    isim = isim.replace("\n", " ")
    isim = isim.replace("\r", " ")

    isim = " ".join(isim.split())

    return isim.strip()


def isim_normalize(isim):

    if not isim:
        return ""

    isim = str(isim).upper()

    ceviri = str.maketrans(
        "ÇĞİÖŞÜ",
        "CGIOSU"
    )

    isim = isim.translate(ceviri)

    isim = re.sub(
        r"[^A-Z0-9 ]",
        " ",
        isim
    )

    return " ".join(isim.split())


def isim_benzerligi(a, b):

    from difflib import SequenceMatcher

    a = isim_normalize(a)
    b = isim_normalize(b)

    if not a or not b:
        return 0

    return SequenceMatcher(
        None,
        a,
        b
    ).ratio()


def benzersiz_klasor_yolu(
    ana_klasor,
    klasor_adi
):

    temel = os.path.join(
        ana_klasor,
        klasor_adi
    )

    if not os.path.exists(temel):
        return temel

    sayac = 2

    while True:

        yeni = os.path.join(
            ana_klasor,
            f"{klasor_adi}_{sayac}"
        )

        if not os.path.exists(yeni):
            return yeni

        sayac += 1


def create_zip(
    source_dir,
    output_zip
):

    with zipfile.ZipFile(
        output_zip,
        "w",
        zipfile.ZIP_DEFLATED
    ) as zipf:

        for root, dirs, files in os.walk(
            source_dir
        ):

            for file in files:

                file_path = os.path.join(
                    root,
                    file
                )

                arcname = os.path.relpath(
                    file_path,
                    source_dir
                )

                zipf.write(
                    file_path,
                    arcname
                )


# ============================================================
# GÖRÜNTÜ İYİLEŞTİRME
# ============================================================

def goruntu_iyilestir(
    image
):

    if image.mode != "RGB":
        image = image.convert("RGB")

    yeni_w = int(
        image.width * CROP_SCALE
    )

    yeni_h = int(
        image.height * CROP_SCALE
    )

    image = image.resize(
        (
            yeni_w,
            yeni_h
        ),
        Image.Resampling.LANCZOS
    )

    image = ImageEnhance.Contrast(
        image
    ).enhance(1.5)

    image = ImageEnhance.Sharpness(
        image
    ).enhance(1.8)

    image = image.filter(
        ImageFilter.SHARPEN
    )

    return image


# ============================================================
# OTOMATİK İSİM ALANI
# ============================================================

def otomatik_crop_koordinatlari(
    width,
    height
):

    """
    Formun yapısına göre başlangıç crop'u.

    Koordinatlar yüzde olarak tutuluyor.

    Böylece 2479x3500 veya başka çözünürlüklerde
    çalışmaya devam eder.
    """

    # Başlangıç değerleri
    #
    # x1 = %4
    # y1 = %47
    # x2 = %80
    # y2 = %61

    x1 = int(width * 0.04)
    y1 = int(height * 0.47)

    x2 = int(width * 0.80)
    y2 = int(height * 0.61)

    return (
        x1,
        y1,
        x2,
        y2
    )


# ============================================================
# CROP OLUŞTUR
# ============================================================

def isim_crop_al(
    image,
    x1_oran,
    y1_oran,
    x2_oran,
    y2_oran
):

    width = image.width
    height = image.height

    x1 = int(
        width * x1_oran
    )

    y1 = int(
        height * y1_oran
    )

    x2 = int(
        width * x2_oran
    )

    y2 = int(
        height * y2_oran
    )

    x1 = max(
        0,
        min(x1, width)
    )

    x2 = max(
        0,
        min(x2, width)
    )

    y1 = max(
        0,
        min(y1, height)
    )

    y2 = max(
        0,
        min(y2, height)
    )

    if x2 <= x1 or y2 <= y1:
        return None

    crop = image.crop(
        (
            x1,
            y1,
            x2,
            y2
        )
    )

    return goruntu_iyilestir(
        crop
    )


# ============================================================
# GEMINI MODELİ BUL
# ============================================================

def gemini_model_bul():

    modeller = []

    for model in genai.list_models():

        if (
            "generateContent"
            in model.supported_generation_methods
        ):

            modeller.append(
                model.name
            )

    # Flash modelleri tercih et
    flashlar = [

        x for x in modeller

        if "flash" in x.lower()

    ]

    if flashlar:
        return flashlar[0]

    if modeller:
        return modeller[0]

    return None


# ============================================================
# GEMINI İLE İSİM OKUMA
# ============================================================

def gemini_ile_isim_oku(
    model,
    crop_list
):

    if not crop_list:

        return {
            "isim1": "",
            "isim2": "",
            "final_isim": "",
            "guven": "dusuk"
        }

    prompt = """

Bu görüntüler İş Sağlığı ve Güvenliği sınav
formundaki ADI SOYADI alanlarının kırpılmış
görüntüleridir.

Görevin yalnızca el yazısı ile yazılmış
kişinin ADI SOYADINI okumaktır.

ÇOK ÖNEMLİ:

- "ADI SOYADI" yazısını isim olarak alma.
- "T.C. KİMLİK NO" bilgisini alma.
- "GÖREVİ" bilgisini alma.
- İmza bilgisini alma.
- Sadece kişinin yazdığı gerçek ad ve soyadı oku.
- El yazısını dikkatlice incele.
- Harfleri mümkün olduğunca birebir çöz.
- Görseller aynı kişiye ait iki farklı sınav yüzü olabilir.
- İki görüntüdeki isimleri birlikte değerlendir.
- Bir görüntüde harf net değilse diğer görüntüyü referans al.
- Küçük yazım farklılıklarını aynı kişi olarak değerlendir.
- Emin değilsen uydurma isim oluşturma.

SADECE aşağıdaki JSON formatını döndür:

{
    "isim1": "birinci görüntüdeki isim",
    "isim2": "ikinci görüntüdeki isim",
    "final_isim": "en güvenilir ad soyad",
    "guven": "yuksek"
}

İkinci görüntü yoksa:

{
    "isim1": "okunan isim",
    "isim2": "",
    "final_isim": "okunan isim",
    "guven": "yuksek"
}

İsim kesinlikle okunamıyorsa:

{
    "isim1": "",
    "isim2": "",
    "final_isim": "Bilinmeyen_Kisi",
    "guven": "dusuk"
}

Başka hiçbir açıklama yazma.
"""

    try:

        icerik = [
            prompt
        ]

        for crop in crop_list:
            icerik.append(crop)

        response = model.generate_content(
            icerik
        )

        cevap = response.text

        cevap = cevap.replace(
            "```json",
            ""
        )

        cevap = cevap.replace(
            "```",
            ""
        )

        cevap = cevap.strip()

        veri = json.loads(
            cevap
        )

        isim1 = dosya_adi_duzenle(
            veri.get(
                "isim1",
                ""
            )
        )

        isim2 = dosya_adi_duzenle(
            veri.get(
                "isim2",
                ""
            )
        )

        final_isim = dosya_adi_duzenle(
            veri.get(
                "final_isim",
                ""
            )
        )

        guven = str(
            veri.get(
                "guven",
                "dusuk"
            )
        ).lower()

        # ----------------------------------------------------
        # Gemini'nin sonucu güvenilir değilse
        # iki isim arasındaki benzerliğe bak
        # ----------------------------------------------------

        if (
            not final_isim
            or
            final_isim.lower()
            == "bilinmeyen_kisi"
        ):

            if isim1 and isim2:

                oran = isim_benzerligi(
                    isim1,
                    isim2
                )

                if oran >= 0.70:

                    final_isim = isim1
                    guven = "orta"

            elif isim1:

                final_isim = isim1

            elif isim2:

                final_isim = isim2

        if (
            not final_isim
            or
            len(final_isim) < 4
        ):

            final_isim = "Bilinmeyen_Kisi"
            guven = "dusuk"

        return {

            "isim1": isim1,
            "isim2": isim2,
            "final_isim": final_isim,
            "guven": guven

        }

    except Exception as e:

        return {

            "isim1": "",
            "isim2": "",
            "final_isim": "Bilinmeyen_Kisi",
            "guven": "dusuk"

        }


# ============================================================
# SESSION STATE
# ============================================================

if "zip_data" not in st.session_state:
    st.session_state.zip_data = None

if "pdf_pages" not in st.session_state:
    st.session_state.pdf_pages = None

if "preview_image" not in st.session_state:
    st.session_state.preview_image = None


# ============================================================
# BAŞLIK
# ============================================================

st.title(
    "📄 İSG Belge Tarama ve Klasörleme"
)

st.write(
    "Belgeler kişi başına 6'şarlı ayrılır. "
    "Sınav sayfalarındaki isim alanı kırpılır ve "
    "Gemini yalnızca bu alanı okuyarak klasör adını oluşturur."
)


# ============================================================
# API
# ============================================================

api_key = st.text_input(
    "Gemini API Anahtarı",
    type="password"
)


# ============================================================
# BELGE DİZİLİMİ
# ============================================================

st.subheader(
    "📑 Belge Yapısı"
)

col1, col2, col3 = st.columns(3)

with col1:

    sinav_sayfa = st.number_input(
        "Sınav sayısı",
        min_value=1,
        value=2,
        step=1
    )

with col2:

    talimat_sayfa = st.number_input(
        "Diğer belge sayısı",
        min_value=0,
        value=4,
        step=1
    )

with col3:

    blok_boyutu = (
        sinav_sayfa
        +
        talimat_sayfa
    )

    st.metric(
        "Kişi başına",
        f"{blok_boyutu} sayfa"
    )


# ============================================================
# PDF
# ============================================================

uploaded_file = st.file_uploader(
    "Taranmış PDF'yi yükleyin",
    type=["pdf"]
)


# ============================================================
# PDF ÖNİZLEME
# ============================================================

if uploaded_file:

    if st.session_state.pdf_pages is None:

        with st.spinner(
            "PDF önizleme için hazırlanıyor..."
        ):

            preview_temp = tempfile.mkdtemp()

            preview_pdf = os.path.join(
                preview_temp,
                "preview.pdf"
            )

            with open(
                preview_pdf,
                "wb"
            ) as f:

                f.write(
                    uploaded_file.getbuffer()
                )

            try:

                pages = convert_from_path(
                    preview_pdf,
                    dpi=120,
                    first_page=1,
                    last_page=2
                )

                st.session_state.pdf_pages = pages

            except Exception as e:

                st.error(
                    f"PDF okunamadı: {e}"
                )

    if st.session_state.pdf_pages:

        st.subheader(
            "🔍 İsim Alanı Önizleme"
        )

        st.info(
            "Aşağıdaki görüntü Gemini'ye gönderilecek "
            "isim bölgesidir. Yanlış alan seçildiyse "
            "X/Y ve genişlik ayarlarını değiştirebilirsin."
        )

        # ----------------------------------------------------
        # Ayarlar
        # ----------------------------------------------------

        st.markdown(
            "### Crop Ayarları"
        )

        c1, c2, c3, c4 = st.columns(4)

        with c1:

            x1 = st.slider(
                "Sol (%)",
                0,
                50,
                4,
                1
            ) / 100

        with c2:

            y1 = st.slider(
                "Üst (%)",
                0,
                80,
                47,
                1
            ) / 100

        with c3:

            x2 = st.slider(
                "Sağ (%)",
                30,
                100,
                80,
                1
            ) / 100

        with c4:

            y2 = st.slider(
                "Alt (%)",
                20,
                90,
                61,
                1
            ) / 100


        # ----------------------------------------------------
        # İlk sınav sayfası
        # ----------------------------------------------------

        ilk_sayfa = st.session_state.pdf_pages[0]

        # Küçük önizleme
        st.markdown(
            "#### 1️⃣ Taranan sınav sayfası"
        )

        st.image(
            ilk_sayfa,
            width=700
        )


        # ----------------------------------------------------
        # Crop
        # ----------------------------------------------------

        crop = isim_crop_al(

            ilk_sayfa,

            x1,
            y1,
            x2,
            y2

        )


        if crop:

            st.markdown(
                "#### 2️⃣ Gemini'ye gönderilecek isim alanı"
            )

            st.image(
                crop,
                width=900
            )

            st.success(
                "Eğer burada ADI SOYADI ve kişinin "
                "el yazısı net görünüyorsa crop hazır."
            )

        else:

            st.error(
                "Crop oluşturulamadı."
            )


# ============================================================
# İŞLEM BAŞLAT
# ============================================================

baslat = st.button(
    "🚀 Ayrıştırmayı Başlat",
    type="primary",
    use_container_width=True
)


if baslat:

    if not api_key:

        st.error(
            "Gemini API anahtarını gir."
        )

        st.stop()

    if not uploaded_file:

        st.error(
            "PDF yükle."
        )

        st.stop()

    if blok_boyutu <= 0:

        st.error(
            "Kişi başına sayfa sayısı 0 olamaz."
        )

        st.stop()


    # ========================================================
    # GEMINI
    # ========================================================

    try:

        genai.configure(
            api_key=api_key
        )

        model_adi = gemini_model_bul()

        if not model_adi:

            st.error(
                "Kullanılabilir Gemini modeli bulunamadı."
            )

            st.stop()

        model = genai.GenerativeModel(
            model_adi
        )

        st.success(
            f"Gemini hazır: {model_adi}"
        )

    except Exception as e:

        st.error(
            f"Gemini bağlantı hatası: {e}"
        )

        st.stop()


    # ========================================================
    # GEÇİCİ DİZİNLER
    # ========================================================

    with tempfile.TemporaryDirectory() as temp_dir:

        pdf_path = os.path.join(
            temp_dir,
            "dosya.pdf"
        )

        orijinal_klasor = os.path.join(
            temp_dir,
            "Orijinal"
        )

        ayrilmis_klasor = os.path.join(
            temp_dir,
            "Ayrilmis_Dosyalar"
        )

        zip_yolu = os.path.join(
            temp_dir,
            "ISG_Ayrilmis_Dosyalar.zip"
        )

        os.makedirs(
            orijinal_klasor,
            exist_ok=True
        )

        os.makedirs(
            ayrilmis_klasor,
            exist_ok=True
        )

        with open(
            pdf_path,
            "wb"
        ) as f:

            f.write(
                uploaded_file.getbuffer()
            )


        # ====================================================
        # PDF -> TIFF
        # ====================================================

        st.info(
            "PDF 300 DPI olarak işleniyor..."
        )

        try:

            sayfa_yollari = convert_from_path(

                pdf_path,

                dpi=DPI,

                output_folder=orijinal_klasor,

                fmt="tiff",

                paths_only=True

            )

        except Exception as e:

            st.error(
                f"PDF sayfalara ayrılamadı: {e}"
            )

            st.stop()


        toplam_sayfa = len(
            sayfa_yollari
        )


        if toplam_sayfa == 0:

            st.error(
                "PDF'de sayfa bulunamadı."
            )

            st.stop()


        # ====================================================
        # 6'ŞARLI BLOKLAR
        # ====================================================

        bloklar = [

            sayfa_yollari[i:i + blok_boyutu]

            for i in range(
                0,
                toplam_sayfa,
                blok_boyutu
            )

        ]


        toplam_blok = len(
            bloklar
        )


        st.info(
            f"{toplam_sayfa} sayfa → "
            f"{toplam_blok} kişi bloğu"
        )


        progress = st.progress(0)

        durum = st.empty()

        kontrol_sayisi = 0


        # ====================================================
        # KİŞİLER
        # ====================================================

        for blok_no, blok in enumerate(
            bloklar
        ):

            durum.text(
                f"📦 {blok_no + 1}/{toplam_blok} "
                f"numaralı kişi hazırlanıyor..."
            )


            gecici_klasor = os.path.join(

                ayrilmis_klasor,

                f"GEÇİCİ_{blok_no + 1}"

            )

            os.makedirs(
                gecici_klasor,
                exist_ok=True
            )


            sinav_yollari = []


            # =================================================
            # SAYFALARI KLASÖRE TAŞI
            # =================================================

            for sayfa_index, sayfa in enumerate(
                blok
            ):

                if sayfa_index < sinav_sayfa:

                    yeni_ad = (
                        f"Sinav_{sayfa_index + 1}.tiff"
                    )

                    sinav_yollari.append(
                        os.path.join(
                            gecici_klasor,
                            yeni_ad
                        )
                    )

                else:

                    yeni_ad = (

                        f"Belge_"
                        f"{sayfa_index - sinav_sayfa + 1}.tiff"

                    )


                hedef = os.path.join(

                    gecici_klasor,

                    yeni_ad

                )


                shutil.move(
                    sayfa,
                    hedef
                )


            # =================================================
            # SINAV GÖRSELLERİNİ HAZIRLA
            # =================================================

            crop_list = []


            for sinav_yolu in sinav_yollari:

                try:

                    image = Image.open(
                        sinav_yolu
                    ).convert(
                        "RGB"
                    )

                    crop = isim_crop_al(

                        image,

                        x1,
                        y1,
                        x2,
                        y2

                    )

                    if crop:

                        crop_list.append(
                            crop
                        )

                except Exception:
                    pass


            # =================================================
            # GEMINI
            # =================================================

            durum.text(

                f"🤖 {blok_no + 1}/{toplam_blok} "
                f"→ isim okunuyor..."

            )


            sonuc = gemini_ile_isim_oku(

                model,

                crop_list

            )


            isim1 = sonuc["isim1"]

            isim2 = sonuc["isim2"]

            final_isim = sonuc["final_isim"]

            guven = sonuc["guven"]


            # =================================================
            # KLASÖR ADI
            # =================================================

            if (

                not final_isim

                or

                final_isim
                ==
                "Bilinmeyen_Kisi"

            ):

                kontrol_sayisi += 1

                klasor_adi = (

                    f"KONTROL_GEREKLI_"
                    f"{blok_no + 1:03d}"

                )

            else:

                klasor_adi = dosya_adi_duzenle(
                    final_isim
                )


            hedef_klasor = benzersiz_klasor_yolu(

                ayrilmis_klasor,

                klasor_adi

            )


            os.rename(

                gecici_klasor,

                hedef_klasor

            )


            # =================================================
            # SONUÇ
            # =================================================

            if (
                final_isim
                !=
                "Bilinmeyen_Kisi"
            ):

                durum.text(

                    f"✅ {blok_no + 1}/{toplam_blok} "
                    f"→ {final_isim} "
                    f"({guven})"

                )

            else:

                durum.text(

                    f"⚠️ {blok_no + 1}/{toplam_blok} "
                    f"→ isim okunamadı"

                )


            progress.progress(

                (blok_no + 1)
                /
                toplam_blok

            )


            time.sleep(
                API_BEKLEME
            )


        # ====================================================
        # ZIP
        # ====================================================

        durum.text(
            "📦 ZIP hazırlanıyor..."
        )


        create_zip(

            ayrilmis_klasor,

            zip_yolu

        )


        with open(
            zip_yolu,
            "rb"
        ) as f:

            st.session_state.zip_data = f.read()


        st.session_state.pdf_pages = None


        # ====================================================
        # SONUÇ
        # ====================================================

        st.success(
            "🎉 İşlem tamamlandı!"
        )


        if kontrol_sayisi:

            st.warning(

                f"{kontrol_sayisi} kişi için isim "
                f"okunamadı. Bu kişiler "
                f"KONTROL_GEREKLI klasörlerine ayrıldı."

            )

        else:

            st.success(
                "Tüm kişi klasörleri isimlendirildi."
            )


# ============================================================
# ZIP İNDİR
# ============================================================

if st.session_state.zip_data:

    st.download_button(

        "📦 Ayrılmış Dosyaları ZIP Olarak İndir",

        data=st.session_state.zip_data,

        file_name="ISG_Ayrilmis_Dosyalar.zip",

        mime="application/zip",

        use_container_width=True

    )
