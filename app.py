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
from difflib import SequenceMatcher

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

st.set_page_config(page_title="İSG Belge Ayrıştırıcı", page_icon="📄", layout="centered")

if "zip_data" not in st.session_state:
    st.session_state.zip_data = None
if "islem_mesaji" not in st.session_state:
    st.session_state.islem_mesaji = ""

st.title("📄 İSG Belge Ayrıştırıcı (Turbo & 300 DPI Kalite)")

# YENİ: Tarayıcının şifreyi hatırlaması için autocomplete eklendi
api_key = st.text_input("Gemini API Anahtarınızı Girin:", type="password", autocomplete="current-password")

st.markdown("### ⚙️ Belge Dizilimi (Blok Ayarları)")
col1, col2 = st.columns(2)
with col1:
    sinav_sayfa = st.number_input("Sınav Sayfa Sayısı:", min_value=0, value=2, step=1)
with col2:
    talimat_sayfa = st.number_input("Talimat Sayfa Sayısı:", min_value=0, value=4, step=1)

blok_boyutu = sinav_sayfa + talimat_sayfa

uploaded_file = st.file_uploader("Lütfen tarama yapılmış PDF dosyasını yükleyin", type="pdf")

if st.button("Ayrıştırmayı Başlat", type="primary"):
    if not api_key:
        st.error("Lütfen bir Gemini API anahtarı girin!")
    elif not uploaded_file:
        st.error("Lütfen işlenecek PDF dosyasını yükleyin!")
    elif blok_boyutu == 0:
        st.error("Toplam sayfa sayısı 0 olamaz. Lütfen Sınav veya Talimat sayfası girin.")
    else:
        st.session_state.zip_data = None
        st.session_state.islem_mesaji = ""
        
        try:
            genai.configure(api_key=api_key)
            
            uygun_model = None
            aktif_modeller = []
            
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    aktif_modeller.append(m.name)
            
            oncelikli_modeller = ['gemini-3.8-flash', 'gemini-3.5-flash', 'gemini-2.5-flash']
            
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

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = os.path.join(temp_dir, "yuklenen_dosya.pdf")
            orijinal_klasor = os.path.join(temp_dir, "Orijinal_Sayfalar")
            ayrilmis_klasor_yolu = os.path.join(temp_dir, "Ayrilmis_Dosyalar")
            zip_yolu = os.path.join(temp_dir, "ISG_Dosyalari.zip")
            
            os.makedirs(orijinal_klasor, exist_ok=True)
            os.makedirs(ayrilmis_klasor_yolu, exist_ok=True)

            with open(pdf_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            st.info("PDF dosyası okunuyor, klasörleme için Yüksek Kalitede (300 DPI) diske kaydediliyor...")
            try:
                sayfa_yollari = convert_from_path(
                    pdf_path, 
                    dpi=300, 
                    output_folder=orijinal_klasor, 
                    fmt="tiff", 
                    paths_only=True
                )
            except Exception as e:
                st.error("PDF parçalanırken hata oluştu! Hata: " + str(e))
                st.stop()
                
            toplam_sayfa = len(sayfa_yollari)
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            bloklar = [sayfa_yollari[i:i + blok_boyutu] for i in range(0, toplam_sayfa, blok_boyutu)]
            
            aktif_kisi = "Bilinmeyen_Kisi"
            aktif_tc = "BilinmeyenTC"
            islenen_sayfa = 0
            basarili_sayisi = 0

            for blok_no, blok_sayfalari in enumerate(bloklar):
                blok_kisi = "Bilinmeyen_Kisi"
                blok_tc = "BilinmeyenTC"
                
                if len(blok_sayfalari) > 1:
                    arama_sirasi = [0, len(blok_sayfalari) - 1] + list(range(1, len(blok_sayfalari) - 1))
                else:
                    arama_sirasi = [0]

                for idx in arama_sirasi:
                    sayfa_yolu = blok_sayfalari[idx]
                    status_text.text(f"Blok {blok_no+1} / {len(bloklar)} taranıyor (Akıllı Arama: Sayfa {idx+1})...")
                    
                    with Image.open(sayfa_yolu) as img:
                        islem_gorseli = img.convert('RGB')
                        # YENİ: Yapay zekaya giden resmi daha da küçülttük (1200 piksel). Kotayı yormaz, aktarımı hızlandırır.
                        islem_gorseli.thumbnail((1200, 1200)) 
                    
                    prompt = """Bu görsel bir İş Sağlığı ve Güvenliği belgesidir.
Lütfen form üzerindeki el yazısı ile yazılmış bilgileri bul:
1. "ADI SOYADI:" başlığının yanındaki el yazısı ismi. Okunmuyorsa "Bilinmeyen_Kisi" yaz.
2. "T.C. KİMLİK NO:" başlığının yanındaki 11 haneli el yazısı rakamı. Okunmuyorsa "BilinmeyenTC" yaz.

Yanıtını sadece aşağıdaki formatta, düz bir JSON olarak ver. Başka hiçbir açıklama ekleme:
{"isim": "Ad Soyad", "tc": "12345678901"}"""
                    
                    max_deneme = 3
                    sayfa_isim = "Bilinmeyen_Kisi"
                    sayfa_tc = "BilinmeyenTC"
                    
                    for deneme in range(max_deneme):
                        try:
                            response = model.generate_content([prompt, islem_gorseli])
                            response_text = response.text.replace("```json", "").replace("```", "").strip()
                            veri = json.loads(response_text)
                            
                            sayfa_isim = dosya_adi_duzenle(veri.get("isim", "Bilinmeyen_Kisi"))
                            sayfa_tc = dosya_adi_duzenle(str(veri.get("tc", "BilinmeyenTC")))
                            break
                        except Exception as e:
                            # YENİ: Şeffaf Hata Gösterimi ve 30 Saniye Dinlenme
                            hata_mesaji = str(e).lower()
                            if "429" in hata_mesaji or "quota" in hata_mesaji or "resource exhausted" in hata_mesaji:
                                status_text.text(f"API kotası doldu, sistemin sıfırlanması için 30 sn bekleniyor... ({deneme+1}/{max_deneme})")
                                time.sleep(30)
                            else:
                                st.error(f"Sayfa işlenirken beklenmeyen hata oluştu: {str(e)}")
                                break
                                
                    islem_gorseli.close()
                    time.sleep(2) 
                    
                    if sayfa_isim != "Bilinmeyen_Kisi" and sayfa_isim != "":
                        blok_kisi = sayfa_isim
                        if sayfa_tc != "BilinmeyenTC" and sayfa_tc != "":
                            blok_tc = sayfa_tc
                        break 
                
                if blok_kisi != "Bilinmeyen_Kisi":
                    if aktif_kisi != "Bilinmeyen_Kisi":
                        isim_benziyor_mu = benzerlik_orani(aktif_kisi, blok_kisi) > 0.70
                        tc_benziyor_mu = benzerlik_orani(aktif_tc, blok_tc) > 0.80
                        
                        if isim_benziyor_mu or tc_benziyor_mu:
                            blok_kisi = aktif_kisi
                            blok_tc = aktif_tc
                        else:
                            aktif_kisi = blok_kisi
                            aktif_tc = blok_tc
                    else:
                        aktif_kisi = blok_kisi
                        aktif_tc = blok_tc

                klasor_adi = f"{blok_kisi}_{blok_tc}"
                hedef_klasor = os.path.join(ayrilmis_klasor_yolu, klasor_adi)
                if not os.path.exists(hedef_klasor):
                    os.makedirs(hedef_klasor)

                for idx, sayfa_yolu in enumerate(blok_sayfalari):
                    islenen_sayfa += 1
                    status_text.text(f"Blok {blok_no+1} dosyalanıyor... Toplam Sayfa: {islenen_sayfa}/{toplam_sayfa}")
                    
                    if idx < sinav_sayfa:
                        dosya_adi = f"Sinav_{idx + 1}.tiff"
                    elif idx < (sinav_sayfa + talimat_sayfa):
                        dosya_adi = f"Talimat_{idx - sinav_sayfa + 1}.tiff"
                    else:
                        dosya_adi = f"Ekstra_Belge_{idx + 1}.tiff"

                    hedef_yol = os.path.join(hedef_klasor, dosya_adi)
                    
                    sayac = 1
                    orijinal_isim = dosya_adi.replace(".tiff", "")
                    while os.path.exists(hedef_yol):
                        dosya_adi = f"{orijinal_isim}_{sayac}.tiff"
                        hedef_yol = os.path.join(hedef_klasor, dosya_adi)
                        sayac += 1
                        
                    shutil.move(sayfa_yolu, hedef_yol)
                    basarili_sayisi += 1
                    progress_bar.progress(islenen_sayfa / toplam_sayfa)

            status_text.text("Klasörler ZIP formatında sıkıştırılıyor...")
            create_zip(ayrilmis_klasor_yolu, zip_yolu)
            
            with open(zip_yolu, "rb") as f:
                st.session_state.zip_data = f.read()
            st.session_state.islem_mesaji = f"{basarili_sayisi} sayfa isme ve TC'ye göre yüksek kalitede (300 DPI) arşivlendi."
            
            status_text.text("İşlem tamamlandı!")

if st.session_state.zip_data is not None:
    st.success(st.session_state.islem_mesaji)
    st.download_button(
        label="📦 Hazırlanan Klasörleri İndir (ZIP)",
        data=st.session_state.zip_data,
        file_name="ISG_Ayrilmis_Dosyalar.zip",
        mime="application/zip",
        type="primary"
    )
