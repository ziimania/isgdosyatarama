
import streamlit as st
import os
import json
import zipfile
import tempfile
import time
import shutil

import google.generativeai as genai

from pdf2image import convert_from_path
from PIL import Image


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================

def dosya_adi_duzenle(isim):
    """
    Windows klasör/dosya adında kullanılamayan karakterleri temizler.
    """
    if isim is None:
        return ""

    isim = str(isim)

    yasakli_karakterler = '<>:"/\\|?*'

    for karakter in yasakli_karakterler:
        isim = isim.replace(karakter, '')

    # Satır sonu / gereksiz boşlukları temizle
    isim = isim.replace("\n", " ")
    isim = isim.replace("\r", " ")

    # Birden fazla boşluğu teke indir
    isim = " ".join(isim.split())

    return isim.strip().title()


def klasor_adi_olustur(isim, tc):
    """
    AI tarafından okunan isim ve TC'den güvenli klasör adı oluşturur.
    """

    isim = dosya_adi_duzenle(isim)
    tc = dosya_adi_duzenle(tc)

    if not isim or len(isim) < 3:
        isim = "Bilinmeyen_Kisi"

    if not tc or tc == "None":
        tc = "BilinmeyenTC"

    return f"{isim}_{tc}"


def benzersiz_klasor_yolu(ana_klasor, klasor_adi):
    """
    Aynı isimde klasör varsa _1, _2 şeklinde benzersiz isim oluşturur.
    """

    temel_yol = os.path.join(
        ana_klasor,
        klasor_adi
    )

    if not os.path.exists(temel_yol):
        return temel_yol

    sayac = 1

    while True:

        yeni_yol = os.path.join(
            ana_klasor,
            f"{klasor_adi}_{sayac}"
        )

        if not os.path.exists(yeni_yol):
            return yeni_yol

        sayac += 1


def create_zip(source_dir, output_zip):
    """
    Ayrıştırılmış klasörleri ZIP dosyasına dönüştürür.
    """

    with zipfile.ZipFile(
        output_zip,
        "w",
        zipfile.ZIP_DEFLATED
    ) as zipf:

        for root, dirs, files in os.walk(source_dir):

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
# STREAMLIT AYARLARI
# ============================================================

st.set_page_config(
    page_title="İSG Belge Ayrıştırıcı",
    page_icon="📄",
    layout="centered"
)


# ============================================================
# SESSION STATE
# ============================================================

if "zip_data" not in st.session_state:
    st.session_state.zip_data = None

if "islem_mesaji" not in st.session_state:
    st.session_state.islem_mesaji = ""


# ============================================================
# BAŞLIK
# ============================================================

st.title("📄 İSG Belge Ayrıştırıcı")

st.write(
    "PDF içerisindeki belgeleri kişi başına belirlenen sayfa sayısına "
    "göre ayırır ve sınav formundaki isim bilgisine göre klasörler."
)


# ============================================================
# API KEY
# ============================================================

api_key = st.text_input(
    "Gemini API Anahtarınızı Girin:",
    type="password",
    autocomplete="current-password"
)


# ============================================================
# BELGE DİZİLİMİ
# ============================================================

st.markdown("### ⚙️ Belge Dizilimi")

col1, col2 = st.columns(2)

with col1:

    sinav_sayfa = st.number_input(
        "Sınav Sayfa Sayısı:",
        min_value=0,
        value=2,
        step=1
    )

with col2:

    talimat_sayfa = st.number_input(
        "Talimat Sayfa Sayısı:",
        min_value=0,
        value=4,
        step=1
    )


# Her kişinin toplam sayfa sayısı
blok_boyutu = sinav_sayfa + talimat_sayfa


st.info(
    f"Her kişi için toplam **{blok_boyutu} sayfa** ayrılacak."
)


# ============================================================
# PDF YÜKLE
# ============================================================

uploaded_file = st.file_uploader(
    "Lütfen tarama yapılmış PDF dosyasını yükleyin",
    type="pdf"
)


# ============================================================
# BAŞLAT
# ============================================================

