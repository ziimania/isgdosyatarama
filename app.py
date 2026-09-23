import streamlit as st
import os
import json
import zipfile
import tempfile
import time
import shutil
import re

import cv2
import numpy as np

import google.generativeai as genai

from pdf2image import convert_from_path
from PIL import Image, ImageEnhance, ImageFilter


# ============================================================
# GENEL AYARLAR
# ============================================================

DPI = 300

# İsim crop'unu Gemini'ye göndermeden önce büyütme
CROP_SCALE = 2.5

# Gemini'den sonra kaç saniye bekleyelim
API_BEKLEME = 1


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================

def dosya_adi_duzenle(isim):
    """
    Klasör adında kullanılamayan karakterleri temizler.
    """

    if isim is None:
        return ""

    isim = str(isim)

    yasakli = '<>:"/\\|?*'

    for karakter in yasakli:
        isim = isim.replace(karakter, "")

    isim = isim.replace("\n", " ")
    isim = isim.replace("\r", " ")

    isim = " ".join(isim.split())

    return isim.strip().title()


def isim_normalize(isim):
    """
    İki Gemini sonucunu karşılaştırabilmek için normalize eder.
    """

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

    isim = " ".join(
        isim.split()
    )

    return isim


def benzerlik_orani(a, b):
    """
    İki isim arasındaki benzerlik oranı.
    """

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