if st.button(
    "🚀 Ayrıştırmayı Başlat",
    type="primary"
):

    # --------------------------------------------------------
    # KONTROLLER
    # --------------------------------------------------------

    if not api_key:

        st.error(
            "Lütfen Gemini API anahtarınızı girin!"
        )

        st.stop()


    if not uploaded_file:

        st.error(
            "Lütfen işlenecek PDF dosyasını yükleyin!"
        )

        st.stop()


    if blok_boyutu == 0:

        st.error(
            "Toplam sayfa sayısı 0 olamaz."
        )

        st.stop()


    # --------------------------------------------------------
    # SESSION RESET
    # --------------------------------------------------------

    st.session_state.zip_data = None
    st.session_state.islem_mesaji = ""


    # --------------------------------------------------------
    # GEMINI BAĞLANTISI
    # --------------------------------------------------------

    try:

        genai.configure(
            api_key=api_key
        )

        uygun_model = None

        aktif_modeller = []


        # Kullanılabilir modelleri bul
        for model_bilgisi in genai.list_models():

            if (
                "generateContent"
                in model_bilgisi.supported_generation_methods
            ):

                aktif_modeller.append(
                    model_bilgisi.name
                )


        # Öncelikli modeller
        oncelikli_modeller = [

            "gemini-3.8-flash",

            "gemini-3.5-flash",

            "gemini-2.5-flash",

            "gemini-1.5-flash"

        ]


        # Öncelikli model seç
        for oncelik in oncelikli_modeller:

            for model_adi in aktif_modeller:

                if (
                    oncelik in model_adi
                    and "preview" not in model_adi
                    and "lite" not in model_adi
                ):

                    uygun_model = model_adi

                    break

            if uygun_model:
                break


        # Bulamazsa herhangi bir Flash model
        if not uygun_model:

            for model_adi in aktif_modeller:

                if "flash" in model_adi:

                    uygun_model = model_adi

                    break


        if not uygun_model:

            st.error(
                "Görsel işleyebilen Gemini modeli bulunamadı!\n\n"
                f"Mevcut modeller: {aktif_modeller}"
            )

            st.stop()


        model = genai.GenerativeModel(
            uygun_model
        )


        st.success(
            f"Gemini bağlantısı başarılı: {uygun_model}"
        )


    except Exception as e:

        st.error(
            f"API Yapılandırma Hatası: {e}"
        )

        st.stop()


    # ========================================================
    # GEÇİCİ ÇALIŞMA KLASÖRÜ
    # ========================================================

    with tempfile.TemporaryDirectory() as temp_dir:

        pdf_path = os.path.join(
            temp_dir,
            "yuklenen_dosya.pdf"
        )

        orijinal_klasor = os.path.join(
            temp_dir,
            "Orijinal_Sayfalar"
        )

        ayrilmis_klasor_yolu = os.path.join(
            temp_dir,
            "Ayrilmis_Dosyalar"
        )

        zip_yolu = os.path.join(
            temp_dir,
            "ISG_Dosyalari.zip"
        )


        os.makedirs(
            orijinal_klasor,
            exist_ok=True
        )

        os.makedirs(
            ayrilmis_klasor_yolu,
            exist_ok=True
        )


        # ----------------------------------------------------
        # PDF'Yİ KAYDET
        # ----------------------------------------------------

        with open(
            pdf_path,
            "wb"
        ) as f:

            f.write(
                uploaded_file.getbuffer()
            )


        # ----------------------------------------------------
        # PDF -> 300 DPI TIFF
        # ----------------------------------------------------

        st.info(
            "PDF okunuyor ve sayfalar 300 DPI kalitesinde hazırlanıyor..."
        )


        try:

            sayfa_yollari = convert_from_path(

                pdf_path,

                dpi=300,

                output_folder=orijinal_klasor,

                fmt="tiff",

                paths_only=True

            )


        except Exception as e:

            st.error(
                "PDF parçalanamadı: "
                + str(e)
            )

            st.stop()


        toplam_sayfa = len(
            sayfa_yollari
        )


        if toplam_sayfa == 0:

            st.error(
                "PDF içerisinde sayfa bulunamadı."
            )

            st.stop()


        st.success(
            f"PDF içerisinde {toplam_sayfa} sayfa bulundu."
        )


        # ----------------------------------------------------
        # KAÇ KİŞİ / BLOK OLACAĞINI HESAPLA
        # ----------------------------------------------------

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
            f"Toplam {toplam_blok} kişi/dosya bloğu oluşturulacak."
        )


        # ----------------------------------------------------
        # PROGRESS
        # ----------------------------------------------------

        progress_bar = st.progress(0)

        status_text = st.empty()


        islenen_sayfa = 0

        basarili_sayisi = 0

        basarisiz_isim_sayisi = 0


        # ====================================================
        # ANA BLOK DÖNGÜSÜ
        # ====================================================

        for blok_no, blok_sayfalari in enumerate(
            bloklar
        ):


            # ------------------------------------------------
            # BLOK BAŞLANGICI
            # ------------------------------------------------

            status_text.text(

                f"📦 {blok_no + 1}/{toplam_blok} "
                f"numaralı kişi hazırlanıyor..."

            )


            # ------------------------------------------------
            # GEÇİCİ KLASÖR
            #
            # Örneğin:
            #
            # GEÇİCİ_1
            # GEÇİCİ_2
            # GEÇİCİ_3
            #
            # Daha sonra isimle değiştirilecek.
            # ------------------------------------------------

            gecici_klasor = os.path.join(

                ayrilmis_klasor_yolu,

                f"GEÇİCİ_{blok_no + 1}"

            )


            os.makedirs(
                gecici_klasor,
                exist_ok=True
            )


            # ------------------------------------------------
            # BU BLOĞUN SAYFALARINI KLASÖRE KOY
            # ------------------------------------------------

            gecici_sayfalar = []


            for idx, sayfa_yolu in enumerate(
                blok_sayfalari
            ):


                # --------------------------------------------
                # DOSYA İSMİ
                # --------------------------------------------

                if idx < sinav_sayfa:

                    dosya_adi = (
                        f"Sinav_{idx + 1}.tiff"
                    )


                elif idx < (
                    sinav_sayfa + talimat_sayfa
                ):

                    dosya_adi = (

                        f"Talimat_"
                        f"{idx - sinav_sayfa + 1}.tiff"

                    )


                else:

                    dosya_adi = (
                        f"Ekstra_Belge_{idx + 1}.tiff"
                    )


                hedef_yol = os.path.join(

                    gecici_klasor,

                    dosya_adi

                )


                # --------------------------------------------
                # SAYFAYI TAŞI
                # --------------------------------------------

                shutil.move(

                    sayfa_yolu,

                    hedef_yol

                )


                gecici_sayfalar.append(
                    hedef_yol
                )


                islenen_sayfa += 1


                progress_bar.progress(

                    min(
                        islenen_sayfa / toplam_sayfa,
                        1.0
                    )

                )


            # =================================================
            # AI İLE İSİM OKUMA
            # =================================================

            blok_kisi = "Bilinmeyen_Kisi"

            blok_tc = "BilinmeyenTC"


            # ------------------------------------------------
            # İSİM OKUNACAK SAYFA
            #
            # Normal durumda:
            # Sinav_1.tiff
            #
            # Eğer sınav sayfası yoksa ilk sayfa.
            # ------------------------------------------------

            if len(gecici_sayfalar) > 0:

                okunacak_sayfa = (
                    gecici_sayfalar[0]
                )


                status_text.text(

                    f"🔎 {blok_no + 1}/{toplam_blok} "
                    f"kişinin adı okunuyor..."

                )


                try:

                    # ----------------------------------------
                    # GÖRSELİ AÇ
                    # ----------------------------------------

                    with Image.open(
                        okunacak_sayfa
                    ) as img:

                        islem_gorseli = img.convert(
                            "RGB"
                        )


                        # ------------------------------------
                        # AI PROMPT
                        # ------------------------------------

                        prompt = """
Bu görsel bir İş Sağlığı ve Güvenliği eğitim veya sınav formudur.

Formun üst kısmındaki kişisel bilgileri dikkatlice incele.

Özellikle şu alanları bul:

1. "ADI SOYADI:" veya buna benzer alandaki kişinin AD SOYADINI oku.

2. "T.C. KİMLİK NO:" veya "TC KİMLİK NO:" alanındaki
11 haneli T.C. kimlik numarasını oku.

ÖNEMLİ:

- El yazısı olabilir.
- Harfleri dikkatlice ayırt et.
- İsim okunabiliyorsa mümkün olan en doğru şekilde yaz.
- İsim kesinlikle okunamıyorsa "Bilinmeyen_Kisi" yaz.
- TC okunamıyorsa "BilinmeyenTC" yaz.
- Başka açıklama yazma.

SADECE aşağıdaki JSON formatında cevap ver:

{
    "isim": "Ad Soyad",
    "tc": "12345678901"
}
"""


                        # ------------------------------------
                        # GEMINI
                        # ------------------------------------

                        response = model.generate_content(

                            [
                                prompt,
                                islem_gorseli
                            ]

                        )


                        # ------------------------------------
                        # RESPONSE TEMİZLE
                        # ------------------------------------

                        response_text = (
                            response.text
                            .replace(
                                "```json",
                                ""
                            )
                            .replace(
                                "```",
                                ""
                            )
                            .strip()
                        )


                        # ------------------------------------
                        # JSON OKU
                        # ------------------------------------

                        veri = json.loads(
                            response_text
                        )


                        # ------------------------------------
                        # İSİM
                        # ------------------------------------

                        okunan_isim = veri.get(
                            "isim",
                            "Bilinmeyen_Kisi"
                        )


                        blok_kisi = dosya_adi_duzenle(
                            okunan_isim
                        )


                        # ------------------------------------
                        # TC
                        # ------------------------------------

                        okunan_tc = veri.get(
                            "tc",
                            "BilinmeyenTC"
                        )


                        blok_tc = dosya_adi_duzenle(
                            str(okunan_tc)
                        )


                        # ------------------------------------
                        # GEÇERSİZ İSİM KONTROLÜ
                        # ------------------------------------

                        if (

                            not blok_kisi

                            or len(blok_kisi) < 3

                            or blok_kisi.lower()
                            == "bilinmeyen_kisi"

                        ):

                            blok_kisi = "Bilinmeyen_Kisi"

                            basarisiz_isim_sayisi += 1


                except Exception as e:

                    # AI okuyamazsa işlem durmayacak
                    blok_kisi = "Bilinmeyen_Kisi"

                    blok_tc = "BilinmeyenTC"

                    basarisiz_isim_sayisi += 1


            # =================================================
            # KLASÖRÜ İSİMLENDİR
            # =================================================

            yeni_klasor_adi = klasor_adi_olustur(

                blok_kisi,

                blok_tc

            )


            # ------------------------------------------------
            # BENZERSİZ KLASÖR YOLU
            # ------------------------------------------------

            yeni_klasor = benzersiz_klasor_yolu(

                ayrilmis_klasor_yolu,

                yeni_klasor_adi

            )


            # ------------------------------------------------
            # GEÇİCİ KLASÖRÜ İSİMLENDİR
            # ------------------------------------------------

            try:

                os.rename(

                    gecici_klasor,

                    yeni_klasor

                )


            except Exception as e:

                # Rename başarısız olursa mevcut klasörü
                # kaybetmemek için hata göster.

                st.warning(

                    f"{blok_no + 1}. klasör "
                    f"yeniden adlandırılamadı: {e}"

                )


            # ------------------------------------------------
            # DURUM
            # ------------------------------------------------

            status_text.text(

                f"✅ {blok_no + 1}/{toplam_blok} "
                f"→ {blok_kisi} "
                f"({len(blok_sayfalari)} sayfa)"

            )


            basarili_sayisi += len(
                blok_sayfalari
            )


            # Gemini API kotasını zorlamamak için
            # küçük bekleme
            time.sleep(1)


        # ====================================================
        # ZIP OLUŞTUR
        # ====================================================

        status_text.text(
            "📦 Klasörler ZIP dosyasına dönüştürülüyor..."
        )


        try:

            create_zip(

                ayrilmis_klasor_yolu,

                zip_yolu

            )


        except Exception as e:

            st.error(
                f"ZIP oluşturulamadı: {e}"
            )

            st.stop()


        # ----------------------------------------------------
        # ZIP'İ MEMORY'YE AL
        # ----------------------------------------------------

        with open(
            zip_yolu,
            "rb"
        ) as f:

            st.session_state.zip_data = f.read()


        # ----------------------------------------------------
        # SONUÇ MESAJI
        # ----------------------------------------------------

        st.session_state.islem_mesaji = (

            f"{basarili_sayisi} sayfa başarıyla ayrıştırıldı. "
            f"{toplam_blok} kişi klasörü oluşturuldu."

        )


        if basarisiz_isim_sayisi > 0:

            st.session_state.islem_mesaji += (

                f" {basarisiz_isim_sayisi} klasörde isim "
                f"AI tarafından okunamadığı için "
                f"'Bilinmeyen_Kisi' kullanıldı."

            )


        status_text.text(
            "🎉 İşlem tamamlandı!"
        )


        progress_bar.progress(1.0)


# ============================================================
# ZIP İNDİRME
# ============================================================

if st.session_state.zip_data is not None:

    st.success(
        st.session_state.islem_mesaji
    )


    st.download_button(

        label="📦 Hazırlanan Klasörleri İndir (ZIP)",

        data=st.session_state.zip_data,

        file_name="ISG_Ayrilmis_Dosyalar.zip",

        mime="application/zip",

        type="primary"

    )