def create_zip(source_dir, output_zip):
    """
    Klasörleri ZIP yapar.
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


def benzersiz_klasor_yolu(
    ana_klasor,
    klasor_adi
):
    """
    Aynı isimde klasör varsa _2, _3 şeklinde devam eder.
    """

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


# ============================================================
# GÖRÜNTÜ İŞLEME
# ============================================================

def goruntu_iyilestir(
    image,
    scale=CROP_SCALE
):
    """
    Gemini'ye gönderilecek isim bölgesini büyütür,
    kontrastını artırır ve keskinleştirir.
    """

    # RGB olduğundan emin ol
    if image.mode != "RGB":
        image = image.convert("RGB")

    # Büyüt
    yeni_w = int(
        image.width * scale
    )

    yeni_h = int(
        image.height * scale
    )

    image = image.resize(
        (yeni_w, yeni_h),
        Image.Resampling.LANCZOS
    )

    # Kontrast
    image = ImageEnhance.Contrast(
        image
    ).enhance(1.5)

    # Keskinlik
    image = ImageEnhance.Sharpness(
        image
    ).enhance(1.8)

    # Hafif detay artırma
    image = image.filter(
        ImageFilter.SHARPEN
    )

    return image


# ============================================================
# OPEN CV İLE KATILIMCI TABLOSU / İSİM ALANI BULMA
# ============================================================

def isim_alani_bul(
    image_path
):
    """
    Sayfanın üst/orta kısmındaki katılımcı tablosunu
    yatay çizgilerden tespit etmeye çalışır.

    Amaç:
        ADI SOYADI
        T.C. KİMLİK NO
        GÖREVİ

    bölümünü bulmak.

    Başarısız olursa güvenli bir yedek bölge kullanır.
    """

    try:

        img = cv2.imread(
            image_path,
            cv2.IMREAD_GRAYSCALE
        )

        if img is None:
            return None


        h, w = img.shape


        # ----------------------------------------------------
        # Sayfanın yaklaşık %35-%70 arasını incele
        # ----------------------------------------------------

        y_baslangic = int(
            h * 0.35
        )

        y_bitis = int(
            h * 0.70
        )

        roi = img[
            y_baslangic:y_bitis,
            :
        ]


        # ----------------------------------------------------
        # Adaptive threshold
        # ----------------------------------------------------

        binary = cv2.adaptiveThreshold(

            roi,

            255,

            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,

            cv2.THRESH_BINARY_INV,

            31,

            12

        )


        # ----------------------------------------------------
        # Yatay çizgileri bul
        # ----------------------------------------------------

        yatay_kernel = cv2.getStructuringElement(

            cv2.MORPH_RECT,

            (
                max(100, int(w * 0.08)),
                1
            )

        )


        yatay = cv2.morphologyEx(

            binary,

            cv2.MORPH_OPEN,

            yatay_kernel

        )


        # ----------------------------------------------------
        # Her satırdaki yatay çizgi miktarı
        # ----------------------------------------------------

        satir_piksel = (
            yatay > 0
        ).sum(
            axis=1
        )


        # Minimum çizgi uzunluğu
        min_cizgi = int(
            w * 0.25
        )


        adaylar = np.where(
            satir_piksel >= min_cizgi
        )[0]


        # ----------------------------------------------------
        # Ardışık satırları grupla
        # ----------------------------------------------------

        gruplar = []

        for y in adaylar:

            if (
                not gruplar
                or
                y > gruplar[-1][-1] + 2
            ):

                gruplar.append(
                    [y]
                )

            else:

                gruplar[-1].append(
                    y
                )


        cizgi_yerleri = []

        for grup in gruplar:

            merkez = int(
                np.mean(grup)
            )

            cizgi_yerleri.append(
                merkez
            )


        # ----------------------------------------------------
        # Katılımcı tablosunu bul
        #
        # İki yatay çizgi arasında makul mesafe arıyoruz.
        # ----------------------------------------------------

        secilen_ust = None
        secilen_alt = None

        for i in range(
            len(cizgi_yerleri) - 1
        ):

            ust = cizgi_yerleri[i]
            alt = cizgi_yerleri[i + 1]

            mesafe = alt - ust

            # 150-650 px arası alanları değerlendir
            if 150 <= mesafe <= 650:

                secilen_ust = ust
                secilen_alt = alt

                # İlk uygun tablo
                break


        # ----------------------------------------------------
        # BULUNDUYSA
        # ----------------------------------------------------

        if (
            secilen_ust is not None
            and
            secilen_alt is not None
        ):

            gercek_ust = (
                y_baslangic
                + secilen_ust
            )

            gercek_alt = (
                y_baslangic
                + secilen_alt
            )

            # Katılımcı tablosunun
            # üst kısmını ve sol bölümünü al.
            #
            # Sağ taraftaki imza alanını almıyoruz.

            x1 = int(
                w * 0.03
            )

            x2 = int(
                w * 0.78
            )

            # Üst çizgiden biraz aşağı
            crop_y1 = (
                gercek_ust + 10
            )

            # İsmin bulunduğu ilk satırı
            # kapsayacak şekilde
            crop_y2 = min(
                gercek_ust + 280,
                gercek_alt
            )

            if crop_y2 > crop_y1:

                return (
                    x1,
                    crop_y1,
                    x2,
                    crop_y2,
                    "OpenCV"
                )


        # ====================================================
        # YEDEK CROP
        # ====================================================

        # Form yapısı değişirse veya çizgi tespit edilemezse
        # yaklaşık isim bölgesini kullan.
        #
        # Bu bölüm senin mevcut formunun yapısına göre
        # güvenli bırakılmıştır.

        x1 = int(
            w * 0.04
        )

        x2 = int(
            w * 0.78
        )

        y1 = int(
            h * 0.48
        )

        y2 = int(
            h * 0.61
        )

        return (
            x1,
            y1,
            x2,
            y2,
            "Yedek"
        )


    except Exception:

        return None


# ============================================================
# CROP OLUŞTUR
# ============================================================

def isim_crop_olustur(
    image_path
):
    """
    OpenCV'nin bulduğu bölgeyi PIL görüntüsü olarak döndürür.
    """

    sonuc = isim_alani_bul(
        image_path
    )

    if sonuc is None:
        return None

    x1, y1, x2, y2, kaynak = sonuc

    try:

        with Image.open(
            image_path
        ) as img:

            img = img.convert(
                "RGB"
            )

            # Koordinatları sınırla
            x1 = max(
                0,
                min(x1, img.width)
            )

            x2 = max(
                0,
                min(x2, img.width)
            )

            y1 = max(
                0,
                min(y1, img.height)
            )

            y2 = max(
                0,
                min(y2, img.height)
            )

            if x2 <= x1 or y2 <= y1:
                return None

            crop = img.crop(
                (
                    x1,
                    y1,
                    x2,
                    y2
                )
            )

            crop = goruntu_iyilestir(
                crop
            )

            return crop, kaynak


    except Exception:

        return None


# ============================================================
# GEMINI İLE İSİM OKUMA
# ============================================================

def gemini_ile_isim_oku(
    model,
    crop_list
):
    """
    Bir veya iki sınav yüzünü tek Gemini isteğinde okur.
    """

    if not crop_list:

        return {
            "isim1": "",
            "isim2": "",
            "final_isim": "",
            "guven": "dusuk"
        }


    prompt = """
Bu görseller İş Sağlığı ve Güvenliği sınav formundaki
KATILIMCI / ADI SOYADI bölümünün kırpılmış görüntüleridir.

Görseller aynı kişiye ait sınavın farklı yüzleri olabilir.

Görevin SADECE el yazısı ile yazılmış kişinin ADI SOYADINI okumaktır.

Çok dikkatli ol.

Önemli kurallar:

1. "ADI SOYADI:" etiketinin kendisini isim olarak alma.
2. "T.C. KİMLİK NO" bilgisini isim olarak alma.
3. "GÖREVİ" bilgisini isim olarak alma.
4. İmza alanını isim olarak alma.
5. El yazısındaki harfleri mümkün olduğunca doğru çöz.
6. Aynı kişinin iki görseli varsa ikisini birlikte değerlendir.
7. Bir görselde harf okunmuyorsa diğer görseli referans al.
8. İki sonuç arasında küçük yazım farkı varsa aynı kişiyi ifade eden en mantıklı sonucu seç.
9. Tahmin yaparken mevcut harf şekillerine dayan.
10. İsim kesinlikle okunamıyorsa "Bilinmeyen_Kisi" yaz.

SADECE aşağıdaki JSON formatında cevap ver:

{
    "isim1": "Birinci görselde okunan isim",
    "isim2": "İkinci görselde okunan isim",
    "final_isim": "En güvenilir Ad Soyad",
    "guven": "yuksek"
}

Eğer sadece bir görsel varsa:

{
    "isim1": "Okunan Ad Soyad",
    "isim2": "",
    "final_isim": "Okunan Ad Soyad",
    "guven": "yuksek"
}

İsim okunamıyorsa:

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

        # Görselleri ekle
        for crop in crop_list:

            icerik.append(
                crop
            )


        response = model.generate_content(
            icerik
        )


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


        veri = json.loads(
            response_text
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
        # Final isim geçersizse iki sonucu kendimiz karşılaştır
        # ----------------------------------------------------

        if (
            not final_isim
            or
            final_isim.lower()
            == "bilinmeyen_kisi"
        ):

            if (
                isim1
                and
                isim2
            ):

                benzerlik = (
                    benzerlik_orani(
                        isim1,
                        isim2
                    )
                )

                if benzerlik >= 0.70:

                    # Birinci sonucu kullan
                    final_isim = isim1

                    guven = "orta"

            elif isim1:

                final_isim = isim1

            elif isim2:

                final_isim = isim2


        # ----------------------------------------------------
        # Çok kısa isimleri reddet
        # ----------------------------------------------------

        if (
            not final_isim
            or
            len(final_isim) < 4
            or
            final_isim.lower()
            == "bilinmeyen_kisi"
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

st.title(
    "📄 İSG Belge Ayrıştırıcı"
)

st.write(
    "Belgeleri kişi başına belirlenen sayıda böler, "
    "sınav formundaki isim alanını otomatik bulur ve "
    "Gemini ile el yazısı adı okur."
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
# SAYFA AYARLARI
# ============================================================

st.markdown(
    "### ⚙️ Belge Dizilimi"
)


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


blok_boyutu = (
    sinav_sayfa
    +
    talimat_sayfa
)


st.info(

    f"Her kişi için toplam **{blok_boyutu} sayfa** ayrılacak."

)


# ============================================================
# PDF
# ============================================================

uploaded_file = st.file_uploader(

    "Lütfen taranmış PDF dosyasını yükleyin",

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
            "Lütfen Gemini API anahtarınızı girin."
        )

        st.stop()


    if not uploaded_file:

        st.error(
            "Lütfen PDF dosyasını yükleyin."
        )

        st.stop()


    if blok_boyutu == 0:

        st.error(
            "Toplam sayfa sayısı 0 olamaz."
        )

        st.stop()


    # --------------------------------------------------------
    # RESET
    # --------------------------------------------------------

    st.session_state.zip_data = None

    st.session_state.islem_mesaji = ""


    # ========================================================
    # GEMINI BAĞLANTISI
    # ========================================================

    try:

        genai.configure(
            api_key=api_key
        )


        aktif_modeller = []


        for m in genai.list_models():

            if (
                "generateContent"
                in m.supported_generation_methods
            ):

                aktif_modeller.append(
                    m.name
                )


        uygun_model = None


        oncelikli_modeller = [

            "gemini-3.8-flash",

            "gemini-3.5-flash",

            "gemini-2.5-flash",

            "gemini-1.5-flash"

        ]


        for oncelik in oncelikli_modeller:

            for ad in aktif_modeller:

                if (

                    oncelik in ad

                    and
                    "preview" not in ad

                    and
                    "lite" not in ad

                ):

                    uygun_model = ad

                    break


            if uygun_model:
                break


        if not uygun_model:

            for ad in aktif_modeller:

                if "flash" in ad:

                    uygun_model = ad

                    break


        if not uygun_model:

            st.error(

                "Görsel işleyebilen Gemini modeli bulunamadı.\n\n"

                + str(aktif_modeller)

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
            f"Gemini bağlantı hatası: {e}"
        )

        st.stop()


    # ========================================================
    # GEÇİCİ KLASÖR
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


        ayrilmis_klasor = os.path.join(

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

            ayrilmis_klasor,

            exist_ok=True

        )


        # ----------------------------------------------------
        # PDF KAYDET
        # ----------------------------------------------------

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
            "PDF 300 DPI olarak sayfalara ayrılıyor..."
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
                f"PDF parçalanamadı: {e}"
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
        # BLOKLAR
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

            f"{toplam_sayfa} sayfa bulundu. "

            f"{toplam_blok} kişi bloğu oluşturulacak."

        )


        progress = st.progress(
            0
        )

        status = st.empty()


        islenen_sayfa = 0

        basarili = 0

        kontrol_sayisi = 0


        # ====================================================
        # BLOK DÖNGÜSÜ
        # ====================================================

        for blok_no, blok_sayfalari in enumerate(
            bloklar
        ):


            status.text(

                f"📦 {blok_no + 1}/{toplam_blok} "
                f"hazırlanıyor..."

            )


            # ------------------------------------------------
            # GEÇİCİ KLASÖR
            # ------------------------------------------------

            gecici_klasor = os.path.join(

                ayrilmis_klasor,

                f"GEÇİCİ_{blok_no + 1}"

            )


            os.makedirs(

                gecici_klasor,

                exist_ok=True

            )


            gecici_sayfalar = []


            # =================================================
            # SAYFALARI BLOĞA KOY
            # =================================================

            for idx, sayfa_yolu in enumerate(
                blok_sayfalari
            ):


                if idx < sinav_sayfa:

                    dosya_adi = (
                        f"Sinav_{idx + 1}.tiff"
                    )


                elif idx < (
                    sinav_sayfa
                    +
                    talimat_sayfa
                ):

                    dosya_adi = (

                        f"Talimat_"
                        f"{idx - sinav_sayfa + 1}.tiff"

                    )


                else:

                    dosya_adi = (

                        f"Ekstra_Belge_"
                        f"{idx + 1}.tiff"

                    )


                hedef = os.path.join(

                    gecici_klasor,

                    dosya_adi

                )


                shutil.move(

                    sayfa_yolu,

                    hedef

                )


                gecici_sayfalar.append(
                    hedef
                )


                islenen_sayfa += 1


                progress.progress(

                    min(

                        islenen_sayfa
                        /
                        toplam_sayfa,

                        1.0

                    )

                )


            # =================================================
            # SINAV SAYFALARINI BUL
            # =================================================

            sinav_sayfalari = []

            for sayfa in gecici_sayfalar:

                if os.path.basename(
                    sayfa
                ).startswith("Sinav_"):

                    sinav_sayfalari.append(
                        sayfa
                    )


            # =================================================
            # İSİM CROPLARINI OLUŞTUR
            # =================================================

            crop_list = []

            crop_kaynaklari = []


            for sinav_sayfa_yolu in sinav_sayfalari:


                crop_sonuc = isim_crop_olustur(

                    sinav_sayfa_yolu

                )


                if crop_sonuc is not None:

                    crop, kaynak = crop_sonuc

                    crop_list.append(
                        crop
                    )

                    crop_kaynaklari.append(
                        kaynak
                    )


            # =================================================
            # GEMINI'YE GÖNDER
            # =================================================

            status.text(

                f"🔎 {blok_no + 1}/{toplam_blok} "
                f"numaralı kişinin adı okunuyor..."

            )


            sonuc = gemini_ile_isim_oku(

                model,

                crop_list

            )


            isim1 = sonuc.get(
                "isim1",
                ""
            )

            isim2 = sonuc.get(
                "isim2",
                ""
            )

            final_isim = sonuc.get(
                "final_isim",
                "Bilinmeyen_Kisi"
            )

            guven = sonuc.get(
                "guven",
                "dusuk"
            )


            # =================================================
            # İSİM KONTROLÜ
            # =================================================

            if (

                final_isim
                ==
                "Bilinmeyen_Kisi"

                or
                len(final_isim) < 4

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


            # =================================================
            # KLASÖRÜN YENİ ADI
            # =================================================

            hedef_klasor = benzersiz_klasor_yolu(

                ayrilmis_klasor,

                klasor_adi

            )


            # ------------------------------------------------
            # GEÇİCİ KLASÖRÜ YENİ ADLA DEĞİŞTİR
            # ------------------------------------------------

            try:

                os.rename(

                    gecici_klasor,

                    hedef_klasor

                )

            except Exception as e:

                st.warning(

                    f"Klasör adı değiştirilemedi: {e}"

                )


            # =================================================
            # DEBUG BİLGİSİ
            # =================================================

            if final_isim != "Bilinmeyen_Kisi":

                status.text(

                    f"✅ {blok_no + 1}/{toplam_blok} → "
                    f"{final_isim}"

                )

            else:

                status.text(

                    f"⚠️ {blok_no + 1}/{toplam_blok} → "
                    f"İsim okunamadı"

                )


            basarili += len(
                blok_sayfalari
            )


            # ------------------------------------------------
            # API BEKLEME
            # ------------------------------------------------

            time.sleep(
                API_BEKLEME
            )


        # ====================================================
        # ZIP
        # ====================================================

        status.text(
            "📦 Klasörler ZIP dosyasına dönüştürülüyor..."
        )


        try:

            create_zip(

                ayrilmis_klasor,

                zip_yolu

            )


        except Exception as e:

            st.error(
                f"ZIP oluşturulamadı: {e}"
            )

            st.stop()


        # ====================================================
        # ZIP MEMORY
        # ====================================================

        with open(

            zip_yolu,

            "rb"

        ) as f:

            st.session_state.zip_data = f.read()


        # ====================================================
        # SONUÇ
        # ====================================================

        st.session_state.islem_mesaji = (

            f"✅ {basarili} sayfa işlendi. "
            f"{toplam_blok} kişi klasörü oluşturuldu."

        )


        if kontrol_sayisi > 0:

            st.session_state.islem_mesaji += (

                f" ⚠️ {kontrol_sayisi} kişi "
                f"manuel kontrol için ayrıldı."

            )


        progress.progress(
            1.0
        )

        status.text(
            "🎉 İşlem tamamlandı!"
        )


# ============================================================
# İNDİR
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
